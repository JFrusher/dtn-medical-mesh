"""Run a scenario: python -m simulation.runner <scenario> --seed N"""

import argparse
import sys
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from simulation import faults
from simulation.endpoints import VirtualEndpoint
from simulation.provision import provision, publish_directory
from simulation.scenario import MessageSpec, Scenario, load_scenario
from simulation.scheduler import Scheduler
from simulation.virtual_hw import SeededEntropy
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
        world.add_node(spec.config, spec.mobility)
    root = provision(list(world.nodes.values()), seed)
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
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        run(load_scenario(args.scenario), args.seed, Path(tmp), echo=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
