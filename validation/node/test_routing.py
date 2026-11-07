import pytest

from node.bundle import Shard
from node.core import NodeCore, _summary_signing_bytes
from node.events import Delete, Timer
from node.pki import Role
from node.routing import BETA, DELTA, GAMMA, P_ENCOUNTER, TIME_UNIT_S, WARM_AFTER, Router
from validation.node.helpers import (
    connect,
    frame,
    frames,
    identity,
    make_node,
    make_shards,
    records,
    shard_msg,
    summary_from,
)


@pytest.mark.req("REQ-PROTO-07")
def test_prophet_equations() -> None:
    r = Router()
    r.encounter("SA_H", {}, now_s=0)
    assert r.p["SA_H"] == pytest.approx(P_ENCOUNTER * (1 - DELTA))
    first = r.p["SA_H"]
    r.encounter("SA_H", {}, now_s=0)
    assert r.p["SA_H"] == pytest.approx(first + (1 - DELTA - first) * P_ENCOUNTER)
    r.encounter(None, {"SA_X": 0.8}, now_s=0)  # a mule that often meets SA_X
    assert r.p["SA_X"] == pytest.approx(P_ENCOUNTER * 0.8 * BETA)
    before = dict(r.p)
    r.encounter(None, {}, now_s=10 * TIME_UNIT_S)
    assert r.p["SA_H"] == pytest.approx(before["SA_H"] * GAMMA**10)


@pytest.mark.req("REQ-PROTO-07")
def test_epidemic_until_warm_then_prophet() -> None:
    r = Router()
    assert r.forward("SA_H", None, {})  # cold: copy to anyone
    for _ in range(WARM_AFTER):
        r.encounter("SA_H", {}, now_s=0)
    assert r.warm("SA_H")
    assert not r.forward("SA_H", None, {"SA_H": 0.1})  # peer worse than us
    assert r.forward("SA_H", None, {"SA_H": 0.999})  # peer better
    assert r.forward("SA_H", "SA_H", {})  # peer is the destination


@pytest.mark.req("REQ-PROTO-05", "REQ-PROTO-07")
def test_better_for_custody_and_round_trip() -> None:
    r = Router()
    assert not r.better("SA_H", None, {"SA_H": 0.9})  # cold: keep copies (epidemic)
    assert r.better("SA_H", "SA_H", {})
    for _ in range(WARM_AFTER):
        r.encounter(None, {"SA_H": 0.5}, now_s=0)
    assert r.better("SA_H", None, {"SA_H": 0.9})
    assert Router.decode(r.encode()) == r


def warm_mule(shards: list[Shard]) -> NodeCore:
    mule = make_node("M", Role.MULE, held=records(shards))
    for _ in range(WARM_AFTER):
        mule.router.encounter(None, {"SA_H": 0.5}, mule.clock_s)
    return mule


def offered(node: NodeCore, peer: str, prophet: dict[str, float]) -> int:
    msg = summary_from(peer, node.config.node_id)
    msg["prophet"] = prophet
    me = identity("H", Role.HUB, "SA_H") if peer == "H" else identity(peer, Role.MULE, None)
    msg["sig"] = me.sign(_summary_signing_bytes(msg))
    return sum(f["t"] == "shard" for f in frames(frame(node, peer, msg)))


@pytest.mark.req("REQ-PROTO-07")
def test_warm_node_forwards_only_to_better_peers_or_the_destination() -> None:
    shards = make_shards(b"x")
    assert offered(warm_mule(shards), "P", {"SA_H": 0.01}) == 0
    assert offered(warm_mule(shards), "P", {"SA_H": 0.99}) == len(shards)
    assert offered(warm_mule(shards), "H", {}) == len(shards)


@pytest.mark.req("REQ-PROTO-05", "REQ-PROTO-07")
def test_relay_drops_after_custody_by_a_better_mule() -> None:
    shards = make_shards(b"x")
    mule = warm_mule(shards)
    offered(mule, "P", {"SA_H": 0.99})  # P is better and is now connected
    taker = make_node("P", Role.MULE)
    connect(taker, "M")
    for s in shards:
        frame(taker, "M", shard_msg(s))
    issued = frames(taker.handle(Timer(mono=1, wall=None, name="receipt:M")))
    [receipt] = [f for f in issued if f["t"] == "receipt"]
    out = frame(mule, "P", receipt)
    assert {a.key for a in out if isinstance(a, Delete)} == {s.shard_id for s in shards}
