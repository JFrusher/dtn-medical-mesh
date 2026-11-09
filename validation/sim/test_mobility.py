import math
from dataclasses import replace

import pytest

from simulation.mobility import EPOCH_WEEKDAY, Route, Schedule, Walk
from simulation.scenario import ScenarioError, load_scenario

LINE = ((0.0, 0.0), (1000.0, 0.0))  # 100 s each way at 10 m/s: a 200 s loop


def route(schedule: Schedule, seed: str = "s") -> Route:
    return Route(LINE, speed=10, schedule=schedule, seed=seed)


def departures(r: Route, until: float) -> list[float]:
    """Times the vehicle leaves its first stop."""
    out, prev = [], r.position(0)
    for t in range(1, int(until)):
        here = r.position(t)
        if prev == LINE[0] and here != LINE[0]:
            out.append(t - 1.0)
        prev = here
    return out


def test_no_schedule_is_the_exact_loop() -> None:
    assert Route(LINE, speed=10).position(250) == pytest.approx((500, 0))


@pytest.mark.req("REQ-SIM-03")
def test_jitter_and_traffic_vary_rounds_reproducibly() -> None:
    noisy = route(Schedule(every_s=400, jitter_s=60, traffic=0.5))
    d = departures(noisy, 4000)
    assert len(d) == 10 and all(0 <= t - 400 * i <= 61 for i, t in enumerate(d))
    assert len({round(t - 400 * i) for i, t in enumerate(d)}) > 3  # not all equally late
    same = route(Schedule(every_s=400, jitter_s=60, traffic=0.5))
    assert [same.position(t) for t in range(0, 4000, 7)] == [
        noisy.position(t) for t in range(0, 4000, 7)
    ]
    other = route(Schedule(every_s=400, jitter_s=60, traffic=0.5), seed="other")
    assert departures(other, 4000) != d


@pytest.mark.req("REQ-SIM-03")
def test_skips_and_breakdowns() -> None:
    skipping = route(Schedule(every_s=400, skip_p=0.5))
    assert 5 < len(departures(skipping, 40_000)) < 95  # about half of 100 rounds run
    broken = route(Schedule(every_s=10_000, breakdown_p=1.0, repair_s=3000))
    stuck = [broken.position(t) for t in range(0, 3500, 10)]
    assert max(stuck.count(p) for p in set(stuck) if p != LINE[0]) >= 290  # parked ~3000 s


@pytest.mark.req("REQ-SIM-03")
def test_no_departures_at_night_or_on_closed_days() -> None:
    night = route(Schedule(every_s=3600, night=(20, 6)))
    assert all(6 <= (t % 86400) / 3600 < 20 for t in departures(night, 86400 * 2))
    weekday = EPOCH_WEEKDAY  # day 0's weekday
    closed = route(Schedule(every_s=3600, days=frozenset({(weekday + 1) % 7})))
    d = departures(closed, 86400 * 3)
    assert d and all(86400 <= t < 2 * 86400 for t in d)  # only day 1


@pytest.mark.req("REQ-SIM-03")
def test_walker_moves_between_places_at_walking_speed() -> None:
    places = ((0.0, 0.0), (1200.0, 0.0), (0.0, 1200.0))
    w = Walk(places, speed=1.2, dwell_s=(60, 600), seed="w")
    pts = [w.position(t) for t in range(0, 20_000, 5)]
    steps = [math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)]
    assert max(steps) <= 1.2 * 5 + 1e-6
    visited = {p for p in pts if p in places}
    assert len(visited) == 3
    with pytest.raises(ValueError):
        Walk((places[0], places[0]), 1.2, (0, 1))


def test_scenario_parses_schedule_and_walk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "s.yaml"
    f.write_text(
        """
name: s
duration_s: 100
nodes:
  - {id: H, role: hub, sa: SA_H, at: [0, 0]}
  - id: V
    role: mule
    route:
      waypoints: [[0, 0], [100, 0]]
      speed: 10
      schedule: {every_s: 600, jitter_s: 30, night: [20, 6], days: [mon, tue]}
  - id: W
    role: mule
    walk: {places: [[0, 0], [50, 50]], speed: 1.2, dwell_s: [10, 20]}
endpoints: [{name: h, node: H}]
"""
    )
    s = load_scenario(str(f))
    v = s.nodes[1].mobility
    assert isinstance(v, Route) and v.schedule == Schedule(
        every_s=600, jitter_s=30, night=(20, 6), days=frozenset({0, 1})
    )
    assert isinstance(s.nodes[2].mobility, Walk)
    f.write_text(f.read_text().replace("days: [mon, tue]", "days: [funday]"))
    with pytest.raises(ScenarioError, match="days"):
        load_scenario(str(f))
    assert replace(v, seed="x").seed == "x"


@pytest.mark.req("REQ-SIM-02")
def test_walker_is_reproducible_from_its_seed() -> None:
    places = ((0.0, 0.0), (1200.0, 0.0), (0.0, 1200.0))
    times = range(0, 20_000, 7)

    def walk(seed: str) -> list[tuple[float, float]]:
        w = Walk(places, speed=1.2, dwell_s=(60, 600), seed=seed)
        return [w.position(t) for t in times]

    assert walk("w") == walk("w") != walk("x")
