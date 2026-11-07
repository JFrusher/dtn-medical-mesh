import hashlib

import pytest

from node.bundle import FormatError
from node.events import ContactDown, SetTimer, Timer
from node.hw.radio import RadioKind
from node.pki import Role
from node.summary import Bloom
from validation.node.helpers import (
    connect,
    frame,
    frames,
    make_node,
    make_shards,
    records,
    summary_from,
)


@pytest.mark.req("REQ-PROTO-02")
def test_no_false_negatives_and_about_one_percent_false_positives() -> None:
    held = [hashlib.sha256(i.to_bytes(4)).digest() for i in range(2000)]
    bloom = Bloom.decode(Bloom.of(held).encode())
    assert all(h in bloom for h in held)
    others = [hashlib.sha256(b"x" + i.to_bytes(4)).digest() for i in range(20000)]
    rate = sum(o in bloom for o in others) / len(others)
    assert rate < 0.02
    assert len(Bloom.of(held).encode()) < 2600  # ~1.2 bytes per item, vs 33 for exact IDs


def test_empty_and_hostile_filters() -> None:
    assert b"x" not in Bloom.of([])
    with pytest.raises(FormatError):
        Bloom.decode(Bloom(64, 3).encode()[:-1])
    with pytest.raises(FormatError):
        Bloom(10**9, 3)


@pytest.mark.req("REQ-PROTO-02")
def test_bloom_false_positive_only_delays_until_exact_list() -> None:
    shards = make_shards(b"x")
    mule = make_node("M", Role.MULE, held=records(shards))
    lucky = shards[2].shard_id  # pretend the peer's filter falsely claims this one
    out = frame(mule, "P", summary_from("P", "M", ids=[lucky]))
    sent = {f["s"] for f in frames(out) if f["t"] == "shard"}
    assert len(sent) == len(shards) - 1
    [timer] = [a for a in out if isinstance(a, SetTimer)]
    assert timer.name == "exact:P"
    late = frames(frame(mule, "P", {"t": "exact", "ids": [], "acks": []}))
    assert [f["t"] for f in late] == ["shard"]  # the hidden shard follows within the contact


@pytest.mark.req("REQ-PROTO-02")
def test_exact_list_sent_only_while_contact_lasts() -> None:
    mule = make_node("M", Role.MULE, held=records(make_shards(b"x")))
    connect(mule, "P")
    sent = frames(mule.handle(Timer(mono=2, wall=None, name="exact:P")))
    assert [f["t"] for f in sent] == ["exact"] and len(sent[0]["ids"]) == 6  # type: ignore[arg-type]
    mule.handle(ContactDown(mono=3, wall=None, peer_id="P", radio=RadioKind.WIFI))
    assert mule.handle(Timer(mono=4, wall=None, name="exact:P")) == []
