"""Radio links between virtual nodes. v0: disc range, instant setup, perfect delivery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RadioParams:
    wifi_range_m: float = 100.0
