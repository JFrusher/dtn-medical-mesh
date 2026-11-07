import cbor2

from node.bundle import Bundle, Header, Priority, encode_bundle, make_bundle_id
from node.core import NodeConfig, NodeCore, Role
from node.events import Action, ContactUp, Deliver, FrameReceived, Inject, SendFrame, Store
from node.hw.radio import RadioKind

W = RadioKind.WIFI


def bundle(payload: bytes, dst: str = "SA_H", prio: Priority = Priority.ROUTINE) -> Bundle:
    h = Header(make_bundle_id(payload, 1), "SA_C", dst, prio, 1, 3600)
    return Bundle(h, payload)


def frames(actions: list[Action]) -> list[dict[str, object]]:
    return [cbor2.loads(a.frame) for a in actions if isinstance(a, SendFrame)]


def test_inject_stores_and_contact_sends_summary() -> None:
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    b = bundle(b"x")
    actions = clinic.handle(Inject(mono=0, wall=None, bundle=encode_bundle(b)))
    assert actions == [
        Store(table="origin", key=b.header.bundle_id, value=b""),
        Store(table="bundles", key=b.header.bundle_id, value=encode_bundle(b)),
    ]
    up = clinic.handle(ContactUp(mono=1, wall=None, peer_id="M", radio=W))
    assert frames(up) == [{"t": "summary", "ids": [b.header.bundle_id], "acks": []}]


def test_offers_only_missing_bundles_most_urgent_first() -> None:
    a, b, c = bundle(b"a"), bundle(b"b", prio=Priority.CRITICAL), bundle(b"c")
    records = {"bundles": {x.header.bundle_id: encode_bundle(x) for x in (a, b, c)}}
    node = NodeCore(NodeConfig("M", Role.MULE, None), records)
    summary = cbor2.dumps({"t": "summary", "ids": [c.header.bundle_id], "acks": []})
    sent = frames(
        node.handle(FrameReceived(mono=0, wall=None, peer_id="P", radio=W, frame=summary))
    )
    assert [f["b"] for f in sent] == [encode_bundle(b), encode_bundle(a)]


def test_destination_delivers_once() -> None:
    hosp = NodeCore(NodeConfig("H", Role.HUB, "SA_H"), {})
    frame = cbor2.dumps({"t": "bundle", "b": encode_bundle(bundle(b"x"))})
    ev = FrameReceived(mono=0, wall=None, peer_id="M", radio=W, frame=frame)
    first = hosp.handle(ev)
    assert any(isinstance(a, Deliver) for a in first)
    assert hosp.handle(ev) == []


def test_malformed_frames_are_dropped_and_counted() -> None:
    node = NodeCore(NodeConfig("M", Role.MULE, None), {})
    for junk in (b"\xff", cbor2.dumps({"t": "nope"}), cbor2.dumps({"t": "bundle", "b": b"x"})):
        assert node.handle(FrameReceived(mono=0, wall=None, peer_id="P", radio=W, frame=junk)) == []
    assert node.dropped["malformed"] == 3


def test_new_bundle_goes_to_already_connected_peer() -> None:
    clinic = NodeCore(NodeConfig("C", Role.SITE, "SA_C"), {})
    clinic.handle(ContactUp(mono=0, wall=None, peer_id="M", radio=W))
    empty = cbor2.dumps({"t": "summary", "ids": [], "acks": []})
    clinic.handle(FrameReceived(mono=0, wall=None, peer_id="M", radio=W, frame=empty))
    b = bundle(b"late")
    sent = frames(clinic.handle(Inject(mono=5, wall=None, bundle=encode_bundle(b))))
    assert sent == [{"t": "bundle", "b": encode_bundle(b)}]
    assert clinic.handle(Inject(mono=6, wall=None, bundle=encode_bundle(b))) == []


def test_bundle_is_not_echoed_back_to_its_sender() -> None:
    mule = NodeCore(NodeConfig("M", Role.MULE, None), {})
    mule.handle(ContactUp(mono=0, wall=None, peer_id="C", radio=W))
    empty = cbor2.dumps({"t": "summary", "ids": [], "acks": []})
    mule.handle(FrameReceived(mono=0, wall=None, peer_id="C", radio=W, frame=empty))
    frame = cbor2.dumps({"t": "bundle", "b": encode_bundle(bundle(b"x"))})
    out = mule.handle(FrameReceived(mono=1, wall=None, peer_id="C", radio=W, frame=frame))
    assert frames(out) == []
