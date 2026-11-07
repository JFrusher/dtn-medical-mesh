import cbor2
import pytest

from node.core import LORA_MAX_FRAME
from node.events import EndpointAck, FrameReceived, SendFrame
from node.hw.radio import RadioKind
from node.pki import Role
from validation.node.helpers import ack_msg, make_node, make_shards, records, shard_msg, signed_ack

L = RadioKind.LORA


def lora(node, msg: dict[str, object]):  # type: ignore[no-untyped-def]
    return node.handle(
        FrameReceived(mono=0, wall=None, peer_id="FAR", radio=L, frame=cbor2.dumps(msg))
    )


@pytest.mark.req("REQ-PROTO-09")
def test_ack_is_broadcast_on_lora_and_purges_far_away_relays() -> None:
    shards = make_shards(b"x")
    hub = make_node("H", Role.HUB, "SA_H", records(shards))
    out = hub.handle(EndpointAck(mono=0, wall=None, bundle_id=shards[0].header.bundle_id))
    [radio_msg] = [a for a in out if isinstance(a, SendFrame) and a.radio is L]
    assert radio_msg.peer_id == "*" and len(radio_msg.frame) <= LORA_MAX_FRAME
    far_mule = make_node("M", Role.MULE, held=records(shards))
    far_mule.certs[hub.identity.cert.cert_id] = hub.identity.cert
    far_mule.handle(FrameReceived(mono=0, wall=None, peer_id="H", radio=L, frame=radio_msg.frame))
    assert far_mule.shards == {}


@pytest.mark.req("REQ-PROTO-09")
def test_shards_summaries_and_oversized_frames_are_rejected_on_lora() -> None:
    mule = make_node("M", Role.MULE)
    assert lora(mule, shard_msg(make_shards(b"x")[0])) == []
    assert lora(mule, {"t": "summary"}) == []
    big = {**ack_msg(signed_ack(b"\x00" * 32)), "pad": b"x" * LORA_MAX_FRAME}
    assert lora(mule, big) == []
    assert mule.dropped["lora_rejected"] == 3 and mule.shards == {}
