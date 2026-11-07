from dataclasses import replace

import pytest

from node.bundle import Header, Priority
from node.pki import Role, encode_cert
from validation.node.helpers import (
    CLINIC,
    connect,
    frame,
    frames,
    identity,
    inject,
    make_node,
    make_shards,
    shard_msg,
    summary_from,
)


def resign(h: Header, signer: object = CLINIC) -> Header:
    h = replace(h, sig=b"")
    return replace(h, sig=signer.sign(h.signing_bytes()))  # type: ignore[attr-defined]


@pytest.mark.req("REQ-SEC-04")
@pytest.mark.parametrize(
    "case", ["tampered priority", "tampered destination", "unsigned", "injector outside source SA"]
)
def test_relays_drop_shards_with_bad_header_signatures(case: str) -> None:
    mule = make_node("M", Role.MULE)
    s = make_shards(b"x")[0]
    h = s.header
    if case == "tampered priority":
        h = replace(h, priority=Priority.CRITICAL)
    elif case == "tampered destination":
        h = replace(h, dst_sa="SA_EVIL")
    elif case == "unsigned":
        h = replace(h, sig=b"")
    else:
        other = identity("C2", Role.SITE, "SA_OTHER")
        mule.certs[other.cert.cert_id] = other.cert
        h = resign(replace(h, injector=other.cert.cert_id), other)
    assert frame(mule, "P", shard_msg(replace(s, header=h))) == []
    assert mule.dropped["bad_signature"] == 1
    assert frame(mule, "P", shard_msg(s))  # the genuine shard is accepted


@pytest.mark.req("REQ-SEC-04")
def test_unknown_injector_dropped_until_its_cert_arrives() -> None:
    mule = make_node("M", Role.MULE)
    del mule.certs[CLINIC.cert.cert_id]
    s = make_shards(b"x")[0]
    assert frame(mule, "P", shard_msg(s)) == []
    frame(mule, "P", {"t": "cert", "c": encode_cert(CLINIC.cert)})
    assert frame(mule, "P", shard_msg(s))


@pytest.mark.req("REQ-SEC-04")
def test_site_signs_on_inject_and_sends_certs_before_shards() -> None:
    site = make_node("C2", Role.SITE, "SA_C")
    shards = make_shards(b"x")
    inject(site, shards)
    [held] = {s.header for s in site.shards.values()}
    assert held.injector == site.identity.cert.cert_id and held.sig != shards[0].header.sig
    connect(site, "M")
    kinds = [f["t"] for f in frames(frame(site, "M", summary_from("M", "C2")))]
    assert kinds[:2] == ["cert", "cert"] and set(kinds[2:]) == {"shard"}


def test_injecting_for_another_source_sa_fails_loudly() -> None:
    site = make_node("X", Role.SITE, "SA_X")
    with pytest.raises(ValueError):
        inject(site, make_shards(b"x"))
