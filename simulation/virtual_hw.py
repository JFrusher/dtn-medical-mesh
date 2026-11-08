"""Virtual stand-ins for node hardware (implement the node.hw interfaces)."""

import math
import random
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from node.hw.power import PowerState
from node.hw.rtc import RtcError
from simulation.scheduler import Scheduler

_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
SIM_EPOCH = 1_798_761_600.0  # 2027-01-01T00:00:00Z: wall time at virtual t=0


class SeededEntropy:
    """Deterministic randomness for reproducible runs. Never used by production code."""

    def __init__(self, seed: int | str) -> None:
        self._rng = random.Random(seed)

    def random_bytes(self, n: int) -> bytes:
        return self._rng.randbytes(n)


class SoftwareSecureElement:
    """P-256 key and data slots held in process memory. There is no key export method."""

    def __init__(self, entropy: SeededEntropy, slots: int = 4) -> None:
        scalar = int.from_bytes(entropy.random_bytes(32)) % (_P256_ORDER - 1) + 1
        self.__key = ec.derive_private_key(scalar, ec.SECP256R1())
        self.__slots = [entropy.random_bytes(32) for _ in range(slots)]

    def public_key(self) -> bytes:
        return self.__key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

    def sign(self, message: bytes) -> bytes:
        der = self.__key.sign(message, ec.ECDSA(hashes.SHA256(), deterministic_signing=True))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32) + s.to_bytes(32)

    def read_slot(self, slot: int) -> bytes:
        return self.__slots[slot]


class VirtualRtc:
    """Wall clock derived from scheduler time, with injectable faults (spec §12.2)."""

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler
        self.failed = False
        self.offset_s = 0.0
        self.drift_ppm = 0.0

    def read(self) -> float:
        if self.failed:
            raise RtcError("virtual RTC failed")
        now = self._scheduler.now
        return SIM_EPOCH + now + self.offset_s + now * self.drift_ppm * 1e-6

    def reset_to_epoch(self) -> None:
        """Coin cell dead and power lost: the clock restarts from 1970."""
        self.offset_s = -(SIM_EPOCH + self._scheduler.now)


class MainsPower:
    def state(self) -> PowerState:
        return PowerState(external_power=True, battery_pct=None)


@dataclass
class Battery:
    """Battery-backed supply for solar or vehicle nodes (spec §11.2, §12.2).

    solar: charged by a day curve (peak at noon, nothing 18:00-06:00).
    vehicle: charged while the engine runs (the vehicle is moving), otherwise the UPS drains.
    """

    kind: str  # "solar" or "vehicle"
    capacity_wh: float
    load_w: float
    solar_peak_w: float = 0.0
    charge_w: float = 10.0  # vehicle alternator via the DC-DC converter
    level_wh: float = -1.0  # starts full

    def __post_init__(self) -> None:
        if self.kind not in ("solar", "vehicle"):
            raise ValueError(f"unknown power kind {self.kind!r}")
        if self.level_wh < 0:
            self.level_wh = self.capacity_wh

    def external(self, t: float, moving: bool) -> bool:
        return moving if self.kind == "vehicle" else self.solar_w(t) > 0

    def solar_w(self, t: float) -> float:
        hour = (t / 3600) % 24
        return self.solar_peak_w * max(0.0, math.sin(math.pi * (hour - 6) / 12))

    def step(self, t: float, dt: float, moving: bool) -> None:
        if self.kind == "vehicle":
            supply = self.charge_w if moving else 0.0
        else:
            supply = self.solar_w(t)
        self.level_wh = min(
            self.capacity_wh, max(0.0, self.level_wh + (supply - self.load_w) * dt / 3600)
        )

    @property
    def pct(self) -> float:
        return 100 * self.level_wh / self.capacity_wh
