from dataclasses import replace

import pytest

from node.events import HealthTick
from node.pki import Role
from validation.node.helpers import (
    NOW,
    connect,
    frame,
    make_node,
    make_shards,
    shard_msg,
    summary_from,
)

DAY = 86400


def tick(battery: float | None = None) -> HealthTick:
    return HealthTick(
        mono=1, wall=None, battery_pct=battery, temp_c=25.0, version="0.2.0", external_power=True
    )


def seen_hub(node) -> None:  # type: ignore[no-untyped-def]
    frame(node, "H", summary_from("H", node.config.node_id))


@pytest.mark.req("REQ-OPS-06")
def test_green_blue_and_blue_flash() -> None:
    mule = make_node("M", Role.MULE, None)
    seen_hub(mule)
    assert mule.status_light() == "blue-flash"  # exchanging with the hub right now
    mule.peer_bloom.clear()
    assert mule.status_light() == "green"
    connect(mule, "C")
    for s in make_shards(b"x"):
        frame(mule, "C", shard_msg(s))
    mule.peer_bloom.clear()
    assert mule.status_light() == "blue"  # carrying, no contact


@pytest.mark.req("REQ-OPS-06")
def test_amber_when_nearly_full_or_a_hub_is_overdue() -> None:
    mule = make_node("M", Role.MULE, None)
    assert mule.status_light() == "green"  # provisioned at a hub just now
    mule.clock_s = NOW + 8 * DAY
    assert mule.status_light() == "amber"  # no hub since: renewal due
    mule = make_node("M2", Role.MULE, None)
    seen_hub(mule)
    mule.peer_bloom.clear()
    mule.clock_s = NOW + 8 * DAY
    assert mule.status_light() == "amber"  # seven days without a hub: renewal due
    tiny = make_node("T", Role.MULE, None)
    tiny.config = replace(tiny.config, capacity_bytes=1)
    seen_hub(tiny)
    tiny.peer_bloom.clear()
    tiny.used_bytes = 1
    assert tiny.status_light() == "amber"


@pytest.mark.req("REQ-OPS-06")
def test_red_when_revoked_expired_or_power_failing() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    assert hub.status_light() == "green"  # a hub needs no hub
    hub.handle(tick(battery=5.0))
    assert hub.status_light() == "red"
    hub.handle(tick(battery=None))
    hub.revoked = frozenset({"H"})
    assert hub.status_light() == "red"
    mule = make_node("M", Role.MULE, None)
    mule.clock_s = mule.identity.cert.not_after + 1
    assert mule.status_light() == "red"
