from pathlib import Path

import pytest

from endpoint.state import Status
from simulation.runner import RunResult, run
from simulation.scenario import load_scenario

SKELETON = Path(__file__).parents[2] / "simulation/scenarios/skeleton.yaml"


def with_faults(tmp_path: Path, faults: str) -> RunResult:
    f = tmp_path / "s.yaml"
    f.write_text(SKELETON.read_text() + "faults:\n" + faults)
    return run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")


@pytest.mark.req("REQ-DUR-03", "REQ-SAFE-01", "REQ-SIM-03")
def test_messages_survive_power_cuts_at_the_worst_moments(tmp_path: Path) -> None:
    result = with_faults(
        tmp_path,
        # the clinic node is down when the referral is written; the ambulance while carrying it
        "  - {at_s: 50, node: CLINIC_01, kind: power_cut, value: 100}\n"
        "  - {at_s: 400, node: AMBULANCE_01, kind: power_cut, value: 60}\n",
    )
    log = "\n".join(result.log)
    assert "POWER OFF CLINIC_01" in log and "POWER ON AMBULANCE_01" in log
    for name in ("clinic", "hospital"):
        e = result.endpoints[name]
        assert [e.status(b) for b in e.sent] == [Status.DELIVERED], name


@pytest.mark.req("REQ-DUR-03")
def test_shards_survive_reboot_and_old_timers_die(tmp_path: Path) -> None:
    result = with_faults(
        tmp_path, "  - {at_s: 200, node: AMBULANCE_01, kind: power_cut, value: 5}\n"
    )
    amb = result.world.nodes["AMBULANCE_01"]
    assert amb.boots == 2 and amb.up


@pytest.mark.req("REQ-DUR-03")
def test_reboot_restores_shards_from_disk_and_drops_ram_state(tmp_path: Path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(SKELETON.read_text().replace("duration_s: 7200", "duration_s: 70"))
    world = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run").world
    amb = world.nodes["AMBULANCE_01"]  # stopped at 70 s: it has just taken the referral
    held = dict(amb.core.shards)
    assert held and amb.core.peer_has
    world.power_off(amb)
    world.power_on(amb)
    assert amb.core.shards.keys() == held.keys()  # committed shards came back from disk
    assert amb.core.peer_has == {}  # RAM state (open contacts) did not
