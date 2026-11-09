import base64
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interface.app import IDLE_LOCK_S, create_app
from interface.telemetry import NodeView, Telemetry
from simulation.live import DEMO_PIN, LiveSim
from simulation.runner import build
from simulation.scenario import load_scenario


@pytest.fixture
def sim(tmp_path: Path) -> LiveSim:
    return LiveSim(build(load_scenario("skeleton"), seed=1, data_dir=tmp_path), speed=60)


def sign_in(api: TestClient, endpoint: str, staff: str | None = None) -> dict[str, str]:
    who = (
        staff
        or next(e for e in api.get("/api/endpoints").json() if e["name"] == endpoint)["staff"][0]
    )
    r = api.post(f"/api/endpoints/{endpoint}/session", json={"staff": who, "pin": DEMO_PIN})
    assert r.status_code == 200
    return {"authorization": f"Bearer {r.json()['token']}"}


def test_telemetry_matches_documented_schema(sim: LiveSim) -> None:
    sim.advance(1)
    t = TestClient(create_app(sim)).get("/api/telemetry").json()
    assert set(t) == set(Telemetry.__annotations__)
    assert set(t["nodes"][0]) == set(NodeView.__annotations__)
    schema = json.loads((Path(__file__).parents[2] / "docs/telemetry.schema.json").read_text())
    assert set(schema["required"]) == set(Telemetry.__annotations__)
    assert set(schema["properties"]["nodes"]["items"]["required"]) == set(NodeView.__annotations__)


