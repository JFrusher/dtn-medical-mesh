"""Telemetry for the live map (spec §10.3). The emulator and, later, field TELEMETRY bundles
produce the same shape; docs/telemetry.schema.json documents it."""

from typing_extensions import TypedDict  # pydantic needs this one on Python < 3.12


class NodeView(TypedDict):
    id: str
    role: str
    sa: str | None
    x: float
    y: float
    up: bool
    battery_pct: float | None
    shards: int
    used_bytes: int
    route: list[tuple[float, float]] | None  # waypoints of a moving node, for drawing its road
    hostile: bool  # emulator only: an attacker injected by the scenario
    light: str | None  # status light (spec §11.4): green, blue, blue-flash, amber, red; None = off


# functional form because "from" is a Python keyword; bundles are bundle IDs (hex)
Transfer = TypedDict(
    "Transfer", {"t": float, "from": str, "to": str, "pieces": int, "bundles": list[str]}
)


class Stats(TypedDict):
    sent: int
    delivered: int
    in_transit: int
    not_delivered: int
    rejected: int  # forged, corrupt or unauthenticated frames refused by honest nodes


class Telemetry(TypedDict):
    t: float  # seconds since the scenario started
    geo: tuple[float, float] | None  # [lat, lon] origin of x/y metres; None = schematic plane
    speed: float
    paused: bool
    nodes: list[NodeView]
    links: list[tuple[str, str]]
    lora: list[str]  # nodes that broadcast on LoRa in the last few seconds
    transfers: list[Transfer]  # custody receipts in the last few seconds: pieces that just moved
    events: list[str]  # recent log lines, each starting with its [hh:mm:ss] time
    stats: Stats
