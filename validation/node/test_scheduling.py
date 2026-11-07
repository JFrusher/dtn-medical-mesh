import pytest

from node.bundle import Priority, encode_shard
from node.pki import Role
from validation.node.helpers import (
    ack_msg,
    frame,
    frames,
    make_node,
    make_shards,
    records,
    signed_ack,
    summary_from,
)


@pytest.mark.req("REQ-PROTO-03")
def test_acks_before_shards_and_priority_before_size() -> None:
    big_critical = make_shards(b"c", priority=Priority.CRITICAL, size=65536)
    small_routine = make_shards(b"r", size=4096)
    other = make_shards(b"o")
    node = make_node("M", Role.MULE, held=records(big_critical, small_routine))
    frame(node, "H", ack_msg(signed_ack(other[0].header.bundle_id)))
    sent = [f for f in frames(frame(node, "P", summary_from("P", "M"))) if f["t"] != "cert"]
    kinds = [f["t"] for f in sent]
    assert kinds[0] == "ack" and set(kinds[1:]) == {"shard"}
    ids = [f["s"] for f in sent[1:]]
    first_routine = min(
        i for i, s in enumerate(ids) if s in {encode_shard(x) for x in small_routine}
    )
    assert first_routine == len(big_critical)  # all critical shards go first, even though larger


@pytest.mark.req("REQ-PROTO-03")
def test_destination_peer_gets_bundle_it_is_closest_to_completing_first() -> None:
    near, far = make_shards(b"near", created=2), make_shards(b"far", created=1)
    node = make_node("M", Role.MULE, held=records(near, far))
    have = [s.shard_id for s in near[:3]]  # hub already holds 3 of near's k=4
    sent = [
        f["s"]
        for f in frames(frame(node, "H", summary_from("H", "M", ids=have)))
        if f["t"] == "shard"
    ]
    assert sent[0] in {encode_shard(s) for s in near[3:]}  # one more completes it
    to_mule = [
        f["s"] for f in frames(frame(node, "P", summary_from("P", "M"))) if f["t"] == "shard"
    ]
    assert to_mule[0] in {encode_shard(s) for s in far}  # not a destination: older first
