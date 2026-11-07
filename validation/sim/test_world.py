from pathlib import Path

import pytest

from node.core import NodeConfig
from node.pki import Role
from simulation.mobility import Route, Static
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
    w = World(s, tmp_path, log=lambda t, m: log.append(f"{t:.0f} {m}"))
    w.add_node(NodeConfig("CLINIC", Role.SITE, "SA_C"), Static((0, 0)))
    w.add_node(NodeConfig("HOSP", Role.HUB, "SA_H"), Static((1000, 0)))
    w.add_node(NodeConfig("MULE", Role.MULE, None), Route(((0, 0), (1000, 0)), speed=10))
    w.start()
    s.run_until(105)
    assert log == ["0 CONTACT CLINIC <-> MULE", "90 CONTACT HOSP <-> MULE"]
    assert w.contacts == {frozenset(("HOSP", "MULE"))}


def test_rejects_duplicate_node(tmp_path: Path) -> None:
    w = World(Scheduler(seed=1), tmp_path)
    w.add_node(NodeConfig("A", Role.MULE, None), Static((0, 0)))
    with pytest.raises(ValueError):
        w.add_node(NodeConfig("A", Role.MULE, None), Static((0, 0)))
