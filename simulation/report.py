"""Sizing report from a batch run (spec §12.3): Markdown with SVG charts.

    uv run python -m simulation.report out/batch/arusha --scenario arusha

Confidence intervals are 95 % bootstrap intervals over seeds (each seed is one independent
simulated day), so they widen when seeds disagree. "Within N h" counts only messages sent at
least N hours before the run ended, so late-day messages are not counted as late.
"""

import argparse
import csv
import random
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from simulation.assumptions import ASSUMPTIONS

BOOTSTRAP = 1000
WITHIN_H = (2, 4, 6)


@dataclass(frozen=True)
class Msg:
    seed: int
    priority: str
    sent_s: float
    status: str
    latency_s: float | None


@dataclass(frozen=True)
class Estimate:
    value: float
    low: float
    high: float

    def text(self, fmt: str = "{:.0f}") -> str:
        if self.value != self.value:  # NaN: nothing to measure
            return "n/a"
        return f"{fmt.format(self.value)} ({fmt.format(self.low)}–{fmt.format(self.high)})"


def bootstrap(
    seeds: list[int], metric: Callable[[list[int]], float], rng: random.Random
) -> Estimate:
    value = metric(seeds)
    draws = sorted(
        v for v in (metric([rng.choice(seeds) for _ in seeds]) for _ in range(BOOTSTRAP)) if v == v
    )
    if not draws:
        return Estimate(value, value, value)
    return Estimate(value, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1])


def load(out: Path) -> tuple[dict[str, list[Msg]], dict[str, list[dict[str, str]]]]:
    msgs: dict[str, list[Msg]] = defaultdict(list)
    for r in csv.DictReader((out / "messages.csv").open()):
        lat = float(r["latency_s"]) if r["latency_s"] else None
        msgs[r["variant"]].append(
            Msg(int(r["seed"]), r["priority"], float(r["sent_s"]), r["status"], lat)
        )
    runs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in csv.DictReader((out / "runs.csv").open()):
        runs[r["variant"]].append(r)
    return msgs, runs


def summarise(msgs: list[Msg], runs: list[dict[str, str]], seed: int = 1) -> dict[str, Estimate]:
    rng = random.Random(seed)
    seeds = sorted({int(r["seed"]) for r in runs})
    duration = float(runs[0]["duration_s"])
    by_seed: dict[int, list[Msg]] = defaultdict(list)
    for m in msgs:
        by_seed[m.seed].append(m)

    def pooled(ss: list[int], priority: str | None = None) -> list[Msg]:
        return [m for s in ss for m in by_seed[s] if priority is None or m.priority == priority]

    def delivered_pct(ss: list[int]) -> float:
        ms = pooled(ss)
        return 100 * sum(m.status == "DELIVERED" for m in ms) / len(ms) if ms else float("nan")

    def within(hours: float, priority: str | None) -> Callable[[list[int]], float]:
        def f(ss: list[int]) -> float:
            ms = [m for m in pooled(ss, priority) if m.sent_s <= duration - hours * 3600]
            ok = sum(m.latency_s is not None and m.latency_s <= hours * 3600 for m in ms)
            return 100 * ok / len(ms) if ms else float("nan")

        return f

    def latency_q(q: float) -> Callable[[list[int]], float]:
        def f(ss: list[int]) -> float:
            lat = sorted(m.latency_s / 3600 for m in pooled(ss) if m.latency_s is not None)
            return lat[min(len(lat) - 1, int(q * len(lat)))] if lat else float("nan")

        return f

    run_by_seed = {int(r["seed"]): r for r in runs}

    def run_stat(key: str, how: Callable[[list[float]], float]) -> Callable[[list[int]], float]:
        def f(ss: list[int]) -> float:
            vals = [float(run_by_seed[s][key]) for s in ss if run_by_seed[s][key] != ""]
            return how(vals) if vals else float("nan")

        return f

    def overhead(ss: list[int]) -> float:
        sent = sum(float(run_by_seed[s]["wifi_bytes"]) for s in ss)
        useful = sum(float(run_by_seed[s]["delivered_bytes"]) for s in ss)
        return sent / useful if useful else float("nan")

    out = {
        "delivered_pct": bootstrap(seeds, delivered_pct, rng),
        "p50_h": bootstrap(seeds, latency_q(0.5), rng),
        "p90_h": bootstrap(seeds, latency_q(0.9), rng),
        "overhead": bootstrap(seeds, overhead, rng),
        "peak_mule_mb": bootstrap(
            seeds, run_stat("peak_storage_mule", lambda v: max(v) / 1e6), rng
        ),
        "peak_relay_mb": bootstrap(
            seeds, run_stat("peak_storage_relay", lambda v: max(v) / 1e6), rng
        ),
        "min_battery_pct": bootstrap(seeds, run_stat("min_battery_pct", min), rng),
        "busy_pct_max": bootstrap(seeds, run_stat("busy_pct_max", max), rng),
    }
    for h in WITHIN_H:
        out[f"critical_{h}h"] = bootstrap(seeds, within(h, "CRITICAL"), rng)
        out[f"all_{h}h"] = bootstrap(seeds, within(h, None), rng)
    return out


