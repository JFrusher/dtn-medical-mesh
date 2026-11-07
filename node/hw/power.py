"""Power source and UPS (spec §11.2). Loss of input is delivered to the core as a PowerLow event."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PowerState:
    external_power: bool
    battery_pct: float | None  # None when there is no battery


class PowerSource(Protocol):
    def state(self) -> PowerState: ...
