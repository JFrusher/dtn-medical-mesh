import csv
from pathlib import Path

import pytest

from node.bundle import Priority
from simulation.batch import apply, jobs_for, main
from simulation.scenario import load_scenario


def test_variants_change_the_scenario() -> None:
    s = load_scenario("rural-clinics")
    v = apply(
        s,
        {
            "drop": ["VAN_EAST"],
            "set": {"radio.wifi_ple": 3.3},
            "parity": {"CRITICAL": 10},
            "volume": 2,
        },
    )
    assert "VAN_EAST" not in {n.config.node_id for n in v.nodes}
    assert v.radio.wifi_ple == 3.3 and v.overrides["radio.wifi_ple"] == 3.3
    assert v.parity == {Priority.CRITICAL: 10}
    assert len(v.messages) == 2 * len(s.messages)


@pytest.mark.parametrize(
    "bad",
    [
        {"drop": ["NOPE"]},
        {"drop": ["HOSPITAL"]},
        {"set": {"compute.x": 1}},
        {"volume": 0},
        {"speed": 2},
    ],
)
def test_bad_variants_fail_before_any_run(bad: dict[str, object]) -> None:
    with pytest.raises((ValueError, KeyError)):
        jobs_for("rural-clinics", {"bad": bad}, seeds=1)


@pytest.mark.req("REQ-SIM-02")
def test_sweep_runs_in_parallel_and_writes_csv(tmp_path: Path) -> None:
    assert main(["--sweep", "simulation/sweeps/skeleton.yaml", "--out", str(tmp_path)]) == 0
    runs = list(csv.DictReader((tmp_path / "runs.csv").open()))
    msgs = list(csv.DictReader((tmp_path / "messages.csv").open()))
    assert sorted((r["variant"], r["seed"]) for r in runs) == [
        (v, str(s)) for v in ("baseline", "double_volume") for s in (1, 2, 3)
    ]
    sent = {v: sum(1 for m in msgs if m["variant"] == v) for v in ("baseline", "double_volume")}
    assert sent["double_volume"] == 2 * sent["baseline"] == 12
    assert all(m["latency_s"] for m in msgs if m["status"] == "DELIVERED")
    assert runs[0]["autonomy_days_min"] == ""  # no battery nodes in the skeleton
