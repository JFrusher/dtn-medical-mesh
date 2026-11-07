from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from node.clock import Hlc
from node.events import ContactUp, Delete, Store
from node.hw.radio import RadioKind
from node.pki import Role
from node.storage import Storage
from validation.node.helpers import NOW, frame, inject, make_node, make_shards, summary_from

steps = st.lists(
    st.tuples(st.booleans(), st.one_of(st.none(), st.integers(0, 10**13)), st.integers(0, 2**60)),
    max_size=50,
)


@pytest.mark.req("REQ-TIME-01")
@given(steps)
def test_strictly_monotonic_whatever_the_rtc_or_peers_say(
    ops: list[tuple[bool, int | None, int]],
) -> None:
    clock, last = Hlc(), -1
    for is_merge, physical, remote in ops:
        value = clock.merge(remote, physical) if is_merge else clock.tick(physical)
        assert value > last
        if is_merge:
            assert value > remote
        last = value


@pytest.mark.req("REQ-TIME-01")
def test_rtc_reset_to_epoch_cannot_rewind() -> None:
    clock = Hlc()
    clock.tick(1_800_000_000_000)
    assert clock.tick(0) > 1_800_000_000_000 << 16  # RTC reset: keeps counting from last time
    assert clock.tick(None) > clock.value - 1
    assert clock.tick(1_800_000_005_000) == 1_800_000_005_000 << 16  # RTC ahead again: follows it


@pytest.mark.req("REQ-TIME-01")
def test_core_persists_hlc_and_relearns_time_from_peer_when_rtc_dead(tmp_path: Path) -> None:
    site = make_node("C", Role.SITE, "SA_C")
    storage = Storage(tmp_path / "c.db", b"k" * 32)
    for a in inject(site, make_shards(b"x")):
        if isinstance(a, Store | Delete):
            storage.apply(a)
    before = site.hlc.value

    rebooted = make_node("C", Role.SITE, "SA_C", storage.load())
    rebooted.hlc.value = int.from_bytes(storage.load()["meta"][b"hlc"])  # make_node bumps the clock
    rebooted.handle(ContactUp(mono=0, wall=0.0, peer_id="M", radio=RadioKind.WIFI))  # RTC at epoch
    assert rebooted.hlc.value > before

    dead_rtc = make_node("M2", Role.MULE)
    dead_rtc.hlc.value = 0
    msg = summary_from("P", "M2")
    frame(dead_rtc, "P", msg)  # wall=None: our RTC is down, so the peer's time is adopted
    assert dead_rtc.clock_s >= NOW + 60


@pytest.mark.req("REQ-TIME-01", "REQ-SEC-06")
def test_rtc_failure_keeps_time_moving_on_the_monotonic_clock() -> None:
    """Evidence from disaster-response: a site whose RTC died fell an hour behind between
    contacts, so peers rejected its summaries as stale and stopped offering it data."""
    site = make_node("C", Role.SITE, "SA_C")
    site.handle(ContactUp(mono=100.0, wall=float(NOW + 100), peer_id="X", radio=RadioKind.WIFI))
    site.handle(ContactUp(mono=100.0 + 3 * 3600, wall=None, peer_id="Y", radio=RadioKind.WIFI))
    assert site.clock_s == pytest.approx(NOW + 100 + 3 * 3600, abs=1)
