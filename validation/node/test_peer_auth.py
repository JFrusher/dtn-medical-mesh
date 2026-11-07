import pytest

from node.pki import Role, encode_cert
from validation.node.helpers import (
    NOW,
    frame,
    frames,
    identity,
    make_node,
    make_shards,
    records,
    summary_from,
)


def offered(node, msg) -> bool:  # type: ignore[no-untyped-def]
    return any(f["t"] == "shard" for f in frames(frame(node, "P", msg)))


@pytest.mark.req("REQ-SEC-06")
def test_authentic_peer_gets_offers() -> None:
    node = make_node("M", Role.MULE, held=records(make_shards(b"x")))
    assert offered(node, summary_from("P", "M"))


@pytest.mark.req("REQ-SEC-06")
@pytest.mark.parametrize(
    "case", ["wrong subject", "meant for another", "stale", "tampered", "revoked"]
)
def test_unauthenticated_peer_gets_nothing(case: str) -> None:
    node = make_node("M", Role.MULE, held=records(make_shards(b"x")))
    msg = summary_from("P", "M")
    if case == "wrong subject":
        msg = summary_from("Q", "M")  # Q's valid cert, but the radio says this is P
    elif case == "meant for another":
        msg = summary_from("P", "SOMEONE_ELSE")
    elif case == "stale":
        msg["time"] = NOW - 4 * 3600
    elif case == "tampered":
        msg["ids"] = [b"forged"]
    elif case == "revoked":
        node.revoked = frozenset({"P"})
    assert not offered(node, msg)
    assert node.dropped["unauthenticated_peer"] == 1


@pytest.mark.req("REQ-SEC-06")
def test_unregistered_device_gets_nothing() -> None:
    rogue = identity("P", Role.MULE, None)  # self-made cert, not issued by the hub
    from dataclasses import replace

    msg = summary_from("P", "M")
    msg["from"] = encode_cert(replace(rogue.cert, issuer="NOT_A_HUB"))
    assert not offered(make_node("M", Role.MULE, held=records(make_shards(b"x"))), msg)
