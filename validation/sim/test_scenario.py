from pathlib import Path

import pytest

from simulation.scenario import ScenarioError, load_scenario


def test_skeleton_loads() -> None:
    s = load_scenario("skeleton")
    assert [n.config.node_id for n in s.nodes] == ["CLINIC_01", "HOSPITAL", "AMBULANCE_01"]


@pytest.mark.parametrize(
    "bad",
    [
        "name: x\nduration_s: 1\nnodes: []\nendpoints: []\ntypo: 1\n",
        "name: x\nduration_s: 1\nnodes: []\nendpoints: [{name: e, node: NOPE}]\n",
        "name: x\nduration_s: 1\nnodes: [{id: A, role: mule}]\nendpoints: []\n",
        "name: x\nduration_s: 1\nnodes: [{id: A, role: mule, sa: S, at: [0, 0]}]\nendpoints: []\n",
    ],
)
def test_invalid_scenarios_fail_fast(tmp_path: Path, bad: str) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text(bad)
    with pytest.raises(ScenarioError):
        load_scenario(str(f))
