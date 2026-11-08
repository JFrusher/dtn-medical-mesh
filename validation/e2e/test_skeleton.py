from pathlib import Path

import pytest

from endpoint.fhir import Message
from endpoint.state import Status
from node.bundle import Priority
from simulation.runner import run
from simulation.scenario import load_scenario


@pytest.mark.req("REQ-SEC-01", "REQ-SEC-03", "REQ-SIM-01")
def test_referral_reaches_hospital_intact(tmp_path: Path) -> None:
    scenario = load_scenario("skeleton")
    result = run(scenario, seed=1, data_dir=tmp_path)
    inbox = result.endpoints["hospital"].inbox
    assert [m.message.text for m in inbox] == [scenario.messages[0].text]
    assert [m.message.text for m in result.endpoints["clinic"].inbox] == [scenario.messages[1].text]
    assert inbox[0].sender == "clinic"  # inner signature resolved through the signed directory


@pytest.mark.req("REQ-SIM-02")
def test_same_seed_same_run(tmp_path: Path) -> None:
    scenario = load_scenario("skeleton")
    a = run(scenario, seed=3, data_dir=tmp_path / "a")
    b = run(scenario, seed=3, data_dir=tmp_path / "b")
    assert a.log == b.log


@pytest.mark.req("REQ-PROTO-06", "REQ-PROTO-10", "REQ-SAFE-01")
def test_delivery_ack_returns_to_origin_and_purges_everywhere(tmp_path: Path) -> None:
    result = run(load_scenario("skeleton"), seed=1, data_dir=tmp_path)
    for name in ("clinic", "hospital"):
        endpoint = result.endpoints[name]
        [bid] = endpoint.sent
        assert endpoint.status(bid) is Status.DELIVERED, name
    for node in result.world.nodes.values():
        assert node.core.shards == {}, node.id
        assert len(node.core.acks) == 2, node.id


@pytest.mark.req("REQ-SEC-03")
def test_sender_not_in_directory_is_shown_unverified(tmp_path: Path) -> None:
    result = run(load_scenario("skeleton"), seed=1, data_dir=tmp_path)
    hospital = result.endpoints["hospital"]
    hospital.directory.pop("clinic")
    hospital.inbox.clear()
    world = result.world
    world.scheduler.schedule(
        world.scheduler.now,
        lambda: result.endpoints["clinic"].send_message(
            "hospital", Message("again"), Priority.ROUTINE
        ),
    )
    world.scheduler.run_until(world.scheduler.now + 7200)
    assert [(m.sender, m.message.text) for m in hospital.inbox] == [(None, "again")]