@pytest.mark.req("REQ-PROTO-10", "REQ-SEC-01")
def test_send_through_api_and_watch_it_deliver(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    assert [e["name"] for e in api.get("/api/endpoints").json()] == ["clinic", "hospital"]
    clinic, hospital = sign_in(api, "clinic"), sign_in(api, "hospital")
    photo = base64.b64encode(b"\xff\xd8" + bytes(3000)).decode()
    r = api.post(
        "/api/endpoints/clinic/messages",
        headers=clinic,
        json={
            "to": "hospital",
            "text": "query sepsis",
            "priority": "URGENT",
            "vitals": [{"code": "heart-rate", "value": 124}],
            "attachments": [
                {"title": "chart.jpg", "content_type": "image/jpeg", "data_b64": photo}
            ],
        },
    )
    assert r.status_code == 200
    mine = [
        m
        for m in api.get("/api/endpoints/clinic/messages", headers=clinic).json()
        if m["id"] == r.json()["id"]
    ]
    assert mine[0]["status"] == "in transit"
    for _ in range(80):  # 80 x 60 s of virtual time
        sim.advance(1)
    got = [
        m
        for m in api.get("/api/endpoints/hospital/messages", headers=hospital).json()
        if m["text"] == "query sepsis"
    ]
    assert got and "Heart rate 124" in got[0]["summary"] and "chart.jpg" in got[0]["summary"]
    sent = [
        m
        for m in api.get("/api/endpoints/clinic/messages", headers=clinic).json()
        if m["id"] == r.json()["id"]
    ]
    assert sent[0]["status"] == "delivered"


def test_bad_requests_are_explained(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = sign_in(api, "clinic")
    assert api.get("/api/endpoints/nobody/messages", headers=h).status_code == 404
    r = api.post("/api/endpoints/clinic/messages", headers=h, json={"to": "nobody", "text": "x"})
    assert r.status_code == 422 and "directory" in r.json()["detail"]
    bad = {"to": "hospital", "text": "x", "vitals": [{"code": "mood", "value": 1}]}
    assert api.post("/api/endpoints/clinic/messages", headers=h, json=bad).status_code == 422
    answer = {"to": "hospital", "text": "x", "decision": "maybe", "in_response_to": "ab"}
    assert api.post("/api/endpoints/clinic/messages", headers=h, json=answer).status_code == 422


def test_controls_and_live_stream(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    api.post("/api/control", json={"speed": 600, "paused": True})
    before = sim.now
    sim.advance(1)
    assert sim.now == before and sim.speed == 600
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["paused"] is True


@pytest.mark.req("REQ-PROTO-04")
def test_journey_follows_custody_receipts(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    for _ in range(40):  # 40 min of virtual time: the referral reaches the hospital
        sim.advance(1)
    [m] = [j for j in api.get("/api/journeys").json() if j["from"] == "clinic"]
    j = api.get(f"/api/journey/{m['id']}").json()
    assert (j["from_node"], j["to_node"]) == ("CLINIC_01", "HOSPITAL")
    path = [(h["from"], h["to"]) for h in j["hops"]]
    assert ("CLINIC_01", "AMBULANCE_01") in path and ("AMBULANCE_01", "HOSPITAL") in path
    assert j["opened"] and j["k"] == 4 and j["total"] == 6
    assert api.get("/api/journey/00").status_code == 404


@pytest.mark.req("REQ-SEC-14")
def test_staff_must_sign_in_wrong_pins_lock_and_idle_sessions_lock(
    sim: LiveSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = TestClient(create_app(sim))
    assert api.get("/api/endpoints/clinic/messages").status_code == 401
    clinic = sign_in(api, "clinic")
    assert api.get("/api/endpoints/hospital/messages", headers=clinic).status_code == 401
    wrong = {"staff": "Nurse A. Mollel", "pin": "0000"}
    for _ in range(5):
        assert api.post("/api/endpoints/hospital/session", json=wrong).status_code == 401
    locked = api.post("/api/endpoints/hospital/session", json={**wrong, "pin": DEMO_PIN})
    assert locked.status_code == 429 and locked.json()["detail"]["retry_after_s"] > 0
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + IDLE_LOCK_S + 1)
    assert api.get("/api/endpoints/clinic/messages", headers=clinic).status_code == 401
    api.delete("/api/session", headers=clinic)


@pytest.mark.req("REQ-APP-02", "REQ-APP-03", "REQ-APP-04")
def test_referral_answer_and_result_travel_as_tracked_messages(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    clinic, hospital = sign_in(api, "clinic"), sign_in(api, "hospital", "Dr. N. Mushi")
    [patient, *_] = api.get("/api/endpoints/clinic/patients", headers=clinic).json()
    assert set(patient) == {"local_id", "name", "age", "sex"}
    referral = {
        "to": "hospital",
        "priority": "CRITICAL",
        "patient": patient,
        "referral": {"reason": "34 weeks, BP 160/110", "diagnosis": "pre-eclampsia"},
    }
    rid = api.post("/api/endpoints/clinic/messages", headers=clinic, json=referral).json()["id"]

    def wait_for(endpoint: str, headers: dict[str, str], match: str) -> dict[str, object]:
        for _ in range(200):
            sim.advance(1)
            got = [
                m
                for m in api.get(f"/api/endpoints/{endpoint}/messages", headers=headers).json()
                if m["direction"] == "in" and match in m["summary"]
            ]
            if got:
                found: dict[str, object] = got[0]
                return found
        raise AssertionError(f"{match!r} never reached {endpoint}")

    arrived = wait_for("hospital", hospital, "34 weeks")
    assert arrived["author"] == "Nurse A. Mollel" and arrived["priority"] == "CRITICAL"
    assert arrived["patient"] == patient and float(str(arrived["waited_s"])) > 0
    assert api.get("/api/endpoints/hospital/patients", headers=hospital).json() == [patient]
    answer = {"to": "clinic", "text": "Send her now", "decision": "accepted", "in_response_to": rid}
    api.post("/api/endpoints/hospital/messages", headers=hospital, json=answer)
    result = {
        "to": "clinic",
        "in_response_to": rid,
        "patient": patient,
        "result": {"test": "Hb", "conclusion": "8.1 g/dL"},
    }
    api.post("/api/endpoints/hospital/messages", headers=hospital, json=result)
    got = wait_for("clinic", clinic, "Send her now")
    assert got["decision"] == "accepted" and got["in_response_to"] == rid
    assert got["author"] == "Dr. N. Mushi"
    assert wait_for("clinic", clinic, "Result: Hb")["result"] == {
        "test": "Hb",
        "conclusion": "8.1 g/dL",
    }


def test_clinic_registers_patients(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    h = sign_in(api, "clinic")
    new = {"local_id": "CLI-0100", "name": "Asha Mrema", "age": 7, "sex": "female"}
    assert api.post("/api/endpoints/clinic/patients", headers=h, json=new).status_code == 200
    assert (
        api.post("/api/endpoints/clinic/patients", headers=h, json=new).status_code == 422
    )  # twice
    bad = {**new, "local_id": "CLI-0101", "sex": "f"}
    assert api.post("/api/endpoints/clinic/patients", headers=h, json=bad).status_code == 422
    assert new in api.get("/api/endpoints/clinic/patients", headers=h).json()


@pytest.mark.req("REQ-SEC-14")
def test_background_polling_does_not_hold_off_the_idle_lock(
    sim: LiveSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = TestClient(create_app(sim))
    h = sign_in(api, "clinic")
    start = time.monotonic()
    for step in range(1, 4):  # the app polls; nobody touches it
        monkeypatch.setattr(time, "monotonic", lambda s=step: start + s * IDLE_LOCK_S / 4)
        assert (
            api.get("/api/endpoints/clinic/messages", headers={**h, "x-active": "0"}).status_code
            == 200
        )
    monkeypatch.setattr(time, "monotonic", lambda: start + IDLE_LOCK_S + 1)
    assert (
        api.get("/api/endpoints/clinic/messages", headers={**h, "x-active": "0"}).status_code == 401
    )
    h = sign_in(api, "clinic")  # with someone typing, each poll counts as use
    for step in range(1, 5):
        t = start + IDLE_LOCK_S + 1 + step * IDLE_LOCK_S / 2
        monkeypatch.setattr(time, "monotonic", lambda t=t: t)
        assert (
            api.get("/api/endpoints/clinic/messages", headers={**h, "x-active": "1"}).status_code
            == 200
        )


def test_send_to_a_recipient_no_longer_in_the_directory_is_refused(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    sim.r.endpoints["clinic"].directory.pop("hospital")
    r = api.post(
        "/api/endpoints/clinic/messages",
        headers=sign_in(api, "clinic"),
        json={"to": "hospital", "text": "x"},
    )
    assert r.status_code == 422
