from dataclasses import replace

import pytest

from node.pki import Role
from validation.node.helpers import frame, inject, make_node, make_shards, shard_msg


@pytest.mark.req("REQ-DUR-01")
@pytest.mark.parametrize(
    "corrupt",
    [
        lambda s: replace(s, data=bytes([s.data[0] ^ 1]) + s.data[1:]),  # bit flip
        lambda s: replace(s, data=s.data[:-1]),  # truncated
        lambda s: replace(s, index=(s.index + 1) % 6),  # wrong position
        lambda s: replace(s, proof=s.proof[:-1]),  # broken proof
    ],
)
def test_corrupt_shards_are_dropped_and_counted(corrupt) -> None:  # type: ignore[no-untyped-def]
    node = make_node("M", Role.MULE, None)
    good = make_shards(b"x")[0]
    assert frame(node, "P", shard_msg(corrupt(good))) == []
    assert node.dropped["corrupt"] == 1 and not node.shards
    assert frame(node, "P", shard_msg(good))  # the intact shard is still accepted


def test_endpoint_injecting_corrupt_shard_fails_loudly() -> None:
    site = make_node("C", Role.SITE, "SA_C")
    shards = make_shards(b"x")
    shards[2] = replace(shards[2], data=b"\x00" * len(shards[2].data))
    with pytest.raises(ValueError):
        inject(site, shards)
