"""Merkle tree over shard data, so each shard verifies on its own (spec §6.2, REQ-DUR-01).

Leaves and inner nodes are hashed with different prefixes, and the leaf count is padded to
a power of two with a fixed empty leaf, so proof length is fixed by the shard count.
"""

import hashlib

_EMPTY = hashlib.sha256(b"\x02").digest()


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _depth(n: int) -> int:
    return max(0, (n - 1).bit_length())


def build(leaves: list[bytes]) -> tuple[bytes, list[list[bytes]]]:
    """Return (root, proof for each leaf) for the given leaf data."""
    if not leaves:
        raise ValueError("Merkle tree needs at least one leaf")
    level = [leaf_hash(d) for d in leaves]
    level += [_EMPTY] * ((1 << _depth(len(leaves))) - len(level))
    proofs: list[list[bytes]] = [[] for _ in leaves]
    positions = list(range(len(leaves)))
    while len(level) > 1:
        for i, pos in enumerate(positions):
            proofs[i].append(level[pos ^ 1])
            positions[i] = pos // 2
        level = [_node(level[j], level[j + 1]) for j in range(0, len(level), 2)]
    return level[0], proofs


def verify(data: bytes, index: int, count: int, proof: list[bytes], root: bytes) -> bool:
    if not 0 <= index < count or len(proof) != _depth(count):
        return False
    h = leaf_hash(data)
    for sibling in proof:
        h = _node(h, sibling) if index % 2 == 0 else _node(sibling, h)
        index //= 2
    return h == root
