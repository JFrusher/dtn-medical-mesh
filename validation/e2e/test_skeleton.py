from pathlib import Path

import pytest

from simulation.runner import run
from simulation.scenario import load_scenario


@pytest.mark.req("REQ-SEC-01", "REQ-SEC-03", "REQ-SIM-01")
def test_referral_reaches_hospital_intact(tmp_path: Path) -> None:
    scenario = load_scenario("skeleton")
    result = run(scenario, seed=1, data_dir=tmp_path)
    inbox = result.endpoints["hospital"].inbox
    assert [m.content.decode() for m in inbox] == [scenario.messages[0].text]
    assert inbox[0].signer == result.endpoints["clinic"].keys.signing_public


@pytest.mark.req("REQ-SIM-02")
def test_same_seed_same_run(tmp_path: Path) -> None:
    scenario = load_scenario("skeleton")
    a = run(scenario, seed=3, data_dir=tmp_path / "a")
    b = run(scenario, seed=3, data_dir=tmp_path / "b")
    assert a.log == b.log


@pytest.mark.req("REQ-PROTO-06", "REQ-SAFE-01")
def test_delivery_ack_returns_to_origin_and_purges_everywhere(tmp_path: Path) -> None:
    result = run(load_scenario("skeleton"), seed=1, data_dir=tmp_path)
    [sent] = result.endpoints["clinic"].sent.values()
    assert sent.status.value == "delivered"
    for node in result.world.nodes.values():
        assert node.core.shards == {}, node.id
        assert len(node.core.acks) == 1, node.id
