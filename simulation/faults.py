"""Fault injection for scenarios (spec §12.2): clocks, power and hostile behaviour."""

from dataclasses import dataclass
from functools import partial
from typing import Any

from simulation.world import World

RTC_FAULTS = ("rtc_reset", "rtc_fail", "rtc_drift")
POWER_FAULTS = ("power_cut",)  # value: outage in seconds


@dataclass(frozen=True)
class FaultSpec:
    at_s: float
    node: str
    kind: str
    value: float = 0.0  # drift ppm, or outage seconds for power faults

    @classmethod
    def parse(cls, raw: dict[str, Any], node_ids: set[str]) -> "FaultSpec":
        unknown = set(raw) - {"at_s", "node", "kind", "value"}
        if unknown:
            raise ValueError(f"fault: unknown keys {sorted(unknown)}")
        if raw["node"] not in node_ids:
            raise ValueError(f"fault refers to unknown node {raw['node']}")
        if raw["kind"] not in KINDS:
            raise ValueError(f"unknown fault kind {raw['kind']!r}; known: {sorted(KINDS)}")
        return cls(float(raw["at_s"]), raw["node"], raw["kind"], float(raw.get("value", 0)))


def apply(world: World, fault: FaultSpec) -> None:
    node = world.nodes[fault.node]
    world.log(world.scheduler.now, f"FAULT {fault.kind} {fault.node}")
    if fault.kind == "rtc_reset":
        node.rtc.reset_to_epoch()
    elif fault.kind == "rtc_fail":
        node.rtc.failed = True
    elif fault.kind == "rtc_drift":
        node.rtc.drift_ppm = fault.value
    elif fault.kind == "power_cut":
        world.power_off(node)
        world.scheduler.after(fault.value, partial(world.power_on, node))


KINDS = frozenset(RTC_FAULTS + POWER_FAULTS)
