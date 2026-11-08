"""Bloom filter for the opening summary (spec §8.3): one small frame listing what a node holds.

A false positive makes a node wrongly believe its peer has an item, which can only delay that
item; the exact-list follow-up and the sender's retained copy prevent any loss (REQ-PROTO-02).
"""

import hashlib
import math
from collections.abc import Iterable

import cbor2

from node.bundle import FormatError

FALSE_POSITIVE_RATE = 0.01
MAX_BITS = 8 * 1024 * 1024  # 1 MB filter; refuse anything larger from a peer


class Bloom:
    def __init__(self, bits: int, hashes: int, data: bytes | None = None) -> None:
        if not 8 <= bits <= MAX_BITS or bits % 8 or not 1 <= hashes <= 32:
            raise FormatError("bad Bloom filter parameters")
        self.bits, self.hashes = bits, hashes
        self.data = bytearray(data if data is not None else bits // 8)
        if len(self.data) != bits // 8:
            raise FormatError("Bloom filter data does not match its size")

    @classmethod
    def of(cls, items: Iterable[bytes]) -> "Bloom":
        items = list(items)
        n = max(len(items), 1)
        bits = math.ceil(-n * math.log(FALSE_POSITIVE_RATE) / math.log(2) ** 2 / 8) * 8
        bloom = cls(max(bits, 64), max(1, round(bits / n * math.log(2))))
        for item in items:
            bloom.add(item)
        return bloom

    def _positions(self, item: bytes) -> list[int]:
        h = hashlib.sha256(item).digest()
        a, b = int.from_bytes(h[:8]), int.from_bytes(h[8:16]) | 1
        return [(a + i * b) % self.bits for i in range(self.hashes)]

    def add(self, item: bytes) -> None:
        for p in self._positions(item):
            self.data[p // 8] |= 1 << (p % 8)

    def __contains__(self, item: bytes) -> bool:
        return all(self.data[p // 8] >> (p % 8) & 1 for p in self._positions(item))

    def encode(self) -> bytes:
        return cbor2.dumps([self.bits, self.hashes, bytes(self.data)])

    @classmethod
    def decode(cls, raw: bytes) -> "Bloom":
        try:
            bits, hashes, data = cbor2.loads(raw)
            return cls(int(bits), int(hashes), bytes(data))
        except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
            raise FormatError(str(e)) from e
