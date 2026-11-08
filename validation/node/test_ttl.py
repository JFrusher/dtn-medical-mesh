import pytest

from node.bundle import Priority
from node.events import Delete, Timer
from node.pki import Role
from validation.node.helpers import (
    NOW,
    frame,
    frames,
    make_node,
    make_shards,
    records,
    shard_msg,
    summary_from,
)

SIX_HOURS_MS = 6 * 3600 * 1000  # CRITICAL lifetime


@pytest.mark.req("REQ-TIME-02")
def test_copy_aged_past_lifetime_is_refused_even_if_clock_says_fresh() -> None:
    mule = make_node("M", Role.MULE)
    s = make_shards(b"x", priority=Priority.CRITICAL)[0]
    old = {**shard_msg(s), "age": SIX_HOURS_MS + 1}
    assert frame(mule, "P", old) == []
    assert mule.dropped["expired"] == 1
    assert frame(mule, "P", shard_msg(s))


@pytest.mark.req("REQ-TIME-02")
def test_clock_past_lifetime_expires_even_a_young_copy() -> None:
    mule = make_node("M", Role.MULE)
    mule.clock_s = NOW + 6 * 3600 + 1  # HLC can only move forward: a reset can't revive it
    assert frame(mule, "P", shard_msg(make_shards(b"x", priority=Priority.CRITICAL)[0])) == []
    assert mule.dropped["expired"] == 1


@pytest.mark.req("REQ-TIME-02", "REQ-DUR-05")
def test_sweep_purges_expired_and_expired_are_evicted_first() -> None:
    critical = make_shards(b"c", priority=Priority.CRITICAL)
    routine = make_shards(b"r")
    mule = make_node("M", Role.MULE, held=records(critical, routine))
    mule.clock_s = NOW + 6 * 3600 + 60
    out = mule.handle(Timer(mono=61, wall=None, name="noop"))
    assert {a.key for a in out if isinstance(a, Delete)} == {s.shard_id for s in critical}
    assert mule.shards.keys() == {s.shard_id for s in routine}


@pytest.mark.req("REQ-TIME-02")
def test_age_accumulates_across_hops() -> None:
    mule = make_node("M", Role.MULE)
    s = make_shards(b"x")[0]
    frame(mule, "P", {**shard_msg(s), "age": 1000}, mono=10)
    later = frame(mule, "Q", summary_from("Q", "M"), mono=40)
    [sent] = [f for f in frames(later) if f["t"] == "shard"]
    assert sent["age"] == 1000 + 30_000


@pytest.mark.req("REQ-DUR-05")
def test_expired_shard_of_any_class_makes_room_first() -> None:
    old_critical = make_shards(b"c", priority=Priority.CRITICAL)[:1]
    mule = make_node("M", Role.MULE, held=records(old_critical), capacity=1024)
    mule.clock_s = NOW + 6 * 3600 + 1  # critical expired; a routine bundle (72 h) is not
    out = frame(mule, "P", shard_msg(make_shards(b"r", created=6 * 3600 * 1000)[0]))
    assert [a.key for a in out if isinstance(a, Delete)] == [old_critical[0].shard_id]
