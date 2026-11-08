"""End-of-run invariants and metrics (spec §12.4, §15).

"No silent loss" (REQ-SAFE-01), checked per message sent:
  - its status is DELIVERED, NOT_DELIVERED (flagged at the origin) or IN_TRANSIT within TTL;
  - DELIVERED means the recipient really opened it, with the content sent, exactly once;
  - IN_TRANSIT means it is still recoverable: the origin still holds at least k shards;
and every node's custody ledger hash chain verifies.
"""

import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cbor2

from endpoint.state import Status
from node.bundle import decode_receipt, decode_shard
from node.core import T_LEDGER, T_SHARDS
from simulation.assumptions import ASSUMPTIONS
from simulation.compute import endurance_bytes
from simulation.virtual_hw import SIM_EPOCH

if TYPE_CHECKING:
    from simulation.runner import RunResult
    from simulation.world import World


@dataclass
class Report:
    scenario: str
    seed: int
    violations: list[str]
    sent: int
    by_status: dict[str, int]
    by_priority: dict[str, dict[str, float]]
    late_deliveries: int  # opened after the origin had flagged NOT DELIVERED
    peak_storage_bytes: dict[str, int]
    wifi_bytes: int
    lost_frames: int
    lora_over_budget: int
    drops: dict[str, int] = field(default_factory=dict)
    busy_pct: dict[str, float] = field(default_factory=dict)  # CPU and storage time, % of run
    # node -> flash bytes/day, peak write rate (bytes/s over a minute), projected life (years)
    flash: dict[str, dict[str, float]] = field(default_factory=dict)
    # "group.name" -> value, unit, source ("scenario" when the scenario overrode it)
    assumptions: dict[str, dict[str, object]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        lines = [
            f"# Run report: {self.scenario} (seed {self.seed})",
            "",
            f"**Invariant violations: {len(self.violations)}**"
            + (" (no silent loss)" if not self.violations else ""),
            *[f"- {v}" for v in self.violations],
            "",
            f"Messages sent: {self.sent}. "
            + ", ".join(f"{k}: {v}" for k, v in sorted(self.by_status.items())),
            f"Late deliveries (opened after NOT DELIVERED was flagged): {self.late_deliveries}",
            "",
            "| Priority | Sent | Delivered % | p50 latency (min) | p90 latency (min) | Max (min) |",
            "|---|---|---|---|---|---|",
        ]
        for prio, m in sorted(self.by_priority.items()):
            lines.append(
                f"| {prio} | {m['sent']:.0f} | {m['delivered_pct']:.1f} | {m['p50_min']:.1f} "
                f"| {m['p90_min']:.1f} | {m['max_min']:.1f} |"
            )
        lines += [
            "",
            f"Wi-Fi bytes sent: {self.wifi_bytes:,}. Frames lost on air: {self.lost_frames}. "
            f"LoRa frames over duty budget: {self.lora_over_budget}.",
            "",
            "Drops at nodes: "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.drops.items())) or "none"),
            "",
            "Peak storage per node (bytes): "
            + ", ".join(f"{k} {v:,}" for k, v in sorted(self.peak_storage_bytes.items())),
            "",
            "Busy (CPU and storage, % of run): "
            + ", ".join(f"{k} {v:.2f}" for k, v in sorted(self.busy_pct.items())),
            "",
            "Flash wear counts message storage only; operating-system and log writes are extra.",
            "",
            "| Node | Flash written per day (MB) | Peak write rate (kB/s) "
            "| Projected card life (years) |",
            "|---|---|---|---|",
            *[
                f"| {k} | {f['mb_per_day']:.1f} | {f['peak_kb_per_s']:.1f} "
                f"| {f['life_years']:,.0f} |"
                for k, f in sorted(self.flash.items())
            ],
            "",
            "## Assumptions used",
            "",
            "| Parameter | Value | Unit | Source |",
            "|---|---|---|---|",
            *[
                f"| {k} | {a['value']} | {a['unit']} | {a['source']} |"
                for k, a in sorted(self.assumptions.items())
            ],
        ]
        return "\n".join(lines) + "\n"


