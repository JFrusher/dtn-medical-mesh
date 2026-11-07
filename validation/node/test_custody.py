import hashlib
from dataclasses import replace

import pytest

from node.bundle import decode_receipt, encode_receipt
from node.events import ContactDown, Delete, Timer
from node.hw.radio import RadioKind
from node.pki import Role
from validation.node.helpers import (
    connect,
    frame,
    frames,
    inject,
    make_node,
    make_shards,
    records,
    shard_msg,
    summary_from,
)


def receive_all(node, peer: str, shards) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    for s in shards:
        frame(node, peer, shard_msg(s))
    return frames(node.handle(Timer(mono=1, wall=None, name=f"receipt:{peer}")))


@pytest.mark.req("REQ-PROTO-04")
def test_batched_receipt_is_signed_and_hash_chained() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    a, b = make_shards(b"a"), make_shards(b"b")
    [first] = [f for f in receive_all(hub, "M", a) if f["t"] == "receipt"]
    [second] = [f for f in receive_all(hub, "M", b) if f["t"] == "receipt"]
    r1, r2 = decode_receipt(first["r"]), decode_receipt(second["r"])  # type: ignore[arg-type]
    assert set(r1.shard_ids) == {s.shard_id for s in a} and r1.sender == "M"
    assert r1.prev == bytes(32) and r2.prev == hashlib.sha256(first["r"]).digest()  # type: ignore[arg-type]
    assert hub.ledger_seq == 2


@pytest.mark.req("REQ-PROTO-04", "REQ-PROTO-01")
def test_receipt_owed_after_link_drop_is_delivered_at_next_meeting() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    for s in make_shards(b"x"):
        frame(hub, "M", shard_msg(s))
    hub.handle(ContactDown(mono=0.2, wall=None, peer_id="M", radio=RadioKind.WIFI))
    assert len(hub.outbox) == 1 and hub.ledger_seq == 1
    # meeting again: the hub sends what it owes as soon as it accepts M's summary

    owed = [f for f in frames(frame(hub, "M", summary_from("M", "H"))) if f["t"] == "receipt"]
    assert len(owed) == 1


@pytest.mark.req("REQ-PROTO-05")
def test_relay_drops_after_destination_custody_but_origin_keeps_until_ack() -> None:
    shards = make_shards(b"x")
    mule = make_node("M", Role.MULE, held=records(shards))
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    [receipt] = [f for f in receive_all(hub, "M", shards) if f["t"] == "receipt"]
    out = frame(mule, "H", receipt)
    assert {a.key for a in out if isinstance(a, Delete)} == {s.shard_id for s in shards}

    clinic = make_node("C", Role.SITE, "SA_C")
    inject(clinic, shards)
    hub2 = make_node("H", Role.HUB, "SA_H")
    connect(hub2, "C")
    [receipt] = [f for f in receive_all(hub2, "C", shards) if f["t"] == "receipt"]
    assert not [a for a in frame(clinic, "H", receipt) if isinstance(a, Delete)]


@pytest.mark.req("REQ-PROTO-05")
def test_relay_keeps_copy_when_custodian_is_just_another_mule() -> None:
    shards = make_shards(b"x")
    mule = make_node("M", Role.MULE, held=records(shards))
    other = make_node("M2", Role.MULE)
    connect(other, "M")
    [receipt] = [f for f in receive_all(other, "M", shards) if f["t"] == "receipt"]
    assert not [a for a in frame(mule, "M2", receipt) if isinstance(a, Delete)]


@pytest.mark.req("REQ-PROTO-04")
def test_forged_or_misaddressed_receipt_deletes_nothing() -> None:
    shards = make_shards(b"x")
    mule = make_node("M", Role.MULE, held=records(shards))
    hub = make_node("H", Role.HUB, "SA_H")
    connect(hub, "M")
    [f] = [f for f in receive_all(hub, "M", shards) if f["t"] == "receipt"]
    r = decode_receipt(f["r"])  # type: ignore[arg-type]
    forged = replace(r, shard_ids=r.shard_ids[:1] * 2)
    assert frame(mule, "H", {"t": "receipt", "r": encode_receipt(forged)}) == []
    assert mule.dropped["bad_receipt"] == 1
    elsewhere = make_node("M9", Role.MULE, held=records(shards))
    assert frame(elsewhere, "H", f) == []  # addressed to M, not M9
