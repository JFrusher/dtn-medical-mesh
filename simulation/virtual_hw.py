"""Virtual stand-ins for node hardware (implement the node.hw interfaces)."""

import random

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
    """Wall clock derived from scheduler time. Faults are added in P5.S04."""

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler
        self.failed = False

    def read(self) -> float:
        if self.failed:
            raise RtcError("virtual RTC failed")
        return SIM_EPOCH + self._scheduler.now


class MainsPower:
    def state(self) -> PowerState:
        return PowerState(external_power=True, battery_pct=None)
