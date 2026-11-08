import random
from pathlib import Path

import pytest

from simulation.batch import main as batch
from simulation.report import Estimate, Msg, bar_chart, bootstrap, main, summarise


def test_bootstrap_interval_contains_the_estimate_and_widens_with_spread() -> None:
    def mean(values: dict[int, float]):  # type: ignore[no-untyped-def]
        return lambda ss: sum(values[s] for s in ss) / len(ss)

    tight = bootstrap(list(range(10)), mean({s: 50 + s % 2 for s in range(10)}), random.Random(1))
    wide = bootstrap(list(range(10)), mean({s: 10 * s for s in range(10)}), random.Random(1))
    assert tight.low <= tight.value <= tight.high and wide.low <= wide.value <= wide.high
    assert wide.high - wide.low > 10 * (tight.high - tight.low)


def test_within_n_hours_ignores_messages_sent_too_late_to_count() -> None:
    runs = [
        {
            "seed": "1",
            "duration_s": "36000",
            "wifi_bytes": "1",
            "delivered_bytes": "1",
            "peak_storage_mule": "0",
            "peak_storage_relay": "0",
            "min_battery_pct": "",
            "busy_pct_max": "0",
        }
    ]
    msgs = [
        Msg(1, "CRITICAL", 0, "DELIVERED", 3000),  # within 2 h
        Msg(1, "CRITICAL", 100, "IN_TRANSIT", None),  # never: counts against
        Msg(1, "CRITICAL", 35000, "IN_TRANSIT", None),  # sent 17 min before the end: not counted
    ]
    s = summarise(msgs, runs)
    assert s["critical_2h"].value == 50
    assert s["delivered_pct"].value == pytest.approx(100 / 3)


def test_chart_is_an_accessible_svg_with_dark_mode() -> None:
    svg = bar_chart(
        "Title", "%", [("a", Estimate(40, 30, 50)), ("b", Estimate(float("nan"), 0, 0))], 100
    )
    assert svg.startswith("<svg") and 'role="img"' in svg and "prefers-color-scheme: dark" in svg
    assert ">n/a<" in svg and ">40%<" in svg


def test_report_renders_from_a_sweep(tmp_path: Path) -> None:
    assert batch(["--sweep", "simulation/sweeps/skeleton.yaml", "--out", str(tmp_path)]) == 0
    assert main([str(tmp_path), "--scenario", "skeleton", "--title", "T"]) == 0
    md = (tmp_path / "report.md").read_text()
    assert md.startswith("# T") and "| double_volume |" in md and "## Assumptions" in md
    assert (tmp_path / "critical_4h.svg").exists() and (tmp_path / "p90_hours.svg").exists()
