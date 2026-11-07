"""Hybrid Logical Clock (spec §8.9, REQ-TIME-01).

Value = wall milliseconds << 16 | counter. It follows the RTC when that is ahead, never goes
backwards, and absorbs authenticated peers' clocks, so a reset RTC cannot rewind a node.
"""

_COUNTER_BITS = 16
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


class Hlc:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    @property
    def wall_ms(self) -> int:
        return self.value >> _COUNTER_BITS

    def tick(self, physical_ms: int | None) -> int:
        """Advance for a local event; physical_ms is None when the RTC is unavailable."""
        return self._advance(self.value, physical_ms)

    def merge(self, remote: int, physical_ms: int | None) -> int:
        """Advance past a timestamp received from an authenticated peer."""
        return self._advance(max(self.value, remote), physical_ms)

    def _advance(self, base: int, physical_ms: int | None) -> int:
        wall, counter = base >> _COUNTER_BITS, base & _COUNTER_MAX
        if physical_ms is not None and physical_ms > wall:
            wall, counter = physical_ms, 0
        elif counter == _COUNTER_MAX:
            wall, counter = wall + 1, 0
        else:
            counter += 1
        self.value = max(self.value, wall << _COUNTER_BITS | counter)
        return self.value
