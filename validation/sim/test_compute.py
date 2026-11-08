from pathlib import Path

import pytest

from node.core import NodeConfig
from node.events import Action, Delete, PowerLow, Store
from node.pki import Role
from simulation.compute import SE_SIGN_S, Profile, cost_s, flash_bytes, profiles
from simulation.metrics import evaluate
from simulation.mobility import Static
from simulation.provision import provision
from simulation.runner import run
from simulation.scenario import load_scenario
from simulation.scheduler import Scheduler
from simulation.world import World


@pytest.mark.req("REQ-SIM-03")
def test_cost_adds_up_each_operation() -> None:
    p = Profile(verify_s=0.001, sha256_bytes_per_s=1e6, db_commit_s=0.01, db_bytes_per_s=1e6)
    actions: list[Action] = [
        Store(table="t", key=b"k", value=b"v" * 1000),
        Delete(table="t", key=b"k"),
    ]
    expected = 2 * SE_SIGN_S + 3 * 0.001 + 0.5 + 2 * 0.01 + 0.001
    assert cost_s(p, signs=2, verifies=3, received_bytes=500_000, actions=actions) == expected


def test_pi5_sites_are_faster_than_zero_2_w_carriers() -> None:
    zero, pi5 = profiles()[Role.MULE], profiles()[Role.HUB]
    assert profiles()[Role.RELAY] == zero and profiles()[Role.SITE] == pi5
    assert pi5.verify_s < zero.verify_s and pi5.db_commit_s < zero.db_commit_s


@pytest.mark.req("REQ-SIM-03")
def test_actions_wait_for_the_work_and_a_power_cut_loses_it(tmp_path: Path) -> None:
    s = Scheduler(seed=1)
    w = World(s, tmp_path)
    hub = w.add_node(NodeConfig("HUB", Role.HUB, "SA_H"), Static((0, 0)))
    relay = w.add_node(NodeConfig("RELAY", Role.RELAY, None), Static((5000, 0)))
    provision(list(w.nodes.values()), seed=1)
    w.start()
    s.run_until(1)
    stored = []
    w._apply = lambda node, action: stored.append((s.now, node.id))  # type: ignore[method-assign]

    def one_write(event: object) -> list[Action]:
        return [Store(table="t", key=b"k", value=b"x")]

    relay.core.handle = hub.core.handle = one_write  # type: ignore[method-assign]
    w.dispatch(relay, PowerLow)
    w.dispatch(relay, PowerLow)  # arrives while busy: queued behind the first
    commit = profiles()[Role.RELAY].db_commit_s
    s.run_until(1 + 3 * commit)
    assert [t for t, _ in stored] == pytest.approx([1 + commit, 1 + 2 * commit], abs=1e-4)
    w.dispatch(hub, PowerLow)
    w.power_off(hub)  # before its commit lands
    s.run_until(2)
    assert all(n == "RELAY" for _, n in stored)


def test_report_shows_how_busy_nodes_were(tmp_path: Path) -> None:
    report = evaluate(run(load_scenario("skeleton"), 1, tmp_path), 1)
    assert report.busy_pct and all(0 <= v < 100 for v in report.busy_pct.values())
    assert max(report.busy_pct.values()) > 0


@pytest.mark.req("REQ-SIM-03")
def test_flash_writes_round_up_to_pages_and_double_in_wal() -> None:
    small = flash_bytes([Store(table="t", key=b"k" * 10, value=b"v" * 100)])
    big = flash_bytes([Store(table="t", key=b"k", value=b"v" * 5000), Delete(table="t", key=b"k")])
    assert small == 4096 * 2 and big == (2 * 4096 + 4096) * 2


def test_report_projects_card_life(tmp_path: Path) -> None:
    report = evaluate(run(load_scenario("skeleton"), 1, tmp_path), 1)
    mule = report.flash["AMBULANCE_01"]
    assert mule["mb_per_day"] > 0 and mule["peak_kb_per_s"] > 0 and mule["life_years"] > 1
    assert "Projected card life" in report.to_markdown()
