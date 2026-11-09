from pathlib import Path

import pytest

from node.core import NodeConfig
from node.pki import Role
from simulation.mobility import Route, Static
from simulation.provision import provision
from simulation.radio_model import RadioParams
from simulation.scheduler import Scheduler
from simulation.world import World


def test_route_positions_with_dwell() -> None:
    r = Route(waypoints=((0, 0), (100, 0)), speed=10, dwell_s=5)
    assert r.position(0) == (0, 0)
    assert r.position(4.9) == (0, 0)
    assert r.position(10) == pytest.approx((50, 0))
    assert r.position(16) == (100, 0)
    assert r.position(25) == pytest.approx((50, 0))  # driving back
    assert r.position(25 + 30) == pytest.approx((50, 0))  # cycle = 2 * (5 + 10) = 30 s


def test_mule_makes_and_breaks_contacts(tmp_path: Path) -> None:
    s = Scheduler(seed=1)
    log: list[str] = []
    radio = RadioParams.defaults(wifi_shadow_sigma_db=0)  # no shadowing: a fixed ~91 m reach
    w = World(s, tmp_path, radio, log=lambda t, m: log.append(f"{t:.0f} {m}"))
    w.add_node(NodeConfig("CLINIC", Role.SITE, "SA_C"), Static((0, 0)))
    w.add_node(NodeConfig("HOSP", Role.HUB, "SA_H"), Static((1000, 0)))
    w.add_node(NodeConfig("MULE", Role.MULE, None), Route(((0, 0), (1000, 0)), speed=10))
    provision(list(w.nodes.values()), seed=1)
    w.start()
    s.run_until(105)
    (t1, m1), (t2, m2) = [(float(x.split()[0]), x.split(" ", 1)[1]) for x in log]
    assert (m1, m2) == ("CONTACT CLINIC <-> MULE", "CONTACT HOSP <-> MULE")
    assert 1.5 < t1 < 5  # BLE discovery plus Wi-Fi join, never instant
    assert 80 < t2 < 100  # in reach ~90 m before the hospital
    assert w.contacts == {frozenset(("HOSP", "MULE"))}


def test_rejects_duplicate_node(tmp_path: Path) -> None:
    w = World(Scheduler(seed=1), tmp_path)
    w.add_node(NodeConfig("A", Role.MULE, None), Static((0, 0)))
    with pytest.raises(ValueError):
        w.add_node(NodeConfig("A", Role.MULE, None), Static((0, 0)))


def test_route_waits_only_at_stops() -> None:
    r = Route(waypoints=((0, 0), (50, 0), (100, 0)), speed=10, dwell_s=7, stops=(0, 2))
    assert r.position(3) == (0, 0)  # waiting at stop 0
    assert r.position(7 + 5) == pytest.approx((50, 0))  # passes through 1 without waiting
    assert r.position(7 + 6) == pytest.approx((60, 0))
    assert r.position(7 + 10 + 3) == (100, 0)  # waiting at stop 2
    with pytest.raises(ValueError):
        Route(waypoints=((0, 0), (1, 0)), speed=1, stops=(5,))
