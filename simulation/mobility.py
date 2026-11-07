"""Node positions as pure functions of virtual time (metres)."""

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Static:
    at: Point

    def position(self, t: float) -> Point:
        return self.at


@dataclass(frozen=True)
class Route:
    """Drive waypoint to waypoint at `speed` m/s, waiting `dwell_s` at each, looping forever."""

    waypoints: tuple[Point, ...]
    speed: float
    dwell_s: float = 0.0
    start_s: float = 0.0

    def __post_init__(self) -> None:
        if len(self.waypoints) < 2 or self.speed <= 0:
            raise ValueError("a route needs >= 2 waypoints and a positive speed")

    def position(self, t: float) -> Point:
        legs = list(zip(self.waypoints, self.waypoints[1:] + self.waypoints[:1], strict=True))
        cycle = sum(self.dwell_s + math.dist(a, b) / self.speed for a, b in legs)
        remaining = max(0.0, t - self.start_s) % cycle
        for a, b in legs:
            if remaining < self.dwell_s:
                return a
            remaining -= self.dwell_s
            travel = math.dist(a, b) / self.speed
            if remaining < travel:
                f = remaining / travel
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            remaining -= travel
        return self.waypoints[0]


Mobility = Static | Route
