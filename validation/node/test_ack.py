import pytest

from node.core import NodeConfig, NodeCore, Role
from node.events import Delete, Delivered, EndpointAck
from validation.node.helpers import connect, frame, frames, inject, make_shards, records, shard_msg


@pytest.mark.req("REQ-PROTO-06")
def test_ack_purges_spreads_and_blocks_resurrection() -> None:
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    mule = NodeCore(NodeConfig("M", Role.MULE, None), records(shards))
    connect(mule, "OTHER", ids=[s.shard_id for s in shards])
    out = frame(mule, "H", {"t": "ack", "id": bid})
    assert {a.key for a in out if isinstance(a, Delete)} == {s.shard_id for s in shards}
    assert frames(out) == [{"t": "ack", "id": bid}]  # forwarded to the other open contact
    assert frame(mule, "X", shard_msg(shards[0])) == []
    assert mule.shards == {}


@pytest.mark.req("REQ-PROTO-06")
def test_origin_is_told_and_destination_purges_on_endpoint_ack() -> None:
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    inject(clinic, shards)
    connect(clinic, "M", acks=[bid])
    assert clinic.shards  # a summary only lists ACKs; the ACK frame itself must arrive
    out = frame(clinic, "M", {"t": "ack", "id": bid}, mono=1)
    assert not clinic.shards and Delivered(bundle_id=bid) in out

    hosp = NodeCore(NodeConfig("H", Role.HUB, "SA_H"), records(shards))
    out = hosp.handle(EndpointAck(mono=0, wall=None, bundle_id=bid))
    assert len([a for a in out if isinstance(a, Delete)]) == len(shards)
    assert not any(isinstance(a, Delivered) for a in out)  # hospital is not the origin