def evaluate(result: "RunResult", seed: int) -> Report:
    world, endpoints = result.world, result.endpoints
    now_ms = int((SIM_EPOCH + world.scheduler.now) * 1000)
    violations: list[str] = []
    opened: dict[bytes, list[tuple[str, bytes, float]]] = {}
    for name, e in endpoints.items():
        for r in e.inbox:
            opened.setdefault(r.bundle_id, []).append((name, r.content, r.at_s))

    statuses: Counter[str] = Counter()
    per_prio: dict[str, dict[str, list[float]]] = {}
    late = 0
    for name, e in endpoints.items():
        for bid, out in e.sent.items():
            content, prio, sent_at = e.sent_content[bid]
            status = out.status(now_ms)
            statuses[status.name] += 1
            got = opened.get(bid, [])
            bucket = per_prio.setdefault(prio.name, {"sent": [], "latency": []})
            bucket["sent"].append(1)
            tag = f"{name}->{out.recipient} {bid.hex()[:8]}"
            if status is Status.DELIVERED:
                if [(who, c) for who, c, _ in got] != [(out.recipient, content)]:
                    violations.append(f"{tag}: ACKed but recipient inbox shows {len(got)} copies")
                else:
                    bucket["latency"].append(got[0][2] - sent_at)
            elif status is Status.NOT_DELIVERED and got:
                late += 1
            elif status is Status.IN_TRANSIT and not _recoverable(result, name, bid):
                violations.append(f"{tag}: in transit but no longer recoverable at the origin")
            if len(got) > 1:
                violations.append(f"{tag}: opened {len(got)} times")

    for node_id, node in world.nodes.items():
        if node.up and not _ledger_ok(node.storage.load().get(T_LEDGER, {})):
            violations.append(f"{node_id}: custody ledger hash chain broken")

    drops: Counter[str] = Counter()
    for node in world.nodes.values():
        if node.up and node.core_class.__name__ == "NodeCore":  # honest nodes only
            drops.update(node.core.dropped)
    return Report(
        scenario=result.scenario.name,
        seed=seed,
        violations=violations,
        sent=sum(statuses.values()),
        by_status=dict(statuses),
        by_priority={p: _stats(v) for p, v in per_prio.items()},
        late_deliveries=late,
        peak_storage_bytes=dict(world.peak_bytes),
        wifi_bytes=world.wifi_bytes,
        lost_frames=world.lost_frames,
        lora_over_budget=world.lora_over_budget,
        drops=dict(drops),
        busy_pct={n: 100 * b / world.scheduler.now for n, b in world.busy_s.items()},
        flash=_flash(world),
        assumptions=_assumptions(result.scenario.overrides),
    )


def _flash(world: "World") -> dict[str, dict[str, float]]:
    days = world.scheduler.now / 86400
    out = {}
    for node_id, written in world.flash_written.items():
        per_day = written / days
        role = world.nodes[node_id].config.role
        out[node_id] = {
            "mb_per_day": per_day / 1e6,
            "peak_kb_per_s": max(world.flash_per_min[node_id].values()) / 60 / 1e3,
            "life_years": endurance_bytes(role) / per_day / 365,
        }
    return out


def _assumptions(overrides: dict[str, float]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for key, a in ASSUMPTIONS.items():
        scenario = key in overrides
        out[key] = {
            "value": overrides[key] if scenario else a.value,
            "unit": a.unit,
            "source": "scenario" if scenario else a.source,
        }
    return out


def _recoverable(result: "RunResult", endpoint: str, bid: bytes) -> bool:
    e = result.endpoints[endpoint]
    if any(decode_shard(p[0]).header.bundle_id == bid for p in e.pending):
        return True  # still on the laptop, waiting for its node to come back
    site = e.site
    records = site.storage.load().get(T_SHARDS, {}) if site.up else {}
    held = [decode_shard(cbor2.loads(v)[0]) for v in records.values()]
    mine = [s for s in held if s.header.bundle_id == bid]
    return bool(mine) and len(mine) >= mine[0].header.k


def _ledger_ok(ledger: dict[bytes, bytes]) -> bool:
    prev = bytes(32)
    for seq in sorted(ledger, key=lambda k: int.from_bytes(k)):
        r = decode_receipt(ledger[seq])
        if r.prev != prev:
            return False
        prev = hashlib.sha256(ledger[seq]).digest()
    return True


def _stats(v: dict[str, list[float]]) -> dict[str, float]:
    lat = sorted(x / 60 for x in v["latency"])
    sent = len(v["sent"])

    def q(p: float) -> float:
        return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else float("nan")

    return {
        "sent": sent,
        "delivered_pct": 100 * len(lat) / sent if sent else 0.0,
        "p50_min": q(0.5) if lat else float("nan"),
        "p90_min": q(0.9),
        "max_min": lat[-1] if lat else float("nan"),
        "mean_min": statistics.fmean(lat) if lat else float("nan"),
    }