STYLE = (
    ".bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.grid{stroke:#e4e3df}"
    ".bar{fill:#2a78d6}.ci{stroke:#0b0b0b;stroke-width:1.5}"
    "@media (prefers-color-scheme: dark){.bg{fill:#1a1a19}.t1{fill:#f0efec}.t2{fill:#c3c2b7}"
    ".grid{stroke:#3a3936}.bar{fill:#3987e5}.ci{stroke:#f0efec}}"
)


def el(tag: str, text: str = "", **attrs: object) -> str:
    a = " ".join(f'{k.rstrip("_").replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f"<{tag} {a}>{escape(text)}</{tag}>" if text else f"<{tag} {a}/>"


def bar_chart(
    title: str,
    unit: str,
    rows: list[tuple[str, Estimate]],
    max_value: float | None = None,
    fmt: str = "{:.0f}",
) -> str:
    """Horizontal bars with 95 % interval whiskers. One series: the title names it, no legend.
    Static (embedded in Markdown), so the report's tables are the readable view of each value."""
    label_w, bar_w, row_h, top = 190, 420, 34, 44
    known = [e.high for _, e in rows if e.high == e.high]
    scale_max = max_value or max(known, default=1) * 1.1
    h = top + row_h * len(rows) + 30

    def x(v: float) -> float:
        return round(label_w + bar_w * max(0.0, v) / scale_max, 1)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {label_w + bar_w + 90} {h}" '
        f'font-family="system-ui, sans-serif" font-size="13" role="img" '
        f'aria-label="{escape(title)}">',
        f"<style>{STYLE}</style>",
        el("rect", class_="bg", width="100%", height="100%"),
        el("text", title, class_="t1", x=16, y=24, font_size=15, font_weight=600),
    ]
    for i in range(5):  # recessive gridlines and their values
        v = scale_max * i / 4
        out.append(el("line", class_="grid", x1=x(v), x2=x(v), y1=top - 6, y2=h - 26))
        out.append(
            el("text", f"{v:.0f}{unit}", class_="t2", x=x(v), y=h - 10, text_anchor="middle")
        )
    for i, (name, e) in enumerate(rows):
        y = top + i * row_h
        out.append(el("text", name, class_="t1", x=label_w - 10, y=y + 17, text_anchor="end"))
        if e.value != e.value:
            out.append(el("text", "n/a", class_="t2", x=label_w + 6, y=y + 17))
            continue
        w = x(e.value) - label_w
        r = min(4.0, w)  # 4 px rounded data end, square at the baseline
        d = f"M{label_w},{y + 6} h{w - r:.1f} q{r},0 {r},{r} v{12 - 2 * r} "
        d += f"q0,{r} -{r},{r} h-{w - r:.1f} z"
        out.append(el("path", class_="bar", d=d))
        out.append(el("line", class_="ci", x1=x(e.low), x2=x(e.high), y1=y + 12, y2=y + 12))
        for v in (e.low, e.high):
            out.append(el("line", class_="ci", x1=x(v), x2=x(v), y1=y + 7, y2=y + 17))
        value = fmt.format(e.value) + unit
        out.append(el("text", value, class_="t2", x=x(e.high) + 6, y=y + 17))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def table(stats: dict[str, dict[str, Estimate]], columns: list[tuple[str, str, str]]) -> list[str]:
    """Markdown rows: (header, metric key, number format) per column, one row per variant."""
    head = "| Variant | " + " | ".join(c[0] for c in columns) + " |"
    rule = "|---" * (len(columns) + 1) + "|"
    body = [
        f"| {v} | " + " | ".join(s[key].text(fmt) for _, key, fmt in columns) + " |"
        for v, s in stats.items()
    ]
    return [head, rule, *body]


