from dataclasses import replace

import cbor2
import pytest

from endpoint.directory import DirectoryEntry, directory_body, verified_entries
from node.bundle import FormatError
from node.events import DirectoryUpdate, PublishDirectory
from node.pki import Role, decode_doc, encode_cert, encode_doc
from validation.node.helpers import NOW, TEST_HUB, TEST_ROOT, frame, frames, make_node, summary_from

ENTRIES = [DirectoryEntry("ward-3", "SA_H", b"e" * 32, b"s" * 32)]


def published() -> tuple[bytes, bytes]:
    hub = make_node("H", Role.HUB, "SA_H")
    out = hub.handle(PublishDirectory(mono=0, wall=None, entries=tuple(directory_body(ENTRIES))))
    [update] = [a for a in out if isinstance(a, DirectoryUpdate)]  # the hub's own endpoint
    return update.doc, update.issuer


@pytest.mark.req("REQ-SEC-04")
def test_directory_spreads_to_sites_and_verifies_at_endpoint() -> None:
    doc, issuer = published()
    assert verified_entries(doc, issuer, TEST_ROOT.public_key(), NOW) == ENTRIES
    site = make_node("C", Role.SITE, "SA_C")
    out = frame(site, "H", {"t": "doc", "d": doc})
    assert [a.doc for a in out if isinstance(a, DirectoryUpdate)] == [doc]
    mule = make_node("M", Role.MULE)
    frame(mule, "H", {"t": "doc", "d": doc})
    relayed = [f for f in frames(frame(mule, "P", summary_from("P", "M"))) if f["t"] == "doc"]
    assert relayed == [{"t": "doc", "d": doc}]  # mules carry it without an endpoint


def test_forged_directory_rejected_everywhere() -> None:
    doc, issuer = published()
    d = decode_doc(doc)
    evil = [DirectoryEntry("ward-3", "SA_EVIL", b"x" * 32, b"y" * 32)]
    forged = encode_doc(replace(d, body=cbor2.dumps(directory_body(evil))))
    with pytest.raises(FormatError):
        verified_entries(forged, issuer, TEST_ROOT.public_key(), NOW)
    site = make_node("C", Role.SITE, "SA_C")
    assert frame(site, "H", {"t": "doc", "d": forged}) == []
    assert site.dropped["bad_doc"] == 1
    assert encode_cert(TEST_HUB) == issuer
