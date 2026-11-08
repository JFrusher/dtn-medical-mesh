from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

from node.core import T_DOCS
from node.events import StageUpdate, Store
from node.pki import Role, encode_doc, sign_doc
from node.update import encode_manifest, sign_manifest, stage_body
from simulation.runner import RunResult, build
from simulation.scenario import load_scenario
from simulation.updates import make_release
from validation.node.helpers import connect, frame, make_node

LINE = """
name: line
duration_s: 86400
tick_s: 2
nodes:
  - {id: HUB, role: hub, sa: SA_H, at: [0, 0]}
  - {id: SITE, role: site, sa: SA_S, at: [3000, 0]}
  - {id: RELAY, role: relay, at: [1500, 60]}
  - id: VAN
    role: mule
    route: {waypoints: [[0, 0], [3000, 0]], speed: 10, dwell_s: 300}
endpoints: [{name: hospital, node: HUB}, {name: clinic, node: SITE}]
"""


def line(tmp_path: Path) -> RunResult:
    f = tmp_path / "line.yaml"
    f.write_text(LINE)
    return build(load_scenario(str(f)), 1, tmp_path / "run")


def stage(r: RunResult, roles: tuple[str, ...]) -> None:
    manifest, payload = make_release(1, "0.3.0", "0.2.0")
    hub = r.world.nodes["HUB"]
    r.world.dispatch(
        hub, partial(StageUpdate, manifest=encode_manifest(manifest), payload=payload, stage=roles)
    )


def versions(r: RunResult) -> dict[str, str]:
    return {n: node.version for n, node in sorted(r.world.nodes.items())}


@pytest.mark.req("REQ-OPS-05")
def test_staged_rollout_relays_first_then_everyone(tmp_path: Path) -> None:
    r = line(tmp_path)
    stage(r, ("relay",))
    r.world.scheduler.run_until(4 * 3600)
    assert versions(r) == {"HUB": "0.2.0", "RELAY": "0.3.0", "SITE": "0.2.0", "VAN": "0.2.0"}
    assert r.world.nodes["SITE"].core.pkgs  # carried there, but not installed: not its stage
    stage(r, ("relay", "mule", "site", "hub"))
    r.world.scheduler.run_until(10 * 3600)
    assert set(versions(r).values()) == {"0.3.0"}
    hub = r.world.nodes["HUB"].core
    assert {rep.version for rep in hub.health.values()} == {"0.3.0"}  # the console can see it


@pytest.mark.req("REQ-OPS-05")
def test_a_release_that_fails_rolls_back_and_is_not_retried(tmp_path: Path) -> None:
    r = line(tmp_path)
    r.world.broken_versions.add("0.3.0")
    stage(r, ("relay",))
    r.world.scheduler.run_until(10 * 3600)
    assert r.world.nodes["RELAY"].version == "0.2.0"
    installs = [line for line in r.log if "INSTALL RELAY" in line]
    rollbacks = [line for line in r.log if "ROLLBACK RELAY" in line]
    assert len(installs) == 1 and len(rollbacks) == 1  # tried once, then left alone
    assert r.world.nodes["RELAY"].up


@pytest.mark.req("REQ-OPS-05", "REQ-SEC-07")
def test_a_hub_cannot_push_software_the_release_key_did_not_sign() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    manifest, _ = make_release(1, "9.9.9", "0.0.0")
    forged = sign_manifest(replace(manifest, version="9.9.9"), hub.identity.sign)  # hub's key
    assert hub._publish("update", stage_body(forged, ("relay",))) == []
    assert hub.dropped["bad_update"] == 1
    relay = make_node("R", Role.RELAY, None)
    connect(relay, "H")
    doc = sign_doc(hub.identity, "update", 1, stage_body(forged, ("relay",)))
    actions = frame(relay, "H", {"t": "doc", "d": encode_doc(doc)})
    assert not any(isinstance(a, Store) and a.table == T_DOCS for a in actions)
    assert relay.dropped["bad_update"] == 1
    with pytest.raises(ValueError):
        stage_body(manifest, ("satellite",))  # a stage can only name signed roles
