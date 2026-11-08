from pathlib import Path

import pytest

from node.core import T_HEALTH
from node.events import HealthTick
from node.health import decode_report, encode_report
from node.pki import Role
from simulation.runner import run
from simulation.scenario import load_scenario
from validation.node.helpers import connect, frame, make_node

TICK = HealthTick(mono=1, wall=None, battery_pct=80.0, temp_c=31.5, version="0.2.0")


@pytest.mark.req("REQ-OPS-01")
def test_report_is_signed_stored_and_numbered() -> None:
    mule = make_node("M", Role.MULE, None)
    first = mule.handle(TICK)
    second = mule.handle(TICK)
    r1, r2 = (decode_report(a.value) for a in (*first, *second) if a.table == T_HEALTH)  # type: ignore[union-attr]
    assert (r1.seq, r2.seq) == (1, 2) and r2.node == "M" and r2.temp_c == 31.5
    assert r2.version == "0.2.0" and r2.battery_pct == 80.0 and r2.light in ("green", "amber")


@pytest.mark.req("REQ-OPS-01")
def test_hub_accepts_genuine_reports_and_refuses_forged_or_stale_ones() -> None:
    mule = make_node("M", Role.MULE, None)
    mule.handle(TICK)
    report = mule.health["M"]
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    frame(hub, "M", {"t": "health", "r": encode_report(report)})
    assert hub.health["M"] == report
    forged = encode_report(report.__class__(**{**report.__dict__, "seq": 9, "battery_pct": 1.0}))
    frame(hub, "M", {"t": "health", "r": forged})
    assert hub.health["M"].seq == 1 and hub.dropped["bad_health"] == 1
    frame(hub, "M", {"t": "health", "r": encode_report(report)})  # replay: ignored
    assert hub.health["M"] == report


@pytest.mark.req("REQ-OPS-01", "REQ-SIM-01")
def test_every_node_reports_to_the_hub_over_the_mesh(tmp_path: Path) -> None:
    result = run(load_scenario("skeleton"), 1, tmp_path)
    hub = result.world.nodes["HOSPITAL"].core
    assert set(hub.health) == set(result.world.nodes)  # the clinic's came via the ambulance
    clinic = hub.health["CLINIC_01"]
    assert clinic.seq >= 2 and clinic.version == "0.2.0" and clinic.temp_c is not None
    assert clinic.battery_pct is None  # mains powered
