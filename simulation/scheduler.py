"""Discrete-event scheduler on a virtual clock (REQ-SIM-02).

Events at the same time run in the order they were scheduled, so a run is fully
determined by its seed.
"""

import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(order=True)
class _Entry:
    at: float
    seq: int
    fn: Callable[[], object] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class Handle:
    def __init__(self, entry: _Entry) -> None:
        self._entry = entry

    def cancel(self) -> None:
        self._entry.cancelled = True


class Scheduler:
    def __init__(self, seed: int) -> None:
        self.now = 0.0
        self.rng = random.Random(seed)
        self._queue: list[_Entry] = []
        self._seq = 0

    def schedule(self, at: float, fn: Callable[[], object]) -> Handle:
        if at < self.now:
            raise ValueError(f"cannot schedule in the past: {at} < {self.now}")
        entry = _Entry(at, self._seq, fn)
        self._seq += 1
        heapq.heappush(self._queue, entry)
        return Handle(entry)

    def after(self, delay: float, fn: Callable[[], object]) -> Handle:
        return self.schedule(self.now + delay, fn)

    def run_until(self, t: float) -> None:
        while self._queue and self._queue[0].at <= t:
            entry = heapq.heappop(self._queue)
            if entry.cancelled:
                continue
            self.now = entry.at
            entry.fn()
        self.now = max(self.now, t)
