"""Radio links between virtual nodes (spec §12.2).

Defaults are assumptions for a Pi Zero 2 W until the H1 bench measures them.
"""

import math
import random
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RadioParams:
    wifi_range_m: float = 100.0
    wifi_setup_s: float = 2.0  # BLE discovery to data flowing (spec target, §8.2)
    wifi_bytes_per_s: float = 1_500_000.0  # per direction
    lora_range_m: float = 2000.0
    lora_sf: int = 9
    lora_bw_hz: float = 125_000.0
    lora_cr: int = 1  # coding rate 4/(4+cr)
    lora_duty_cycle: float = 0.01  # EU868 sub-band limit; set per deployment region
    # Gilbert-Elliott burst loss per link (per frame): off by default.
    p_good_to_bad: float = 0.0
    p_bad_to_good: float = 0.3
    loss_in_good: float = 0.0
    loss_in_bad: float = 0.5
    bit_flip_per_frame: float = 0.0  # chance a delivered frame has one bit flipped


@dataclass
class Link:
    """A Wi-Fi contact. `generation` changes each time it goes down, so frames that were on
    the air or queued when it dropped are lost instead of arriving on a later contact."""

    generation: int = 0
    up: bool = False
    bad: bool = False  # Gilbert-Elliott channel state
    busy_until: dict[str, float] = field(default_factory=dict)  # per sending node


def lora_airtime_s(payload_bytes: int, sf: int, bw_hz: float, cr: int) -> float:
    """Semtech AN1200.13: 8-symbol preamble, explicit header, CRC on."""
    t_sym = (1 << sf) / bw_hz
    low_rate = 1 if t_sym > 0.016 else 0
    num = 8 * payload_bytes - 4 * sf + 28 + 16
    payload_symbols = 8 + max(math.ceil(num / (4 * (sf - 2 * low_rate))) * (cr + 4), 0)
    return (8 + 4.25) * t_sym + payload_symbols * t_sym


@dataclass
class DutyCycle:
    """A node's LoRa airtime over the last hour, against its regional budget."""

    used: deque[tuple[float, float]] = field(default_factory=deque)  # (sent_at, airtime)

    def admit(self, now: float, airtime: float, duty: float) -> bool:
        while self.used and self.used[0][0] <= now - 3600:
            self.used.popleft()
        if sum(a for _, a in self.used) + airtime > duty * 3600:
            return False
        self.used.append((now, airtime))
        return True


def transfer_s(n_bytes: int, params: RadioParams) -> float:
    return n_bytes / params.wifi_bytes_per_s


def channel(link: Link, frame: bytes, params: RadioParams, rng: random.Random) -> bytes | None:
    """Pass a frame through the link's channel: None if lost, else possibly with a flipped bit."""
    flip = params.p_bad_to_good if link.bad else params.p_good_to_bad
    if rng.random() < flip:
        link.bad = not link.bad
    if rng.random() < (params.loss_in_bad if link.bad else params.loss_in_good):
        return None
    if frame and rng.random() < params.bit_flip_per_frame:
        i = rng.randrange(len(frame) * 8)
        corrupted = bytearray(frame)
        corrupted[i // 8] ^= 1 << (i % 8)
        return bytes(corrupted)
    return frame
