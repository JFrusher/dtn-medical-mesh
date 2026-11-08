from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interface.app import create_app
from simulation.admin import LogEntry
from simulation.live import DEMO_PIN, LiveSim
from simulation.runner import build
from simulation.scenario import load_scenario


@pytest.fixture
def sim(tmp_path: Path) -> LiveSim:
    return LiveSim(build(load_scenario("skeleton"), seed=1, data_dir=tmp_path), speed=60)


def admin(api: TestClient) -> dict[str, str]:
    r = api.post("/api/admin/session", json={"operator": "Admin F. Mbise", "pin": DEMO_PIN})
    assert r.status_code == 200
    return {"authorization": f"Bearer {r.json()['token']}"}


def sign_in_staff(api: TestClient, endpoint: str) -> dict[str, str]:
    staff = next(e for e in api.get("/api/endpoints").json() if e["name"] == endpoint)["staff"][0]
    r = api.post(f"/api/endpoints/{endpoint}/session", json={"staff": staff, "pin": DEMO_PIN})
    return {"authorization": f"Bearer {r.json()['token']}"}


def run_minutes(sim: LiveSim, n: int) -> None:
    for _ in range(n):
        sim.advance(1)  # 60 s of virtual time each


@pytest.mark.req("REQ-OPS-02")
def test_inventory_comes_from_health_reports(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    assert api.get("/api/admin").status_code == 401
    assert (
        api.post("/api/admin/session", json={"operator": "Admin F. Mbise", "pin": "1"}).status_code
        == 401
    )
    h = admin(api)
    run_minutes(sim, 60)
    s = api.get("/api/admin", headers=h).json()
    nodes = {n["id"]: n for n in s["nodes"]}
    assert set(nodes) == {"CLINIC_01", "HOSPITAL", "AMBULANCE_01"}
    assert nodes["CLINIC_01"]["version"] == "0.2.0" and nodes["CLINIC_01"]["light"]
    assert not nodes["AMBULANCE_01"]["stale"] and nodes["HOSPITAL"]["battery_pct"] is None
    assert [c["clinic"] for c in s["clinics"]] == ["clinic"] and s["clinics"][0]["arrived"] >= 1
    assert {la["name"] for la in s["laptops"]} == {"clinic", "hospital"}
    assert api.get("/admin").status_code == 200


@pytest.mark.req("REQ-OPS-03", "REQ-SEC-07")
def test_revocation_from_the_console_spreads_and_is_logged_and_signed(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = admin(api)
    r = api.post("/api/admin/actions", headers=h, json={"action": "revoke", "target": "CLINIC_01"})
    assert r.status_code == 200
    run_minutes(sim, 60)
    assert "CLINIC_01" in sim.r.world.nodes["AMBULANCE_01"].core.revoked  # carried over the mesh
    [entry] = api.get("/api/admin", headers=h).json()["log"]
    assert (entry["action"], entry["target"], entry["signed"]) == ("revoke", "CLINIC_01", True)
    e = sim.admin.log[0]
    assert not sim.admin.log_authentic(LogEntry(e.at, e.operator, e.action, "HOSPITAL", e.sig))
    bad = api.post("/api/admin/actions", headers=h, json={"action": "revoke", "target": "HOSPITAL"})
    assert bad.status_code == 422


@pytest.mark.req("REQ-OPS-03", "REQ-OPS-07")
def test_removed_laptop_drops_out_of_every_directory(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = admin(api)
    api.post(
        "/api/admin/actions", headers=h, json={"action": "remove_laptop", "target": "hospital"}
    )
    refused = api.post(
        "/api/endpoints/hospital/messages",
        headers=sign_in_staff(api, "hospital"),
        json={"to": "clinic", "text": "from a lost laptop"},
    )
    assert refused.status_code == 422 and "directory" in refused.json()["detail"]
    run_minutes(sim, 60)
    assert "hospital" not in sim.r.endpoints["clinic"].directory  # the new directory travelled
    laptops = {la["name"]: la for la in api.get("/api/admin", headers=h).json()["laptops"]}
    assert laptops["hospital"]["in_directory"] is False


@pytest.mark.req("REQ-OPS-03")
def test_spare_relay_provisioned_from_the_console_joins_and_reports(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = admin(api)
    act = {"action": "provision_relay", "target": "SPARE_RELAY"}
    assert api.post("/api/admin/actions", headers=h, json=act).status_code == 200
    assert api.post("/api/admin/actions", headers=h, json=act).status_code == 422  # ID taken
    run_minutes(sim, 30)
    nodes = {n["id"]: n for n in api.get("/api/admin", headers=h).json()["nodes"]}
    assert nodes["SPARE_RELAY"]["role"] == "relay" and nodes["SPARE_RELAY"]["version"] == "0.2.0"


@pytest.mark.req("REQ-OPS-05")
def test_console_stages_an_update_and_watches_versions_change(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = admin(api)
    bad = api.post("/api/admin/actions", headers=h, json={"action": "stage_update", "target": "x"})
    assert bad.status_code == 422
    api.post("/api/admin/actions", headers=h, json={"action": "stage_update", "target": "all"})
    run_minutes(sim, 120)
    versions = {n["id"]: n["version"] for n in api.get("/api/admin", headers=h).json()["nodes"]}
    assert set(versions.values()) == {"0.3.0"}, versions
