from pathlib import Path

import pytest

from endpoint.state import Status
from simulation.runner import run
from simulation.scenario import load_scenario
from simulation.scheduler import Scheduler
from simulation.virtual_hw import SIM_EPOCH, VirtualRtc


def test_rtc_drift_reset_and_failure() -> None:
    s = Scheduler(seed=1)
    rtc = VirtualRtc(s)
    rtc.drift_ppm = 100
    s.run_until(10_000)
    assert rtc.read() == pytest.approx(SIM_EPOCH + 10_001)
    rtc.reset_to_epoch()
    s.run_until(10_060)
    assert rtc.read() == pytest.approx(60 + 10_060 * 100e-6)  # drift keeps running


@pytest.mark.req("REQ-TIME-01", "REQ-TIME-02", "REQ-SIM-03")
def test_mesh_still_delivers_when_clocks_reset_and_fail(tmp_path: Path) -> None:
    text = (Path(__file__).parents[2] / "simulation/scenarios/skeleton.yaml").read_text()
    text += """faults:
  - {at_s: 30, node: CLINIC_01, kind: rtc_reset}
  - {at_s: 30, node: AMBULANCE_01, kind: rtc_fail}
  - {at_s: 30, node: HOSPITAL, kind: rtc_drift, value: 200}
"""
    f = tmp_path / "s.yaml"
    f.write_text(text)
    result = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")
    for name in ("clinic", "hospital"):
        e = result.endpoints[name]
        assert [e.status(b) for b in e.sent] == [Status.DELIVERED], name
    assert sum(line.count("FAULT") for line in result.log) == 3
