from dataclasses import replace

import cbor2
import pytest

from node.events import Revoke
from node.pki import Role, encode_doc
from validation.node.helpers import (
    connect,
    frame,
    frames,
    make_node,
    make_shards,
    records,
    shard_msg,
    summary_from,
)


@pytest.mark.req("REQ-SEC-07")
def test_revocation_spreads_first_and_blocks_new_shards_from_revoked_injector() -> None:
    hub = make_node("H", Role.HUB, "SA_H", records(make_shards(b"old")))
    hub.handle(Revoke(mono=0, wall=None, subject="C"))
    sent = [f for f in frames(frame(hub, "P", summary_from("P", "H"))) if f["t"] != "cert"]
    assert sent[0]["t"] == "doc"  # straight after the certs needed to check it, before shards

    mule = make_node("M", Role.MULE)
    assert frame(mule, "P", {"t": "doc", "d": sent[0]["d"]})
    assert frame(mule, "P", shard_msg(make_shards(b"new")[0])) == []
    assert mule.dropped["bad_signature"] == 1


@pytest.mark.req("REQ-SEC-07")
def test_revoked_peer_is_refused_and_newer_lists_win() -> None:
    mule = make_node("M", Role.MULE, held=records(make_shards(b"x")))
    hub = make_node("H", Role.HUB, "SA_H")
    hub.handle(Revoke(mono=0, wall=None, subject="P"))
    first = hub.docs[f"crl:{hub.identity.cert.cert_id}"]
    hub.handle(Revoke(mono=0, wall=None, subject="Q"))
    second = hub.docs[f"crl:{hub.identity.cert.cert_id}"]
    assert second.seq == 2 and mule.revoked == frozenset()

    frame(mule, "H", {"t": "doc", "d": encode_doc(second)})
    assert mule.revoked == {"P", "Q"}
    assert frame(mule, "H", {"t": "doc", "d": encode_doc(first)}) == []  # older ignored
    connect(mule, "P")
    assert mule.dropped["unauthenticated_peer"] == 1


@pytest.mark.req("REQ-SEC-07")
def test_forged_list_rejected_and_only_hubs_revoke() -> None:
    mule = make_node("M", Role.MULE)
    hub = make_node("H", Role.HUB, "SA_H")
    hub.handle(Revoke(mono=0, wall=None, subject="P"))
    crl = hub.docs[f"crl:{hub.identity.cert.cert_id}"]
    forged = replace(crl, body=cbor2.dumps(["P", "EVERYONE_ELSE"]))
    assert frame(mule, "H", {"t": "doc", "d": encode_doc(forged)}) == []
    assert mule.dropped["bad_doc"] == 1 and not mule.revoked
    with pytest.raises(ValueError):
        mule.handle(Revoke(mono=0, wall=None, subject="X"))
