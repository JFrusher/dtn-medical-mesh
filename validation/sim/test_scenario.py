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


def test_workload_expands_with_sizes_and_capacity(tmp_path: Path) -> None:
    f = tmp_path / "w.yaml"
    f.write_text("""
name: w
duration_s: 100
nodes:
  - {id: H, role: hub, sa: SA_H, at: [0, 0], capacity_mb: 2}
  - {id: C, role: site, sa: SA_C, at: [9, 0]}
endpoints: [{name: h, node: H}, {name: c, node: C}]
messages: [{at_s: 5, from: c, to: h, priority: URGENT, text: one-off}]
workload:
  - {from: c, to: h, priority: ROUTINE, text: photo, size_bytes: 400000, every_s: 30, end_s: 60}
""")
    s = load_scenario(str(f))
    assert [(m.at_s, m.text) for m in s.messages] == [
        (0, "photo #1"),
        (5, "one-off"),
        (30, "photo #2"),
        (60, "photo #3"),
    ]
    assert len(s.messages[0].content()) == 400000
    assert s.nodes[0].config.capacity_bytes == 2 * 1024 * 1024
