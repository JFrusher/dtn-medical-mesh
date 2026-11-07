"""Node positions as pure functions of virtual time (metres)."""

import math
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property

Point = tuple[float, float]


@dataclass(frozen=True)
class Static:
    at: Point

    def position(self, t: float) -> Point:
        return self.at


@dataclass(frozen=True)
class Route:
    """Drive along `waypoints` at `speed` m/s, looping forever, waiting `dwell_s` at each stop.

    `stops` are waypoint indices where the vehicle waits; None means every waypoint (a simple
    stop-to-stop route). Road-following routes have many waypoints but few stops.
    """

    waypoints: tuple[Point, ...]
    speed: float
    dwell_s: float = 0.0
    start_s: float = 0.0
    stops: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if len(self.waypoints) < 2 or self.speed <= 0:
            raise ValueError("a route needs >= 2 waypoints and a positive speed")
        if self.stops is not None and not all(0 <= i < len(self.waypoints) for i in self.stops):
            raise ValueError("route stops must be waypoint indices")

    @cached_property
    def _timeline(self) -> tuple[list[float], list[tuple[Point, Point, float, float]]]:
        """Start time of each segment, and (from, to, dwell, travel) for each, over one loop."""
        stops = set(range(len(self.waypoints)) if self.stops is None else self.stops)
        pts = self.waypoints
        starts, segments, t = [], [], 0.0
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            dwell = self.dwell_s if i in stops else 0.0
            travel = math.dist(a, b) / self.speed
            starts.append(t)
            segments.append((a, b, dwell, travel))
            t += dwell + travel
        starts.append(t)  # loop length
        return starts, segments

    def position(self, t: float) -> Point:
        starts, segments = self._timeline
        remaining = max(0.0, t - self.start_s) % starts[-1]
        i = bisect_right(starts, remaining) - 1
        a, b, dwell, travel = segments[i]
        into = remaining - starts[i]
        if into < dwell or travel == 0:
            return a
        f = (into - dwell) / travel
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


Mobility = Static | Route
