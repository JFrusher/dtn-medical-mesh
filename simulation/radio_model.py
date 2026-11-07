"""Radio links between virtual nodes (spec §12.2).

Defaults are assumptions for a Pi Zero 2 W until the H1 bench measures them.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RadioParams:
    wifi_range_m: float = 100.0
    wifi_setup_s: float = 2.0  # BLE discovery to data flowing (spec target, §8.2)
    wifi_bytes_per_s: float = 1_500_000.0  # per direction
    lora_range_m: float = 2000.0


@dataclass
class Link:
    """A Wi-Fi contact. `generation` changes each time it goes down, so frames that were on
    the air or queued when it dropped are lost instead of arriving on a later contact."""

    generation: int = 0
    up: bool = False
    busy_until: dict[str, float] = field(default_factory=dict)  # per sending node


def transfer_s(n_bytes: int, params: RadioParams) -> float:
    return n_bytes / params.wifi_bytes_per_s
