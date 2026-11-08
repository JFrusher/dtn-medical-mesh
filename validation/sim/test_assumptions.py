from pathlib import Path

import pytest

from simulation.assumptions import ASSUMPTIONS, SOURCES, load
from simulation.metrics import evaluate
from simulation.radio_model import RadioParams
from simulation.runner import run
from simulation.scenario import load_scenario


@pytest.mark.req("REQ-SIM-03")
def test_every_assumption_has_a_known_source() -> None:
    assert ASSUMPTIONS
    assert all(a.source in SOURCES and a.ref for a in ASSUMPTIONS.values())


@pytest.mark.req("REQ-SIM-03")
def test_radio_params_come_from_assumptions() -> None:
    assert RadioParams.defaults().wifi_setup_s == ASSUMPTIONS["radio.wifi_setup_s"].value


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "a.yaml"
    bad.write_text("radio:\n  x: {value: 1, unit: m, source: guess, ref: r}\n")
    with pytest.raises(ValueError, match="radio.x"):
        load(bad)


@pytest.mark.req("REQ-SIM-03")
def test_report_lists_assumptions_and_marks_overrides(tmp_path: Path) -> None:
    scenario = load_scenario("skeleton")
    report = evaluate(run(scenario, 1, tmp_path), 1)
    assert report.assumptions["radio.wifi_range_m"]["source"] == "scenario"
    assert report.assumptions["radio.lora_sf"]["source"] == "estimate"
    assert "## Assumptions used" in report.to_markdown()
