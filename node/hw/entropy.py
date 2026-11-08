"""Randomness source. Production uses the OS CSPRNG; seeded entropy exists only in simulation/."""

import secrets
from typing import Protocol


class Entropy(Protocol):
    def random_bytes(self, n: int) -> bytes: ...


class OsEntropy:
    def random_bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)
