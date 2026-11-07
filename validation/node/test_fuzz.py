"""Every byte from the radio is untrusted: no corrupted frame may crash a node (§8.1).

Found by the stress scenario (seed 2): a flipped bit made a header's `created` negative and
make_bundle_id raised OverflowError, crashing the node.
"""

import random
from dataclasses import asdict

import cbor2
import pytest

from node.bundle import FormatError, decode_header
from node.events import FrameReceived
from node.hw.radio import RadioKind
from node.pki import Role
from validation.node.helpers import (
    ack_msg,
    make_node,
    make_shards,
    shard_msg,
    signed_ack,
    summary_from,
)


def corpus() -> list[bytes]:
    s = make_shards(b"x")[0]
    return [
        cbor2.dumps(shard_msg(s)),
        cbor2.dumps(ack_msg(signed_ack(s.header.bundle_id))),
        cbor2.dumps(summary_from("P", "M")),
    ]


@pytest.mark.req("REQ-PROTO-01")
@pytest.mark.parametrize("radio", [RadioKind.WIFI, RadioKind.LORA])
def test_bit_flipped_frames_never_crash_the_core(radio: RadioKind) -> None:
    """Every single-bit flip in the first 700 bytes (all headers and signatures), plus random
    multi-bit damage anywhere."""
    rng = random.Random(2)
    node = make_node("M", Role.MULE)

    def feed(frame: bytes) -> None:
        node.handle(FrameReceived(mono=0, wall=None, peer_id="P", radio=radio, frame=frame))

    for frame in corpus():
        for i in range(min(len(frame), 700) * 8):
            bad = bytearray(frame)
            bad[i // 8] ^= 1 << (i % 8)
            feed(bytes(bad))
        for _ in range(300):
            bad = bytearray(frame)
            for _ in range(rng.randint(2, 6)):
                i = rng.randrange(len(bad) * 8)
                bad[i // 8] ^= 1 << (i % 8)
            feed(bytes(bad))


@pytest.mark.req("REQ-PROTO-01")
def test_out_of_range_header_fields_are_format_errors() -> None:
    h = asdict(make_shards(b"x")[0].header)
    bad = [("created", -1), ("k", 0), ("m", 300), ("lifetime_s", 0), ("size_bucket", -5)]
    for field, value in bad:
        with pytest.raises(FormatError):
            decode_header({**h, field: value})
