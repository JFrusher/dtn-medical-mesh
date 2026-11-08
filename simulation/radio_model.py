"""Radio links between virtual nodes (spec §12.2)."""

import math
import random
from collections import deque
from dataclasses import dataclass, field

from simulation.assumptions import group


@dataclass(frozen=True)
class RadioParams:
    """Values come from simulation/assumptions.yaml; build with `RadioParams.defaults()`."""

    wifi_range_m: float
    wifi_setup_s: float
    wifi_bytes_per_s: float
    lora_range_m: float
    lora_sf: int
    lora_bw_hz: float
    lora_cr: int  # coding rate 4/(4+cr)
    lora_duty_cycle: float
    # Gilbert-Elliott burst loss per link (per frame)
    p_good_to_bad: float
    p_bad_to_good: float
    loss_in_good: float
    loss_in_bad: float
    bit_flip_per_frame: float  # chance a delivered frame has one bit flipped

    @classmethod
    def defaults(cls, **overrides: float) -> "RadioParams":
        return cls(**{**group("radio"), **overrides})  # type: ignore[arg-type]


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
