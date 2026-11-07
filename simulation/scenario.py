"""Scenario files (YAML) -> typed, validated scenario. Unknown keys are errors."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from node.bundle import Priority
from node.core import NodeConfig
from node.pki import Role
from simulation.faults import FaultSpec
from simulation.hostile import ATTACKS
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
    attacks: frozenset[str] = frozenset()
    registered: bool = True


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
    size_bytes: int = 0  # pad the content to this size (photos, media); 0 = just the text

    def content(self) -> bytes:
        body = self.text.encode()
        return body + bytes(max(0, self.size_bytes - len(body)))


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
    allowed = {
        "name",
        "duration_s",
        "tick_s",
        "radio",
        "nodes",
        "endpoints",
        "messages",
        "workload",
        "faults",
    }
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
        messages=tuple(
            sorted(
                [_message(m, names) for m in raw.get("messages") or []]
                + [m for w in raw.get("workload") or [] for m in _workload(w, names)],
                key=lambda m: m.at_s,
            )
        ),
        faults=tuple(FaultSpec.parse(f, node_ids) for f in raw.get("faults") or []),
    )


def _node(raw: dict[str, Any]) -> NodeSpec:
    _keys(
        raw,
        f"node {raw.get('id')}",
        {"id", "role", "sa", "at", "route", "power", "hostile", "capacity_mb"},
    )
    hostile = raw.get("hostile") or {}
    _keys(hostile, f"hostile of {raw['id']}", {"attacks", "registered"})
    attacks = frozenset(hostile.get("attacks", []))
    if attacks - ATTACKS:
        raise ValueError(f"unknown attacks {sorted(attacks - ATTACKS)}")
    registered = bool(hostile.get("registered", True))
    battery = raw.get("power")
    if battery is not None:
        _keys(battery, f"power of {raw['id']}", {"kind", "capacity_wh", "load_w", "solar_peak_w"})
        Battery(**battery)  # validate now
    config = NodeConfig(raw["id"], Role(raw["role"]), raw.get("sa"))
    if "capacity_mb" in raw:
        config = replace(config, capacity_bytes=int(float(raw["capacity_mb"]) * 1024 * 1024))
    if ("at" in raw) == ("route" in raw):
        raise ValueError(f"node {raw['id']}: give exactly one of 'at' or 'route'")
    if "at" in raw:
        return NodeSpec(config, Static(_point(raw["at"])), battery, attacks, registered)
    r = raw["route"]
    _keys(r, f"route of {raw['id']}", {"waypoints", "speed", "dwell_s", "start_s"})
    mobility = Route(
        waypoints=tuple(_point(p) for p in r["waypoints"]),
        speed=float(r["speed"]),
        dwell_s=float(r.get("dwell_s", 0)),
        start_s=float(r.get("start_s", 0)),
    )
    return NodeSpec(config, mobility, battery, attacks, registered)


def _endpoint(raw: dict[str, Any], node_ids: set[str]) -> EndpointSpec:
    _keys(raw, "endpoint", {"name", "node"})
    if raw["node"] not in node_ids:
        raise ValueError(f"endpoint {raw['name']}: unknown node {raw['node']}")
    return EndpointSpec(raw["name"], raw["node"])


def _message(raw: dict[str, Any], names: set[str]) -> MessageSpec:
    _keys(raw, "message", {"at_s", "from", "to", "priority", "text", "size_bytes"})
    for who in (raw["from"], raw["to"]):
        if who not in names:
            raise ValueError(f"message refers to unknown endpoint {who}")
    return MessageSpec(
        at_s=float(raw["at_s"]),
        sender=raw["from"],
        recipient=raw["to"],
        priority=Priority[raw["priority"]],
        text=str(raw["text"]),
        size_bytes=int(raw.get("size_bytes", 0)),
    )


def _workload(raw: dict[str, Any], names: set[str]) -> list[MessageSpec]:
    """A periodic message stream: every `every_s` from `start_s` until `end_s`."""
    _keys(
        raw,
        "workload",
        {"from", "to", "priority", "text", "size_bytes", "every_s", "start_s", "end_s"},
    )
    start, every, end = float(raw.get("start_s", 0)), float(raw["every_s"]), float(raw["end_s"])
    if every <= 0:
        raise ValueError("workload every_s must be positive")
    base: dict[str, Any] = {k: raw[k] for k in ("from", "to", "priority") if k in raw}
    out: list[MessageSpec] = []
    i = 0
    while start + i * every <= end:
        msg: dict[str, Any] = {**base, "at_s": start + i * every, "text": f"{raw['text']} #{i + 1}"}
        if "size_bytes" in raw:
            msg["size_bytes"] = raw["size_bytes"]
        out.append(_message(msg, names))
        i += 1
    return out


def _point(p: list[float]) -> tuple[float, float]:
    x, y = p
    return (float(x), float(y))


def _keys(raw: dict[str, Any], where: str, allowed: set[str]) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
