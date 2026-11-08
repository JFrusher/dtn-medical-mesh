"""Run a scenario across seeds and variants in parallel processes (spec §12.3).

    uv run python -m simulation.batch arusha --seeds 5
    uv run python -m simulation.batch --sweep simulation/sweeps/arusha.yaml

Writes messages.csv (one row per message sent) and runs.csv (one row per run) to --out.
A variant changes the scenario before it runs:
    drop: [node ids]           remove nodes (vehicles, relays)
    set: {radio.<name>: value} override a radio assumption
    parity: {PRIORITY: m}      parity shards per priority class
    volume: k                  send every message k times (copies 5 min apart)
"""

import argparse
import csv
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from node.bundle import Priority
from simulation.metrics import evaluate
from simulation.runner import run
from simulation.scenario import Scenario, load_scenario
from simulation.virtual_hw import SIM_EPOCH

VOLUME_SPACING_S = 300.0
MESSAGE_FIELDS = [
    "variant",
    "seed",
    "sender",
    "recipient",
    "priority",
    "sent_s",
    "status",
    "latency_s",
]
RUN_FIELDS = [
    "variant",
    "seed",
    "sent",
    "delivered",
    "violations",
    "wifi_bytes",
    "delivered_bytes",
    "peak_storage_mule",
    "peak_storage_relay",
    "busy_pct_max",
    "min_battery_pct",
    "autonomy_days_min",
    "flash_life_years_min",
]


@dataclass(frozen=True)
class Job:
    scenario: str
    variant: str
    changes: dict[str, Any]
    seed: int


def apply(s: Scenario, changes: dict[str, Any]) -> Scenario:
    unknown = set(changes) - {"drop", "set", "parity", "volume"}
    if unknown:
        raise ValueError(f"unknown variant keys {sorted(unknown)}")
    drop = set(changes.get("drop", []))
    ids = {n.config.node_id for n in s.nodes}
    if drop - ids:
        raise ValueError(f"cannot drop unknown nodes {sorted(drop - ids)}")
    if drop & {e.node for e in s.endpoints}:
        raise ValueError("cannot drop a node that has an endpoint")
    radio = {k.removeprefix("radio."): v for k, v in changes.get("set", {}).items()}
    if any(not k.startswith("radio.") for k in changes.get("set", {})):
        raise ValueError("variants can only set radio.* assumptions")
    k = int(changes.get("volume", 1))
    if k < 1:
        raise ValueError("volume must be a whole number >= 1")
    messages = tuple(
        replace(
            m, at_s=m.at_s + i * VOLUME_SPACING_S, text=f"{m.text} (copy {i + 1})" if i else m.text
        )
        for m in s.messages
        for i in range(k)
    )
    return replace(
        s,
        nodes=tuple(n for n in s.nodes if n.config.node_id not in drop),
        radio=replace(s.radio, **radio),
        overrides={**s.overrides, **{f"radio.{n}": v for n, v in radio.items()}},
        parity={**s.parity, **{Priority[p]: int(m) for p, m in changes.get("parity", {}).items()}},
        messages=tuple(sorted(messages, key=lambda m: m.at_s)),
    )


def run_job(job: Job) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scenario = apply(load_scenario(job.scenario), job.changes)
    with tempfile.TemporaryDirectory() as tmp:
        result = run(scenario, job.seed, Path(tmp))
        report = evaluate(result, job.seed)
        now_ms = int((SIM_EPOCH + result.world.scheduler.now) * 1000)
        opened = {r.bundle_id: r.at_s for e in result.endpoints.values() for r in e.inbox}
        rows, delivered_bytes = [], 0
        for name, e in result.endpoints.items():
            for bid, out in e.sent.items():
                content, prio, sent_at = e.sent_content[bid]
                status = out.status(now_ms).name
                delivered = status == "DELIVERED"
                delivered_bytes += len(content) if delivered else 0
                rows.append(
                    {
                        "variant": job.variant,
                        "seed": job.seed,
                        "sender": name,
                        "recipient": out.recipient,
                        "priority": prio.name,
                        "sent_s": round(sent_at, 1),
                        "status": status,
                        "latency_s": round(opened[bid] - sent_at, 1) if delivered else "",
                    }
                )
        roles = {n.config.node_id: n.config.role.value for n in scenario.nodes}

        def peak(role: str) -> int:
            return max(
                (b for n, b in report.peak_storage_bytes.items() if roles[n] == role), default=0
            )

        run_row = {
            "variant": job.variant,
            "seed": job.seed,
            "sent": report.sent,
            "delivered": report.by_status.get("DELIVERED", 0),
            "violations": len(report.violations),
            "wifi_bytes": report.wifi_bytes,
            "delivered_bytes": delivered_bytes,
            "peak_storage_mule": peak("mule"),
            "peak_storage_relay": peak("relay"),
            "busy_pct_max": round(max(report.busy_pct.values(), default=0), 3),
            # blank when the scenario has no battery nodes
            "min_battery_pct": _least(report.power, "min_pct", 1),
            "autonomy_days_min": _least(report.power, "autonomy_days", 2),
            "flash_life_years_min": _least(report.flash, "life_years", 0),
        }
    return rows, run_row


def _least(per_node: dict[str, dict[str, float]], key: str, digits: int) -> float | str:
    return round(min(v[key] for v in per_node.values()), digits) if per_node else ""


def jobs_for(scenario: str, variants: dict[str, dict[str, Any]], seeds: int) -> list[Job]:
    base = load_scenario(scenario)
    for changes in variants.values():
        apply(base, changes)  # fail fast on a bad variant before starting any process
    return [Job(scenario, v, c, seed) for v, c in variants.items() for seed in range(1, seeds + 1)]


def run_all(jobs: list[Job], out: Path, workers: int | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        results = list(pool.map(run_job, jobs))
    with (out / "messages.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, MESSAGE_FIELDS)
        w.writeheader()
        for rows, _ in results:
            w.writerows(rows)
    with (out / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, RUN_FIELDS)
        w.writeheader()
        w.writerows(r for _, r in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("scenario", nargs="?")
    parser.add_argument("--sweep", type=Path, help="YAML: scenario, seeds, variants")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args(argv)
    if args.sweep:
        spec = yaml.safe_load(args.sweep.read_text())
        scenario, seeds, variants = spec["scenario"], int(spec["seeds"]), spec["variants"]
        name = args.sweep.stem
    elif args.scenario:
        scenario, seeds, variants, name = args.scenario, args.seeds, {"baseline": {}}, args.scenario
    else:
        parser.error("give a scenario or --sweep")
    out = args.out or Path("out/batch") / name
    jobs = jobs_for(scenario, variants, seeds)
    print(f"{len(jobs)} runs ({len(variants)} variants x {seeds} seeds) -> {out}")
    run_all(jobs, out, args.workers)
    bad = 0
    with (out / "runs.csv").open() as f:
        bad = sum(int(r["violations"]) for r in csv.DictReader(f))
    print(f"done; invariant violations: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
