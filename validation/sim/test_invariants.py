from pathlib import Path

import pytest

from node.events import Delete, Store
from simulation.metrics import evaluate
from simulation.runner import RunResult, run
from simulation.scenario import load_scenario

SKELETON = Path(__file__).parents[2] / "simulation/scenarios/skeleton.yaml"


def skeleton(tmp_path: Path, duration: int = 7200) -> RunResult:
    f = tmp_path / "s.yaml"
    f.write_text(SKELETON.read_text().replace("duration_s: 7200", f"duration_s: {duration}"))
    return run(load_scenario(str(f)), seed=1, data_dir=tmp_path / "run")


@pytest.mark.req("REQ-SAFE-01")
def test_clean_run_has_no_violations(tmp_path: Path) -> None:
    report = evaluate(skeleton(tmp_path), seed=1)
    assert report.violations == [] and report.by_status == {"DELIVERED": 2}
    assert "no silent loss" in report.to_markdown()


@pytest.mark.req("REQ-SAFE-01")
def test_ack_without_delivery_is_caught(tmp_path: Path) -> None:
    result = skeleton(tmp_path)
    result.endpoints["hospital"].inbox.clear()  # as if a forged ACK had been accepted
    assert any("ACKed but" in v for v in evaluate(result, 1).violations)


@pytest.mark.req("REQ-SAFE-01", "REQ-PROTO-05")
def test_unrecoverable_in_transit_message_is_caught(tmp_path: Path) -> None:
    result = skeleton(tmp_path, duration=100)  # referral still in transit
    clinic = result.world.nodes["CLINIC_01"]
    assert evaluate(result, 1).violations == []
    for sid in list(clinic.core.shards):
        clinic.storage.apply(Delete(table="shards", key=sid))  # origin wrongly let go
    assert any("no longer recoverable" in v for v in evaluate(result, 1).violations)


@pytest.mark.req("REQ-PROTO-04")
def test_tampered_custody_ledger_is_caught(tmp_path: Path) -> None:
    result = skeleton(tmp_path)
    node = result.world.nodes["AMBULANCE_01"]  # has two chained receipts
    ledger = node.storage.load()["ledger"]
    first = min(ledger, key=lambda k: int.from_bytes(k))
    assert len(ledger) >= 2
    node.storage.apply(Store(table="ledger", key=first, value=ledger[max(ledger)]))
    assert any("ledger hash chain broken" in v for v in evaluate(result, 1).violations)
