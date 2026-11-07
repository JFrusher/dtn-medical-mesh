import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interface.app import create_app
from interface.telemetry import NodeView, Telemetry
from simulation.live import LiveSim
from simulation.runner import build
from simulation.scenario import load_scenario


@pytest.fixture
def sim(tmp_path: Path) -> LiveSim:
    return LiveSim(build(load_scenario("skeleton"), seed=1, data_dir=tmp_path), speed=60)


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
    photo = base64.b64encode(b"\xff\xd8" + bytes(3000)).decode()
    r = api.post(
        "/api/endpoints/clinic/messages",
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
        m for m in api.get("/api/endpoints/clinic/messages").json() if m["id"] == r.json()["id"]
    ]
    assert mine[0]["status"] == "in transit"
    for _ in range(80):  # 80 x 60 s of virtual time
        sim.advance(1)
    got = [
        m for m in api.get("/api/endpoints/hospital/messages").json() if m["text"] == "query sepsis"
    ]
    assert got and "Heart rate 124" in got[0]["summary"] and "chart.jpg" in got[0]["summary"]
    sent = [
        m for m in api.get("/api/endpoints/clinic/messages").json() if m["id"] == r.json()["id"]
    ]
    assert sent[0]["status"] == "delivered"


def test_bad_requests_are_explained(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    assert api.get("/api/endpoints/nobody/messages").status_code == 404
    r = api.post("/api/endpoints/clinic/messages", json={"to": "nobody", "text": "x"})
    assert r.status_code == 422 and "directory" in r.json()["detail"]
    bad = {"to": "hospital", "text": "x", "vitals": [{"code": "mood", "value": 1}]}
    assert api.post("/api/endpoints/clinic/messages", json=bad).status_code == 422


def test_controls_and_live_stream(sim: LiveSim) -> None:
    api = TestClient(create_app(sim))
    api.post("/api/control", json={"speed": 600, "paused": True})
    before = sim.now
    sim.advance(1)
    assert sim.now == before and sim.speed == 600
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["paused"] is True
