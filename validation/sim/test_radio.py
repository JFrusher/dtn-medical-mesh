import random
from pathlib import Path

import pytest

from node.core import NodeConfig
from node.events import SendFrame
from node.hw.radio import RadioKind
from node.pki import Role
from simulation.mobility import Route, Static
from simulation.provision import provision
from simulation.radio_model import (
    DutyCycle,
    Link,
    RadioParams,
    ble_discovery_s,
    channel,
    lora_airtime_s,
    lora_reach_m,
    wifi_mean_rx_dbm,
    wifi_rate,
    wifi_reach_m,
)
from simulation.scheduler import Scheduler
from simulation.world import SimNode, World


def drive_by(tmp_path: Path, speed: float) -> World:
    """A mule drives straight past a relay; Wi-Fi range 100 m, so the window is ~200 m / speed."""
    s = Scheduler(seed=1)
    w = World(s, tmp_path, RadioParams.defaults(wifi_max_bytes_per_s=100_000), tick_s=0.5)
    w.add_node(NodeConfig("RELAY", Role.RELAY, None), Static((0, 0)))
    w.add_node(NodeConfig("HUB", Role.HUB, "SA_H"), Static((50_000, 0)))
    w.add_node(NodeConfig("MULE", Role.MULE, None), Route(((-1000, 0), (1000, 0)), speed=speed))
    provision(list(w.nodes.values()), seed=1)
    w.start()
    return w


def run_until_contact(w: World) -> None:
    while frozenset(("MULE", "RELAY")) not in w.contacts:
        assert w.scheduler.now < 60, "mule never reached the relay"
        w.scheduler.run_until(w.scheduler.now + 0.5)


@pytest.mark.req("REQ-SIM-03")
def test_rate_steps_down_with_distance_and_stops_past_mcs0() -> None:
    p = RadioParams.defaults()
    rates = [wifi_rate(wifi_mean_rx_dbm(d, p), p) for d in (5, 30, 60, 85, 200)]
    assert rates == sorted(rates, reverse=True) and rates[0] > rates[3] > 0 == rates[4]
    assert rates[0] == p.wifi_max_bytes_per_s  # close by: capped by the Pi, not the radio
    assert 80 < wifi_reach_m(p) / 10 ** (3 * p.wifi_shadow_sigma_db / (10 * p.wifi_ple)) < 100


@pytest.mark.req("REQ-SIM-03")
def test_ble_discovery_delay_is_random_with_the_expected_mean() -> None:
    p, rng = RadioParams.defaults(), random.Random(3)
    delays = [ble_discovery_s(p, rng) for _ in range(4000)]
    assert min(delays) == p.ble_adv_interval_s
    mean = p.ble_adv_interval_s / p.ble_scan_duty
    assert sum(delays) / len(delays) == pytest.approx(mean, rel=0.1)


@pytest.mark.req("REQ-SIM-03")
def test_nearby_contacts_share_airtime(tmp_path: Path) -> None:
    """Two parked pairs 20 m apart share a channel; a frame takes twice as long."""
    w = World(Scheduler(seed=1), tmp_path, RadioParams.defaults(wifi_shadow_sigma_db=0))
    w.add_node(NodeConfig("A", Role.HUB, "SA_A"), Static((0, 0)))
    for name, x in (("B", 10), ("C", 20), ("D", 30)):
        w.add_node(NodeConfig(name, Role.RELAY, None), Static((x, 0)))
    w.add_node(NodeConfig("FAR", Role.RELAY, None), Static((5000, 0)))
    provision(list(w.nodes.values()), seed=1)
    w.start()
    w.scheduler.run_until(10)
    assert w._sharing("A", "B") == len(w.contacts) == 6  # every pair of the four is in contact
    w.add_node(NodeConfig("FAR2", Role.RELAY, None), Static((5010, 0)))
    assert w._sharing("FAR", "FAR2") == 1  # out of carrier-sense range of the others


@pytest.mark.req("REQ-SIM-03", "REQ-PROTO-01")
def test_drive_by_window_cuts_big_transfers(tmp_path: Path) -> None:
    w = drive_by(tmp_path, speed=22)  # ~80 km/h: in range ~8 s, link up ~2 s after reach
    s = w.scheduler
    run_until_contact(w)
    frame = b"x" * 2_000_000  # 20 s of airtime at 100 kB/s: cannot finish
    w._transmit("RELAY", SendFrame(peer_id="MULE", radio=RadioKind.WIFI, frame=frame))
    before = w.lost_frames
    s.run_until(100)
    assert w.lost_frames > before  # ours, plus any protocol frames cut off with it
    assert frozenset(("MULE", "RELAY")) not in w.contacts


