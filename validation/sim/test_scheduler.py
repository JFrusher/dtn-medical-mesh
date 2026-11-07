import pytest

from simulation.scheduler import Scheduler


def test_runs_in_time_then_fifo_order() -> None:
    s = Scheduler(seed=1)
    log: list[str] = []
    s.schedule(5, lambda: log.append("b"))
    s.schedule(1, lambda: log.append("a"))
    s.schedule(5, lambda: log.append("c"))
    s.run_until(10)
    assert log == ["a", "b", "c"]
    assert s.now == 10


def test_events_can_schedule_events_and_cancel() -> None:
    s = Scheduler(seed=1)
    log: list[float] = []
    h = s.after(3, lambda: log.append(-1))
    s.after(1, lambda: s.after(1, lambda: log.append(s.now)))
    h.cancel()
    s.run_until(2.5)
    assert log == [2.0]
    s.run_until(4)
    assert log == [2.0]


def test_rejects_past_and_seeds_rng() -> None:
    s = Scheduler(seed=7)
    s.run_until(5)
    with pytest.raises(ValueError):
        s.schedule(1, lambda: None)
    assert Scheduler(seed=7).rng.random() == Scheduler(seed=7).rng.random()
