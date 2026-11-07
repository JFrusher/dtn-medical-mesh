"""Endpoint send/receive pipeline (spec §5.2): sign -> seal -> pad -> shard -> header, and back."""

from endpoint.crypto import EndpointKeys, Opened, open_sealed, seal
from endpoint.padding import pad, unpad
from node import erasure, merkle
from node.bundle import Bundle, Header, Priority, Shard, make_bundle_id
from node.config import PRIORITY_CLASSES
from node.hw.entropy import Entropy


def build_shards(
    content: bytes,
    recipient: bytes,
    author: EndpointKeys,
    entropy: Entropy,
    *,
    src_sa: str,
    dst_sa: str,
    priority: Priority,
    created: int,
    injector: str,
) -> list[Shard]:
    padded = pad(seal(content, recipient, author, entropy))
    cls = PRIORITY_CLASSES[priority]
    blocks = erasure.encode(padded, cls.k, cls.m)
    root, proofs = merkle.build(blocks)
    header = Header(
        bundle_id=make_bundle_id(root, created),
        src_sa=src_sa,
        dst_sa=dst_sa,
        priority=priority,
        created=created,
        lifetime_s=cls.ttl_s,
        size_bucket=len(padded),
        k=cls.k,
        m=cls.m,
        merkle_root=root,
        injector=injector,
        sig=b"",  # signed by the injecting site node from P3.S03
    )
    return [Shard(header, i, b, tuple(proofs[i])) for i, b in enumerate(blocks)]


def open_delivered(bundle: Bundle, keys: EndpointKeys) -> Opened:
    """Raises CryptoError when the bundle is for another endpoint in this service area."""
    return open_sealed(unpad(bundle.payload), keys)
