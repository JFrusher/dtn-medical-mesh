"""Battery-backed real-time clock (spec §8.9)."""

from typing import Protocol


class RtcError(Exception):
    """The RTC is missing, failed, or reports a time it cannot vouch for."""


class Rtc(Protocol):
    def read(self) -> float:
        """Wall-clock seconds since the Unix epoch. Raises RtcError if unavailable."""
        ...