@pytest.mark.req("REQ-SIM-03")
def test_link_needs_setup_time_and_frames_queue(tmp_path: Path) -> None:
    w = drive_by(tmp_path, speed=200)  # in range ~1 s: never gets past link setup
    w.scheduler.run_until(60)
    assert w.links[frozenset(("MULE", "RELAY"))].generation == 0  # never came up


@pytest.mark.req("REQ-SIM-03")
def test_frame_lost_when_contact_drops_does_not_leak_into_next_contact(tmp_path: Path) -> None:
    w = drive_by(tmp_path, speed=22)
    run_until_contact(w)
    link = w.links[frozenset(("MULE", "RELAY"))]
    w._transmit("RELAY", SendFrame(peer_id="MULE", radio=RadioKind.WIFI, frame=b"y" * 3_000_000))
    w.scheduler.run_until(400)  # route loops back past the relay: a new contact, new generation
    assert link.generation >= 2 and w.lost_frames >= 1


@pytest.mark.req("REQ-SIM-03")
def test_gilbert_elliott_losses_come_in_bursts_and_bits_flip() -> None:
    params = RadioParams.defaults(
        p_good_to_bad=0.05, p_bad_to_good=0.2, loss_in_bad=0.9, bit_flip_per_frame=0.1
    )
    rng, link = random.Random(7), Link(up=True)
    out = [channel(link, b"frame", params, rng) for _ in range(5000)]
    lost = [o is None for o in out]
    runs = sum(1 for a, b in zip(lost, lost[1:], strict=False) if a and b)
    assert 0.05 < sum(lost) / len(lost) < 0.4
    assert runs > sum(lost) * 0.5  # losses cluster: most lost frames follow another loss
    flipped = [o for o in out if o is not None and o != b"frame"]
    assert flipped and all(len(o) == 5 for o in flipped)


@pytest.mark.req("REQ-SIM-03", "REQ-PROTO-09")
def test_lora_airtime_and_duty_cycle() -> None:
    assert lora_airtime_s(20, sf=7, bw_hz=125_000, cr=1) == pytest.approx(0.0566, abs=0.001)
    assert lora_airtime_s(200, 12, 125_000, 1) > 5 * lora_airtime_s(200, 9, 125_000, 1)
    budget = DutyCycle()
    sent = sum(budget.admit(t, 1.0, duty=0.01) for t in range(60))
    assert sent == 36  # 1% of an hour is 36 s of airtime
    assert budget.admit(3600 + 1, 1.0, duty=0.01)  # the oldest airtime has rolled out


@pytest.mark.req("REQ-SIM-03", "REQ-PROTO-09")
def test_lora_budget_gives_about_2_km_at_sf9_and_more_at_sf12() -> None:
    def mean_range(sf: int) -> float:
        p = RadioParams.defaults(lora_sf=sf)
        return float(lora_reach_m(p) / 10 ** (3 * p.lora_shadow_sigma_db / (10 * p.lora_ple)))

    assert 1800 < mean_range(9) < 2300
    assert mean_range(12) > 1.6 * mean_range(9) > 1.6 * mean_range(7)  # +8 dB, +6 dB of budget


@pytest.mark.req("REQ-SIM-03", "REQ-PROTO-09")
def test_lora_broadcast_reaches_near_nodes_not_far_ones(tmp_path: Path) -> None:
    radio = RadioParams.defaults(lora_shadow_sigma_db=0)
    w = World(Scheduler(seed=1), tmp_path, radio)
    for name, x in (("HUB", 0), ("NEAR", 1500), ("FAR", 3000)):
        w.add_node(
            NodeConfig(
                name, Role.HUB if name == "HUB" else Role.RELAY, "SA_H" if name == "HUB" else None
            ),
            Static((x, 0)),
        )
    provision(list(w.nodes.values()), seed=1)
    w.start()
    heard: list[str] = []

    def record(node: SimNode, event: object) -> None:
        heard.append(node.id)

    w.dispatch = record  # type: ignore[method-assign,assignment]
    w._transmit("HUB", SendFrame(peer_id="", radio=RadioKind.LORA, frame=b"z" * 20))
    w.scheduler.run_until(5)
    assert heard == ["NEAR"]