DELIVERY = [
    ("Delivered %", "delivered_pct", "{:.0f}"),
    ("CRITICAL ≤ 2 h %", "critical_2h", "{:.0f}"),
    ("CRITICAL ≤ 4 h %", "critical_4h", "{:.0f}"),
    ("CRITICAL ≤ 6 h %", "critical_6h", "{:.0f}"),
    ("All ≤ 4 h %", "all_4h", "{:.0f}"),
    ("p50 (h)", "p50_h", "{:.1f}"),
    ("p90 (h)", "p90_h", "{:.1f}"),
]
RESOURCES = [
    ("Peak storage, vehicle (MB)", "peak_mule_mb", "{:.2f}"),
    ("Peak storage, relay (MB)", "peak_relay_mb", "{:.2f}"),
    ("Lowest battery (%)", "min_battery_pct", "{:.0f}"),
    ("Busiest node (% of time)", "busy_pct_max", "{:.2f}"),
    ("Bytes moved per byte delivered", "overhead", "{:.0f}"),
]


def render(out: Path, title: str, scenario: str) -> str:
    msgs, runs = load(out)
    variants = list(runs)
    stats = {v: summarise(msgs[v], runs[v]) for v in variants}
    seeds = len(runs[variants[0]])
    violations = sum(int(r["violations"]) for rs in runs.values() for r in rs)
    critical = [(v, stats[v]["critical_4h"]) for v in variants]
    p90 = [(v, stats[v]["p90_h"]) for v in variants]
    (out / "critical_4h.svg").write_text(
        bar_chart("CRITICAL delivered within 4 h (%)", "%", critical, 100)
    )
    (out / "p90_hours.svg").write_text(
        bar_chart("90th percentile delivery time (h)", " h", p90, fmt="{:.1f}")
    )
    lines = [
        f"# {title}",
        "",
        f"Scenario `{scenario}`, {len(variants)} variants × {seeds} seeds. "
        f"Invariant violations across all runs: **{violations}**. "
        "Figures are point estimates with 95 % bootstrap intervals over seeds.",
        "",
        "![CRITICAL delivered within 4 h, by variant](critical_4h.svg)",
        "",
        "![90th percentile delivery time, by variant](p90_hours.svg)",
        "",
        "## Delivery",
        "",
        *table(stats, DELIVERY),
        "",
        "## Resources",
        "",
        *table(stats, RESOURCES),
        "",
        "## Assumptions",
        "",
        "Every value below is from `simulation/assumptions.yaml` unless a variant overrides it.",
        "",
        "| Parameter | Value | Unit | Source |",
        "|---|---|---|---|",
        *[f"| {k} | {a.value} | {a.unit} | {a.source} |" for k, a in sorted(ASSUMPTIONS.items())],
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("out", type=Path, help="a batch output directory")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--title", default="Sizing report")
    args = parser.parse_args(argv)
    (args.out / "report.md").write_text(render(args.out, args.title, args.scenario))
    print(f"wrote {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
