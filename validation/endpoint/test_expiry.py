from pathlib import Path

import pytest

from endpoint.state import Outgoing, Status
from simulation.runner import run
from simulation.scenario import load_scenario

NO_MULE = """
name: stranded
duration_s: 30000
nodes:
  - {id: CLINIC, role: site, sa: SA_C, at: [0, 0]}
  - {id: HOSPITAL, role: hub, sa: SA_H, at: [9000, 0]}
endpoints:
  - {name: clinic, node: CLINIC}
  - {name: hospital, node: HOSPITAL}
messages:
  - {at_s: 10, from: clinic, to: hospital, priority: CRITICAL, text: "nobody will carry this"}
"""


@pytest.mark.req("REQ-SAFE-01", "REQ-SAFE-02")
def test_undeliverable_message_is_flagged_and_never_resent(tmp_path: Path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(NO_MULE)
    result = run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")
    clinic = result.endpoints["clinic"]
    [bid] = clinic.sent
    assert clinic.status(bid) is Status.NOT_DELIVERED
    assert any("NOT DELIVERED clinic -> hospital" in line for line in result.log)
    assert len(clinic.sent) == 1 and result.endpoints["hospital"].inbox == []


def test_status_transitions() -> None:
    out = Outgoing("h", b"id", created_ms=1000, lifetime_s=10)
    assert out.status(5000) is Status.IN_TRANSIT
    assert out.status(11_001) is Status.NOT_DELIVERED
    out.delivered_ms = 12_000
    assert out.status(20_000) is Status.DELIVERED  # a late ACK still counts
