import cbor2
import pytest

from node.bundle import encode_bundle
from node.core import NodeConfig, NodeCore, Role
from node.events import ContactUp, Delete, Delivered, EndpointAck, FrameReceived, Inject
from node.hw.radio import RadioKind
from validation.node.test_core_v0 import bundle, frames

W = RadioKind.WIFI


def connect(node: NodeCore, peer: str, ids: list[bytes], acks: list[bytes]) -> None:
    node.handle(ContactUp(mono=0, wall=None, peer_id=peer, radio=W))
    summary = cbor2.dumps({"t": "summary", "ids": ids, "acks": acks})
    node.handle(FrameReceived(mono=0, wall=None, peer_id=peer, radio=W, frame=summary))


@pytest.mark.req("REQ-PROTO-06")
def test_ack_purges_spreads_and_blocks_resurrection() -> None:
    b = bundle(b"x")
    bid = b.header.bundle_id
    mule = NodeCore(NodeConfig("M", Role.MULE, None), {"bundles": {bid: encode_bundle(b)}})
    connect(mule, "OTHER", ids=[bid], acks=[])
    ack = cbor2.dumps({"t": "ack", "id": bid})
    out = mule.handle(FrameReceived(mono=1, wall=None, peer_id="H", radio=W, frame=ack))
    assert Delete(table="bundles", key=bid) in out
    assert frames(out) == [{"t": "ack", "id": bid}]  # forwarded to the other open contact
    frame = cbor2.dumps({"t": "bundle", "b": encode_bundle(b)})
    assert mule.handle(FrameReceived(mono=2, wall=None, peer_id="X", radio=W, frame=frame)) == []
    assert bid not in mule.bundles


@pytest.mark.req("REQ-PROTO-06")
def test_origin_is_told_and_destination_purges_on_endpoint_ack() -> None:
    b = bundle(b"x")
    bid = b.header.bundle_id
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    clinic.handle(Inject(mono=0, wall=None, bundle=encode_bundle(b)))
    connect(clinic, "M", ids=[], acks=[bid])
    assert bid in clinic.bundles  # a summary only lists ACKs; the ACK frame itself must arrive
    ack = cbor2.dumps({"t": "ack", "id": bid})
    out = clinic.handle(FrameReceived(mono=1, wall=None, peer_id="M", radio=W, frame=ack))
    assert bid not in clinic.bundles and Delivered(bundle_id=bid) in out

    hosp = NodeCore(NodeConfig("H", Role.HUB, "SA_H"), {"bundles": {bid: encode_bundle(b)}})
    out = hosp.handle(EndpointAck(mono=0, wall=None, bundle_id=bid))
    assert Delete(table="bundles", key=bid) in out
    assert not any(isinstance(a, Delivered) for a in out)  # hospital is not the origin
