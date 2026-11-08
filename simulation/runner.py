"""Run a scenario: python -m simulation.runner <scenario> --seed N"""

import argparse
import sys
import tempfile
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from simulation import faults
from simulation.compute import board
from simulation.endpoints import VirtualEndpoint
from simulation.hostile import HostileCore
from simulation.metrics import evaluate
from simulation.mobility import Route, Walk
from simulation.provision import provision, publish_directory
from simulation.scenario import MessageSpec, Scenario, load_scenario
from simulation.scheduler import Scheduler
from simulation.terrain import Terrain
from simulation.virtual_hw import Battery, SeededEntropy
from simulation.world import World


@dataclass
class RunResult:
    scenario: Scenario
    world: World
    endpoints: dict[str, VirtualEndpoint]
    log: list[str]


def run(scenario: Scenario, seed: int, data_dir: Path, echo: bool = False) -> RunResult:
    result = build(scenario, seed, data_dir, echo)
    result.world.scheduler.run_until(scenario.duration_s)
    return result


def build(scenario: Scenario, seed: int, data_dir: Path, echo: bool = False) -> RunResult:
    """Set up the world, endpoints, workload and faults at t=0 without running it."""
    lines: list[str] = []

    def log(t: float, msg: str) -> None:
        line = f"[{int(t) // 3600:02d}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}] {msg}"
        lines.append(line)
        if echo:
            print(line)

    scheduler = Scheduler(seed)
    terrain = Terrain.load(scenario.terrain) if scenario.terrain else None
    if terrain and terrain.origin != scenario.geo:
        raise ValueError(
            f"terrain {scenario.terrain} is centred on {terrain.origin}, not {scenario.geo}"
        )
    world = World(scheduler, data_dir, scenario.radio, scenario.tick_s, log, terrain)
    for spec in scenario.nodes:
        battery = (
            Battery.for_board(board(spec.config.role), **spec.battery) if spec.battery else None
        )
        mobility = spec.mobility
        if isinstance(mobility, Route | Walk):  # noisy mobility draws from the run's seed
            mobility = replace(mobility, seed=f"{seed}:{spec.config.node_id}")
        node = world.add_node(spec.config, mobility, battery)
        if spec.attacks or not spec.registered:
            node.core_class, node.attacks = HostileCore, spec.attacks
    unregistered = frozenset(s.config.node_id for s in scenario.nodes if not s.registered)
    root = provision(list(world.nodes.values()), seed, unregistered)
    world.start()
    endpoints = {
        e.name: VirtualEndpoint(
            e.name, world.nodes[e.node], world, SeededEntropy(f"{seed}:{e.name}"), root.public_key()
        )
        for e in scenario.endpoints
    }
    publish_directory(world, endpoints)

    def send(m: MessageSpec) -> None:
        endpoints[m.sender].send_message(m.recipient, m.message(), m.priority)
        log(scheduler.now, f"SENT {m.sender} -> {m.recipient} ({m.priority.name})")

    for m in scenario.messages:
        scheduler.schedule(m.at_s, partial(send, m))
    for f in scenario.faults:
        scheduler.schedule(f.at_s, partial(faults.apply, world, f))
    return RunResult(scenario, world, endpoints, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, help="write report.json and report.md here")
    parser.add_argument("--quiet", action="store_true", help="no event log, report only")
    args = parser.parse_args(argv)
    scenario = load_scenario(args.scenario)
    with tempfile.TemporaryDirectory() as tmp:
        result = run(scenario, args.seed, Path(tmp), echo=not args.quiet)
        report = evaluate(result, args.seed)
    if args.out:
        out = args.out / f"{scenario.name}-{args.seed}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(report.to_json())
        (out / "report.md").write_text(report.to_markdown())
    print(report.to_markdown())
    return 1 if report.violations else 0


if __name__ == "__main__":
    sys.exit(main())
