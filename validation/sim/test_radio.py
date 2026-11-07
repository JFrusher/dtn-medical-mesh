from pathlib import Path

import pytest

from node.core import NodeConfig
from node.events import SendFrame
from node.hw.radio import RadioKind
from node.pki import Role
from simulation.mobility import Route, Static
from simulation.provision import provision
from simulation.radio_model import RadioParams
from simulation.scheduler import Scheduler
from simulation.world import World


def drive_by(tmp_path: Path, speed: float) -> World:
    """A mule drives straight past a relay; Wi-Fi range 100 m, so the window is ~200 m / speed."""
    s = Scheduler(seed=1)
    w = World(s, tmp_path, RadioParams(wifi_bytes_per_s=100_000), tick_s=0.5)
    w.add_node(NodeConfig("RELAY", Role.RELAY, None), Static((0, 0)))
    w.add_node(NodeConfig("HUB", Role.HUB, "SA_H"), Static((50_000, 0)))
    w.add_node(NodeConfig("MULE", Role.MULE, None), Route(((-1000, 0), (1000, 0)), speed=speed))
    provision(list(w.nodes.values()), seed=1)
    w.start()
    return w


@pytest.mark.req("REQ-SIM-03", "REQ-PROTO-01")
def test_drive_by_window_cuts_big_transfers(tmp_path: Path) -> None:
    w = drive_by(tmp_path, speed=22)  # ~80 km/h: in range ~9 s, link up after 2 s
    s = w.scheduler
    s.run_until(45)  # in range from ~41 s, link up ~2 s later
    assert frozenset(("MULE", "RELAY")) in w.contacts
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
    w.scheduler.run_until(45)
    link = w.links[frozenset(("MULE", "RELAY"))]
    w._transmit("RELAY", SendFrame(peer_id="MULE", radio=RadioKind.WIFI, frame=b"y" * 3_000_000))
    w.scheduler.run_until(400)  # route loops back past the relay: a new contact, new generation
    assert link.generation >= 2 and w.lost_frames >= 1
