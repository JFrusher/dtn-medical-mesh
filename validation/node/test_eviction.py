import pytest

from node.bundle import Priority, Shard
from node.core import NodeConfig, NodeCore, StorageFull
from node.events import Delete
from node.pki import Role
from validation.node.helpers import frame, inject, make_shards, records, shard_msg

SHARD = 1024  # make_shards(size=4096) with k=4 gives 1024-byte routine shards


def mule(capacity: int, *held: list[Shard]) -> NodeCore:
    return NodeCore(NodeConfig("M", Role.MULE, None, capacity), records(*held))


@pytest.mark.req("REQ-DUR-05")
def test_evicts_telemetry_then_oldest_routine_for_urgent() -> None:
    tele = make_shards(b"t", priority=Priority.TELEMETRY, size=1024)  # one 1024-byte shard
    old, new = make_shards(b"o", created=1)[:1], make_shards(b"n", created=2)[:1]
    node = mule(3 * SHARD, tele, old, new)
    urgent = make_shards(b"u", priority=Priority.URGENT)
    first = frame(node, "P", shard_msg(urgent[0]))
    assert [a.key for a in first if isinstance(a, Delete)] == [tele[0].shard_id]
    second = frame(node, "P", shard_msg(urgent[1]))
    assert [a.key for a in second if isinstance(a, Delete)] == [old[0].shard_id]
    assert node.dropped["evicted"] == 2


@pytest.mark.req("REQ-DUR-05")
def test_critical_never_evicted_for_lower_and_refusal_evicts_nothing() -> None:
    critical = make_shards(b"c", priority=Priority.CRITICAL)
    routine = make_shards(b"r")
    node = mule(2 * SHARD, critical[:1], routine[:1])
    urgent = make_shards(b"u", priority=Priority.URGENT)
    assert [a for a in frame(node, "P", shard_msg(urgent[0])) if isinstance(a, Delete)]
    assert critical[0].shard_id in node.shards
    assert frame(node, "P", shard_msg(urgent[1])) == []  # only CRITICAL left: refuse
    assert node.dropped["full"] == 1 and critical[0].shard_id in node.shards
    full_of_critical = mule(2 * SHARD, critical[:2])
    other = make_shards(b"c2", priority=Priority.CRITICAL)
    assert frame(full_of_critical, "P", shard_msg(other[0])) == []  # same class never evicts


@pytest.mark.req("REQ-DUR-05", "REQ-PROTO-05")
def test_origin_copies_are_never_evicted_and_full_origin_fails_loudly() -> None:
    site = NodeCore(NodeConfig("C", Role.SITE, "SA_C", 6 * SHARD), {})
    own = make_shards(b"own")  # 6 routine shards fill the node
    inject(site, own)
    critical = make_shards(b"c", priority=Priority.CRITICAL)
    assert frame(site, "P", shard_msg(critical[0])) == []
    assert all(s.shard_id in site.shards for s in own)
    with pytest.raises(StorageFull):
        inject(site, make_shards(b"more"))
