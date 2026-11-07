from dataclasses import asdict
from pathlib import Path

import pytest

from node.bundle import decode_shard
from simulation.runner import run
from simulation.scenario import load_scenario

SCENARIO = """
name: two-sites-one-area
duration_s: 3600
nodes:
  - {id: HOSPITAL, role: hub, sa: SA_HOSPITAL, at: [0, 0]}
  - {id: WARD_A, role: site, sa: SA_DISTRICT, at: [2000, 0]}
  - {id: WARD_B, role: site, sa: SA_DISTRICT, at: [2050, 0]}
  - id: VAN
    role: mule
    route: {waypoints: [[0, 0], [2025, 0]], speed: 15, dwell_s: 120}
endpoints:
  - {name: hospital, node: HOSPITAL}
  - {name: ward-a, node: WARD_A}
  - {name: ward-b, node: WARD_B}
messages:
  - {at_s: 10, from: hospital, to: ward-b, priority: URGENT, text: "Lab result for ward B"}
"""


@pytest.mark.req("REQ-SEC-09")
def test_only_the_recipient_opens_and_headers_name_no_endpoint(tmp_path: Path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(SCENARIO)
    result = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")
    assert [m.content for m in result.endpoints["ward-b"].inbox] == [b"Lab result for ward B"]
    assert result.endpoints["ward-a"].inbox == []  # got the bundle, could not open it
    assert {n for n, node in result.world.nodes.items() if node.core.delivered} >= {
        "WARD_A",
        "WARD_B",
    }
    [sent] = result.endpoints["hospital"].sent.values()
    assert sent.status.value == "delivered"

    f.write_text(SCENARIO.replace("duration_s: 3600", "duration_s: 20"))  # stop while still held
    early = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "early")
    blobs = early.world.nodes["HOSPITAL"].storage.load()["shards"].values()
    assert blobs
    for blob in blobs:
        header = str(asdict(decode_shard(blob).header))
        assert not any(name in header for name in ("ward-a", "ward-b", "hospital"))
