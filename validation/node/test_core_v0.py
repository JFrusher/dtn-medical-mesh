import cbor2

from node.bundle import Priority, decode_bundle, encode_shard
from node.core import NodeConfig, NodeCore
from node.events import ContactUp, Deliver, FrameReceived, Store
from node.pki import Role
from validation.node.helpers import (
    W,
    connect,
    frame,
    frames,
    inject,
    make_shards,
    records,
    shard_msg,
)


def test_inject_stores_every_shard_and_contact_sends_summary() -> None:
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    shards = make_shards(b"x")
    actions = inject(clinic, shards)
    bid = shards[0].header.bundle_id
    assert actions[0] == Store(table="origin", key=bid, value=b"")
    assert [a.key for a in actions[1:] if isinstance(a, Store)] == [s.shard_id for s in shards]
    up = clinic.handle(ContactUp(mono=1, wall=None, peer_id="M", radio=W))
    assert frames(up) == [{"t": "summary", "ids": sorted(s.shard_id for s in shards), "acks": []}]


def test_offers_only_missing_shards_most_urgent_first() -> None:
    routine, critical = make_shards(b"a"), make_shards(b"b", priority=Priority.CRITICAL)
    node = NodeCore(NodeConfig("M", Role.MULE, None), records(routine, critical))
    have = [s.shard_id for s in routine[:3]]
    sent = frames(frame(node, "P", {"t": "summary", "ids": have, "acks": []}))
    expected = critical + routine[3:]
    assert [f["s"] for f in sent] == [encode_shard(s) for s in expected]


def test_destination_rebuilds_from_any_k_and_delivers_once() -> None:
    shards = make_shards(b"x")  # k=4, m=2
    hosp = NodeCore(NodeConfig("H", Role.HUB, "SA_H"), {})
    delivered = []
    for s in [shards[5], shards[1], shards[4], shards[2], shards[0]]:
        delivered += [a for a in frame(hosp, "M", shard_msg(s)) if isinstance(a, Deliver)]
    [d] = delivered
    assert decode_bundle(d.bundle).payload == b"".join(s.data for s in shards[:4])


def test_relay_never_delivers() -> None:
    mule = NodeCore(NodeConfig("M", Role.MULE, None), {})
    for s in make_shards(b"x"):
        assert not [a for a in frame(mule, "C", shard_msg(s)) if isinstance(a, Deliver)]


def test_malformed_frames_are_dropped_and_counted() -> None:
    node = NodeCore(NodeConfig("M", Role.MULE, None), {})
    junk = [b"\xff", cbor2.dumps({"t": "nope"}), cbor2.dumps({"t": "shard", "s": b"x"})]
    for j in junk:
        assert node.handle(FrameReceived(mono=0, wall=None, peer_id="P", radio=W, frame=j)) == []
    assert node.dropped["malformed"] == 3


def test_new_shards_go_to_already_connected_peer() -> None:
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    connect(clinic, "M")
    shards = make_shards(b"late")
    assert len(frames(inject(clinic, shards))) == len(shards)
    assert inject(clinic, shards) == []


def test_shard_is_not_echoed_back_to_its_sender() -> None:
    mule = NodeCore(NodeConfig("M", Role.MULE, None), {})
    connect(mule, "C")
    assert frames(frame(mule, "C", shard_msg(make_shards(b"x")[0]))) == []
