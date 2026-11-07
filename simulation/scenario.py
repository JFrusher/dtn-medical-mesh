"""Scenario files (YAML) -> typed, validated scenario. Unknown keys are errors."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from node.bundle import Priority
from node.core import NodeConfig
from node.pki import Role
from simulation.faults import FaultSpec
from simulation.mobility import Mobility, Route, Static
from simulation.radio_model import RadioParams
from simulation.virtual_hw import Battery

SCENARIO_DIR = Path(__file__).parent / "scenarios"


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class NodeSpec:
    config: NodeConfig
    mobility: Mobility
    battery: dict[str, Any] | None = None  # Battery(**battery) per run


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    node: str


@dataclass(frozen=True)
class MessageSpec:
    at_s: float
    sender: str
    recipient: str
    priority: Priority
    text: str


@dataclass(frozen=True)
class Scenario:
    name: str
    duration_s: float
    tick_s: float
    radio: RadioParams
    nodes: tuple[NodeSpec, ...]
    endpoints: tuple[EndpointSpec, ...]
    messages: tuple[MessageSpec, ...]
    faults: tuple[FaultSpec, ...] = ()


def load_scenario(name_or_path: str) -> Scenario:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{name_or_path}.yaml"
    raw = yaml.safe_load(path.read_text())
    try:
        return _parse(raw)
    except (KeyError, TypeError, ValueError) as e:
        raise ScenarioError(f"{path.name}: {e}") from e


def _parse(raw: dict[str, Any]) -> Scenario:
    allowed = {"name", "duration_s", "tick_s", "radio", "nodes", "endpoints", "messages", "faults"}
    _keys(raw, "scenario", allowed)
    nodes = tuple(_node(n) for n in raw["nodes"])
    node_ids = {n.config.node_id for n in nodes}
    endpoints = tuple(_endpoint(e, node_ids) for e in raw["endpoints"])
    names = {e.name for e in endpoints}
    return Scenario(
        name=raw["name"],
        duration_s=float(raw["duration_s"]),
        tick_s=float(raw.get("tick_s", 1.0)),
        radio=RadioParams(**raw.get("radio", {})),
        nodes=nodes,
        endpoints=endpoints,
        messages=tuple(_message(m, names) for m in raw.get("messages") or []),
        faults=tuple(FaultSpec.parse(f, node_ids) for f in raw.get("faults") or []),
    )


def _node(raw: dict[str, Any]) -> NodeSpec:
    _keys(raw, f"node {raw.get('id')}", {"id", "role", "sa", "at", "route", "power"})
    battery = raw.get("power")
    if battery is not None:
        _keys(battery, f"power of {raw['id']}", {"kind", "capacity_wh", "load_w", "solar_peak_w"})
        Battery(**battery)  # validate now
    config = NodeConfig(raw["id"], Role(raw["role"]), raw.get("sa"))
    if ("at" in raw) == ("route" in raw):
        raise ValueError(f"node {raw['id']}: give exactly one of 'at' or 'route'")
    if "at" in raw:
        return NodeSpec(config, Static(_point(raw["at"])), battery)
    r = raw["route"]
    _keys(r, f"route of {raw['id']}", {"waypoints", "speed", "dwell_s", "start_s"})
    mobility = Route(
        waypoints=tuple(_point(p) for p in r["waypoints"]),
        speed=float(r["speed"]),
        dwell_s=float(r.get("dwell_s", 0)),
        start_s=float(r.get("start_s", 0)),
    )
    return NodeSpec(config, mobility, battery)


def _endpoint(raw: dict[str, Any], node_ids: set[str]) -> EndpointSpec:
    _keys(raw, "endpoint", {"name", "node"})
    if raw["node"] not in node_ids:
        raise ValueError(f"endpoint {raw['name']}: unknown node {raw['node']}")
    return EndpointSpec(raw["name"], raw["node"])


def _message(raw: dict[str, Any], names: set[str]) -> MessageSpec:
    _keys(raw, "message", {"at_s", "from", "to", "priority", "text"})
    for who in (raw["from"], raw["to"]):
        if who not in names:
            raise ValueError(f"message refers to unknown endpoint {who}")
    return MessageSpec(
        at_s=float(raw["at_s"]),
        sender=raw["from"],
        recipient=raw["to"],
        priority=Priority[raw["priority"]],
        text=str(raw["text"]),
    )


def _point(p: list[float]) -> tuple[float, float]:
    x, y = p
    return (float(x), float(y))


def _keys(raw: dict[str, Any], where: str, allowed: set[str]) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
