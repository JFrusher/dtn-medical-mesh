"""Node positions as pure functions of virtual time (metres) and, for noisy ones, a seed."""

import math
import random
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property

from simulation.virtual_hw import SIM_EPOCH

Point = tuple[float, float]


@dataclass(frozen=True)
class Static:
    at: Point

    def position(self, t: float) -> Point:
        return self.at


# Virtual t=0 is local midnight on SIM_EPOCH's date (as for the solar curve).
EPOCH_WEEKDAY = datetime.fromtimestamp(SIM_EPOCH, UTC).weekday()  # 0 = Monday
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Schedule:
    """Real-world noise on a route's rounds (spec §12.2). All draws come from the route's seed.

    every_s: a round is due every this long (default: as soon as the last one ends).
    jitter_s: each departure is late by up to this much. traffic: a round takes up to this
    fraction longer. skip_p: chance a round does not run. breakdown_p, repair_s: chance a
    round stops part-way for repair_s. night: (from_hour, to_hour) with no departures.
    days: weekdays with rounds (0 = Monday); None = every day.
    """

    every_s: float | None = None
    jitter_s: float = 0.0
    traffic: float = 0.0
    skip_p: float = 0.0
    breakdown_p: float = 0.0
    repair_s: float = 0.0
    night: tuple[float, float] | None = None
    days: frozenset[int] | None = None


@dataclass
class _Round:
    depart: float
    factor: float  # time stretch from traffic
    skipped: bool
    break_at: float  # seconds into the round; inf if no breakdown
    break_s: float
    end: float


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
    schedule: Schedule | None = None
    seed: str = ""  # set per run and node by the runner
    _rounds: list[_Round] = field(default_factory=list, init=False, compare=False, repr=False)

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
        starts = self._timeline[0]
        if self.schedule is None:
            return self._at(max(0.0, t - self.start_s) % starts[-1])
        r = self._round(t)
        if r is None or r.skipped or t >= r.end:
            return self.waypoints[0]  # parked at the first stop
        into = t - r.depart
        if into > r.break_at:
            into = max(r.break_at, into - r.break_s)  # broken down: stays put for break_s
        return self._at(min(into / r.factor, starts[-1]))

    def _round(self, t: float) -> _Round | None:
        """The last round that departed at or before t, drawing new rounds as needed."""
        rounds, s = self._rounds, self.schedule
        assert s is not None
        loop = self._timeline[0][-1]
        while not rounds or rounds[-1].depart <= t:
            i = len(rounds)
            rng = random.Random(f"{self.seed}:{i}")
            due = self.start_s + i * (s.every_s or loop)
            depart = _allowed(
                max(due + rng.uniform(0, s.jitter_s), rounds[-1].end if rounds else 0), s
            )
            factor = 1 + rng.uniform(0, s.traffic)
            skipped = rng.random() < s.skip_p
            broken = not skipped and rng.random() < s.breakdown_p
            break_at = rng.uniform(0, loop * factor) if broken else math.inf
            break_s = s.repair_s if broken else 0.0
            end = depart if skipped else depart + loop * factor + break_s
            rounds.append(_Round(depart, factor, skipped, break_at, break_s, end))
        i = bisect_right([r.depart for r in rounds], t) - 1
        return rounds[i] if i >= 0 else None

    def _at(self, remaining: float) -> Point:
        starts, segments = self._timeline
        i = min(bisect_right(starts, remaining) - 1, len(segments) - 1)
        a, b, dwell, travel = segments[i]
        into = remaining - starts[i]
        if into < dwell or travel == 0:
            return a
        f = (into - dwell) / travel
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def _allowed(t: float, s: Schedule) -> float:
    """The first time at or after t outside the night window and on an operating day."""
    for _ in range(14 * 24):  # two weeks of hours: enough for any weekly pattern
        day, hour = int(t // 86400), (t % 86400) / 3600
        if s.days is not None and (EPOCH_WEEKDAY + day) % 7 not in s.days:
            t = (day + 1) * 86400 + (s.night[1] * 3600 if s.night else 0)
            continue
        if s.night:
            start, end = s.night
            at_night = start <= hour or hour < end if start > end else start <= hour < end
            if at_night:
                t = day * 86400 + end * 3600 + (86400 if hour >= end else 0)
                continue
        return t
    raise ValueError("schedule never allows a departure")


@dataclass(frozen=True)
class Walk:
    """An on-foot courier: walks straight between random places, dwelling a random time."""

    places: tuple[Point, ...]
    speed: float
    dwell_s: tuple[float, float]
    seed: str = ""
    _legs: list[tuple[float, Point, Point, float]] = field(  # (start, from, to, arrive)
        default_factory=list, init=False, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if len(self.places) < 2 or self.speed <= 0 or not 0 <= self.dwell_s[0] <= self.dwell_s[1]:
            raise ValueError("a walk needs >= 2 places, a positive speed and dwell [min, max]")
        if len(set(self.places)) != len(self.places):
            raise ValueError("a walk's places must be distinct")

    def position(self, t: float) -> Point:
        legs = self._legs
        while not legs or legs[-1][3] <= t:
            rng = random.Random(f"{self.seed}:{len(legs)}")
            here = legs[-1][2] if legs else self.places[0]
            start = (legs[-1][3] if legs else 0.0) + rng.uniform(*self.dwell_s)
            there = rng.choice([p for p in self.places if p != here])
            legs.append((start, here, there, start + math.dist(here, there) / self.speed))
        i = bisect_right([leg[0] for leg in legs], t) - 1
        if i < 0:
            return self.places[0]
        start, a, b, arrive = legs[i]
        f = min(1.0, (t - start) / (arrive - start))
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


Mobility = Static | Route | Walk
