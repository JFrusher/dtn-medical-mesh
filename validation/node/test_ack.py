from dataclasses import replace

import pytest

from node.events import Delete, Delivered, EndpointAck
from node.pki import Role
from validation.node.helpers import (
    ack_msg,
    connect,
    frame,
    frames,
    identity,
    inject,
    make_node,
    make_shards,
    records,
    shard_msg,
    signed_ack,
)


@pytest.mark.req("REQ-PROTO-06")
def test_ack_purges_spreads_and_blocks_resurrection() -> None:
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    mule = make_node("M", Role.MULE, held=records(shards))
    connect(mule, "OTHER", ids=[s.shard_id for s in shards])
    ack = signed_ack(bid)
    out = frame(mule, "H", ack_msg(ack))
    assert {a.key for a in out if isinstance(a, Delete)} == {s.shard_id for s in shards}
    forwarded = [f for f in frames(out) if f["t"] == "ack"]
    assert forwarded == [ack_msg(ack)]  # passed on to the other open contact
    assert frame(mule, "X", shard_msg(shards[0])) == []
    assert mule.shards == {}


@pytest.mark.req("REQ-PROTO-06")
def test_origin_is_told_and_destination_signs_on_endpoint_ack() -> None:
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    clinic = make_node("C", Role.SITE, "SA_C")
    inject(clinic, shards)
    connect(clinic, "M", acks=[bid])
    assert clinic.shards  # a summary only lists ACKs; the ACK frame itself must arrive
    out = frame(clinic, "M", ack_msg(signed_ack(bid)), mono=1)
    assert not clinic.shards and Delivered(bundle_id=bid) in out

    hosp = make_node("H", Role.HUB, "SA_H", records(shards))
    out = hosp.handle(EndpointAck(mono=0, wall=None, bundle_id=bid))
    assert len([a for a in out if isinstance(a, Delete)]) == len(shards)
    assert hosp.acks[bid].signer == hosp.identity.cert.cert_id
    assert not any(isinstance(a, Delivered) for a in out)  # hospital is not the origin


@pytest.mark.req("REQ-PROTO-06", "REQ-SAFE-01")
@pytest.mark.parametrize("case", ["unsigned", "tampered", "signer outside dst SA", "relay signer"])
def test_forged_acks_purge_nothing(case: str) -> None:
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    mule = make_node("M", Role.MULE, held=records(shards))
    ack = signed_ack(bid)
    if case == "unsigned":
        ack = replace(ack, sig=b"")
    elif case == "tampered":
        ack = replace(signed_ack(b"\x00" * 32), bundle_id=bid)
    elif case == "signer outside dst SA":
        ack = signed_ack(bid, dst_sa="SA_C", signer=identity("C", Role.SITE, "SA_C"))
    else:
        relay = identity("R", Role.RELAY, None)
        mule.certs[relay.cert.cert_id] = relay.cert
        ack = signed_ack(bid, signer=relay)
    assert frame(mule, "P", ack_msg(ack)) == []
    assert len(mule.shards) == len(shards) and mule.dropped["bad_ack"] == 1


@pytest.mark.req("REQ-PROTO-06", "REQ-SAFE-01")
def test_forged_ack_seen_first_cannot_block_the_real_bundle() -> None:
    """A valid site in SA_C 'acks' a bundle bound for SA_H before any shard arrives."""
    shards = make_shards(b"x")
    bid = shards[0].header.bundle_id
    mule = make_node("M", Role.MULE)
    forged = signed_ack(bid, dst_sa="SA_C", signer=identity("C", Role.SITE, "SA_C"))
    frame(mule, "P", ack_msg(forged))
    assert bid in mule.acks  # accepted: nothing held yet contradicts it
    assert frame(mule, "P", shard_msg(shards[0]))  # the real shard still gets in
