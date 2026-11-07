"""Run a scenario: python -m simulation.runner <scenario> --seed N"""

import argparse
import sys
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from simulation import faults
from simulation.endpoints import VirtualEndpoint
from simulation.hostile import HostileCore
from simulation.metrics import evaluate
from simulation.provision import provision, publish_directory
from simulation.scenario import MessageSpec, Scenario, load_scenario
from simulation.scheduler import Scheduler
from simulation.virtual_hw import Battery, SeededEntropy
from simulation.world import World


@dataclass
class RunResult:
    scenario: Scenario
    world: World
    endpoints: dict[str, VirtualEndpoint]
    log: list[str]


def run(scenario: Scenario, seed: int, data_dir: Path, echo: bool = False) -> RunResult:
    lines: list[str] = []

    def log(t: float, msg: str) -> None:
        line = f"[{int(t) // 3600:02d}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}] {msg}"
        lines.append(line)
        if echo:
            print(line)

    scheduler = Scheduler(seed)
    world = World(scheduler, data_dir, scenario.radio, scenario.tick_s, log)
    for spec in scenario.nodes:
        battery = Battery(**spec.battery) if spec.battery else None
        node = world.add_node(spec.config, spec.mobility, battery)
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
        endpoints[m.sender].send(m.recipient, m.text.encode(), m.priority)
        log(scheduler.now, f"SENT {m.sender} -> {m.recipient} ({m.priority.name})")

    for m in scenario.messages:
        scheduler.schedule(m.at_s, partial(send, m))
    for f in scenario.faults:
        scheduler.schedule(f.at_s, partial(faults.apply, world, f))
    scheduler.run_until(scenario.duration_s)
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
