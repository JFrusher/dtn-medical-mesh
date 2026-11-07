"""Build valid shards and frames for node tests without any endpoint crypto."""

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from functools import cache

import cbor2

from node import erasure, merkle
from node.bundle import Ack, Header, Priority, Shard, encode_ack, encode_shard, make_bundle_id
from node.config import PRIORITY_CLASSES
from node.core import T_CERTS, NodeConfig, NodeCore, _summary_signing_bytes
from node.events import Action, ContactUp, FrameReceived, Inject, SendFrame
from node.hw.radio import RadioKind
from node.pki import HUB_CERT_LIFETIME_S, ROOT, Identity, Role, encode_cert, issue
from node.summary import Bloom
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy, SoftwareSecureElement

W = RadioKind.WIFI


def make_shards(
    tag: bytes,
    dst: str = "SA_H",
    priority: Priority = Priority.ROUTINE,
    created: int = 1,
    size: int = 4096,
) -> list[Shard]:
    cls = PRIORITY_CLASSES[priority]
    data = hashlib.shake_256(tag).digest(size)  # varied, so no two blocks are equal
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
        CLINIC.cert.cert_id,
        b"",
    )
    h = replace(h, sig=CLINIC.sign(h.signing_bytes()))
    return [Shard(h, i, b, tuple(proofs[i])) for i, b in enumerate(blocks)]


def records(*bundles: list[Shard]) -> dict[str, dict[bytes, bytes]]:
    return {"shards": {s.shard_id: encode_shard(s) for b in bundles for s in b}}


def frames(actions: list[Action]) -> list[dict[str, object]]:
    return [cbor2.loads(a.frame) for a in actions if isinstance(a, SendFrame)]


def frame(node: NodeCore, peer: str, msg: dict[str, object], mono: float = 0) -> list[Action]:
    return node.handle(
        FrameReceived(mono=mono, wall=None, peer_id=peer, radio=W, frame=cbor2.dumps(msg))
    )


def connect(
    node: NodeCore, peer: str, ids: Sequence[bytes] = (), acks: Sequence[bytes] = ()
) -> None:
    node.handle(ContactUp(mono=0, wall=None, peer_id=peer, radio=W))
    frame(node, peer, summary_from(peer, node.config.node_id, ids, acks))


def summary_from(
    peer: str, to: str, ids: Sequence[bytes] = (), acks: Sequence[bytes] = ()
) -> dict[str, object]:
    """A correctly signed opening summary from test node `peer` (a mule, or the hub 'H')."""
    me = identity(peer, Role.HUB, "SA_H") if peer == "H" else identity(peer, Role.MULE, None)
    msg: dict[str, object] = {
        "t": "summary",
        "bloom": Bloom.of([b"s" + i for i in ids] + [b"a" + a for a in acks]).encode(),
        "certs": [],
        "docs": {},
        "from": encode_cert(me.cert),
        "issuer": encode_cert(me.issuer) if me.issuer else None,
        "to": to,
        "time": NOW + 60,
    }
    msg["sig"] = me.sign(_summary_signing_bytes(msg))
    return msg


def shard_msg(s: Shard) -> dict[str, object]:
    return {"t": "shard", "s": encode_shard(s)}


def inject(node: NodeCore, shards: list[Shard]) -> list[Action]:
    encoded = tuple(encode_shard(s) for s in shards)
    return node.handle(Inject(mono=0, wall=None, shards=encoded))


# --- provisioned test identities -------------------------------------------------

NOW = int(SIM_EPOCH)
TEST_ROOT = SoftwareSecureElement(SeededEntropy("test-root"))
TEST_HUB_SE = SoftwareSecureElement(SeededEntropy("test-hub"))
TEST_HUB = issue(
    TEST_ROOT.sign, ROOT, "H", Role.HUB, "SA_H", TEST_HUB_SE.public_key(), NOW, HUB_CERT_LIFETIME_S
)


@cache
def node_se(node_id: str) -> SoftwareSecureElement:
    return TEST_HUB_SE if node_id == "H" else SoftwareSecureElement(SeededEntropy(node_id))


def identity(node_id: str, role: Role, sa: str | None) -> Identity:
    if node_id == "H":
        return Identity(TEST_HUB, None, TEST_ROOT.public_key(), TEST_HUB_SE.sign)
    se = node_se(node_id)
    cert = issue(TEST_HUB_SE.sign, TEST_HUB.cert_id, node_id, role, sa, se.public_key(), NOW)
    return Identity(cert, TEST_HUB, TEST_ROOT.public_key(), se.sign)


def make_node(
    node_id: str,
    role: Role,
    sa: str | None = None,
    held: dict[str, dict[bytes, bytes]] | None = None,
    capacity: int = 32 * 1024**3,
) -> NodeCore:
    config = NodeConfig(node_id, role, sa, capacity)
    initial = {**(held or {})}
    initial.setdefault(T_CERTS, {})[CLINIC.cert.cert_id.encode()] = encode_cert(CLINIC.cert)
    core = NodeCore(config, initial, identity(node_id, role, sa))
    core.clock_s = NOW + 60  # as if an event with a wall time had arrived
    return core


CLINIC = identity("C", Role.SITE, "SA_C")  # the test site that signs make_shards() headers


def signed_ack(bundle_id: bytes, dst_sa: str = "SA_H", signer: Identity | None = None) -> Ack:
    """An ACK signed by the test hub H (SA_H) unless another signer is given."""
    me = signer or identity("H", Role.HUB, "SA_H")
    ack = Ack(bundle_id, dst_sa, me.cert.cert_id)
    return replace(ack, sig=me.sign(ack.signing_bytes()))


def ack_msg(ack: Ack) -> dict[str, object]:
    return {"t": "ack", "a": encode_ack(ack)}
