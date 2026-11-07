from collections import Counter
from pathlib import Path

import pytest

from endpoint.state import Status
from simulation.runner import run
from simulation.scenario import load_scenario

SCENARIO = """
name: hostile
duration_s: 14400
nodes:
  - {id: CLINIC, role: site, sa: SA_C, at: [0, 0]}
  - {id: HOSPITAL, role: hub, sa: SA_H, at: [6000, 0]}
  - id: AMBULANCE
    role: mule
    route: {waypoints: [[0, 0], [6000, 0]], speed: 15, dwell_s: 300, start_s: 200}
  - id: FORGER
    role: mule
    route: {waypoints: [[0, 0], [6000, 0]], speed: 12, dwell_s: 200}
    hostile: {attacks: [forge_shards, corrupt_shards, forge_acks, replay]}
  - id: BLACKHOLE  # lures custody with a perfect route, then discards
    role: mule
    route: {waypoints: [[0, 0], [6000, 0]], speed: 20, dwell_s: 60}
    hostile: {attacks: [blackhole]}
  - id: ROGUE
    role: mule
    route: {waypoints: [[0, 0], [6000, 0]], speed: 10, dwell_s: 100}
    hostile: {attacks: [replay, forge_acks], registered: false}
endpoints:
  - {name: clinic, node: CLINIC}
  - {name: hospital, node: HOSPITAL}
messages:
  - {at_s: 60, from: clinic, to: hospital, priority: CRITICAL, text: "referral one"}
  - {at_s: 900, from: clinic, to: hospital, priority: ROUTINE, text: "referral two"}
  - {at_s: 1800, from: hospital, to: clinic, priority: URGENT, text: "reply"}
"""


@pytest.mark.req("REQ-SIM-03", "REQ-SEC-04", "REQ-SEC-06", "REQ-PROTO-06", "REQ-SAFE-01")
def test_mesh_delivers_everything_despite_insider_and_rogue(tmp_path: Path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(SCENARIO)
    result = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")
    for name in ("clinic", "hospital"):
        e = result.endpoints[name]
        assert {e.status(b) for b in e.sent} == {Status.DELIVERED}, name
    assert [m.content for m in result.endpoints["hospital"].inbox] == [
        b"referral one",
        b"referral two",
    ]
    honest = Counter[str]()
    for n in ("CLINIC", "HOSPITAL", "AMBULANCE"):
        honest.update(result.world.nodes[n].core.dropped)
    assert honest["bad_signature"] and honest["corrupt"] and honest["bad_ack"]
    assert honest["unauthenticated_peer"]  # replayed summaries and the rogue's chain refused
