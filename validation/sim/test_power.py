import math
from pathlib import Path

import pytest

from node.events import PowerLow, Timer
from node.pki import Role
from simulation.metrics import evaluate
from simulation.runner import run
from simulation.scenario import load_scenario
from simulation.virtual_hw import Battery
from validation.node.helpers import connect, frame, frames, make_node, make_shards, shard_msg

DAY = 24 * 3600


@pytest.mark.req("REQ-SIM-03")
def test_solar_curve_and_battery_limits() -> None:
    b = Battery("solar", capacity_wh=10, idle_w=1, active_w=2, panel_w=5)
    jan_day_wh = 5 * 6.31 * 0.75  # panel x NASA POWER January irradiance x derate
    noon = jan_day_wh * math.pi / 24
    assert b.solar_w(12 * 3600) == pytest.approx(noon) and b.solar_w(2 * 3600) == 0
    day = sum(b.solar_w(t) * 60 / 3600 for t in range(0, DAY, 60))
    assert day == pytest.approx(jan_day_wh, rel=0.01)
    for t in range(0, 4 * 3600, 60):  # midnight to 4am: no sun, idle
        b.step(t, 60, moving=False, active=False)
    for t in range(4 * 3600, 5 * 3600, 60):  # an hour transferring
        b.step(t, 60, moving=False, active=True)
    assert b.level_wh == pytest.approx(4)
    with pytest.raises(ValueError):
        Battery("nuclear", 1, 1, 1)


@pytest.mark.req("REQ-SIM-03")
def test_june_yields_less_than_january_and_autonomy_is_reported(tmp_path: Path) -> None:
    b = Battery.for_board("zero2w", kind="solar", capacity_wh=77, panel_w=10)
    june = 151 * DAY + 12 * 3600
    assert b.solar_w(june) < 0.75 * b.solar_w(12 * 3600)
    assert b.autonomy_days == pytest.approx(77 / (0.7 * 24))
    f = tmp_path / "s.yaml"
    f.write_text("""
name: lamp
duration_s: 7200
nodes:
  - {id: HUB, role: hub, sa: SA_H, at: [0, 0]}
  - {id: LAMP, role: relay, at: [5000, 0], power: {kind: solar, capacity_wh: 30, panel_w: 10}}
endpoints: []
""")
    report = evaluate(run(load_scenario(str(f)), 1, tmp_path / "r"), 1)
    assert report.power["LAMP"]["autonomy_days"] == pytest.approx(30 / (0.7 * 24))
    assert 90 < report.power["LAMP"]["min_pct"] < 100  # two night hours at idle


@pytest.mark.req("REQ-SIM-03")
def test_solar_relay_dies_at_night_and_returns_in_the_day(tmp_path: Path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(f"""
name: solar
duration_s: {2 * DAY}
tick_s: 30
nodes:
  - {{id: HUB, role: hub, sa: SA_H, at: [0, 0]}}
  - id: LAMP
    role: relay
    at: [5000, 0]
    power: {{kind: solar, capacity_wh: 6, panel_w: 3}}
endpoints: []
""")
    log = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "r").log
    offs = [line for line in log if "POWER OFF LAMP" in line]
    ons = [line for line in log if "POWER ON LAMP" in line]
    assert offs and ons and ons[0] > offs[0]


@pytest.mark.req("REQ-DUR-03", "REQ-PROTO-04")
def test_power_low_signs_receipts_for_everything_held() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    for s in make_shards(b"x"):
        frame(hub, "M", shard_msg(s))
    out = frames(hub.handle(PowerLow(mono=0.1, wall=None)))
    assert [f["t"] for f in out] == ["receipt"]
    assert hub.handle(Timer(mono=0.5, wall=None, name="receipt:M")) == []  # nothing left owed
