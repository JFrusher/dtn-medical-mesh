"""Build valid shards and frames for node tests without any endpoint crypto."""

import cbor2

from node import erasure, merkle
from node.bundle import Header, Priority, Shard, encode_shard, make_bundle_id
from node.config import PRIORITY_CLASSES
from node.core import NodeCore
from node.events import Action, ContactUp, FrameReceived, Inject, SendFrame
from node.hw.radio import RadioKind

W = RadioKind.WIFI


def make_shards(
    tag: bytes,
    dst: str = "SA_H",
    priority: Priority = Priority.ROUTINE,
    created: int = 1,
    size: int = 4096,
) -> list[Shard]:
    cls = PRIORITY_CLASSES[priority]
    data = (tag * size)[:size]
    blocks = erasure.encode(data, cls.k, cls.m)
    root, proofs = merkle.build(blocks)
    h = Header(
        make_bundle_id(root, created),
        "SA_C",
        dst,
        priority,
        created,
        cls.ttl_s,
        size,
        cls.k,
        cls.m,
        root,
        "C",
        b"",
    )
    return [Shard(h, i, b, tuple(proofs[i])) for i, b in enumerate(blocks)]


def records(*bundles: list[Shard]) -> dict[str, dict[bytes, bytes]]:
    return {"shards": {s.shard_id: encode_shard(s) for b in bundles for s in b}}


def frames(actions: list[Action]) -> list[dict[str, object]]:
    return [cbor2.loads(a.frame) for a in actions if isinstance(a, SendFrame)]


def frame(node: NodeCore, peer: str, msg: dict[str, object], mono: float = 0) -> list[Action]:
    return node.handle(
        FrameReceived(mono=mono, wall=None, peer_id=peer, radio=W, frame=cbor2.dumps(msg))
    )


def connect(node: NodeCore, peer: str, ids: list[bytes] = [], acks: list[bytes] = []) -> None:  # noqa: B006
    node.handle(ContactUp(mono=0, wall=None, peer_id=peer, radio=W))
    frame(node, peer, {"t": "summary", "ids": ids, "acks": acks})


def shard_msg(s: Shard) -> dict[str, object]:
    return {"t": "shard", "s": encode_shard(s)}


def inject(node: NodeCore, shards: list[Shard]) -> list[Action]:
    encoded = tuple(encode_shard(s) for s in shards)
    return node.handle(Inject(mono=0, wall=None, shards=encoded))
