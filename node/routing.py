"""Routing (spec §8.6): epidemic per service area until warm, then PRoPHET (RFC 6693).

Predictabilities are kept per service area (SA), not per node: relays only ever route to SAs.
For transitivity the peer just met stands in at P_ENCOUNTER, so mules need no table of every
other node. Defaults follow RFC 6693; tune them with the emulator (spec §18).
"""

from dataclasses import dataclass, field

import cbor2

from node.bundle import FormatError

P_ENCOUNTER = 0.5
DELTA = 0.01
BETA = 0.9
GAMMA = 0.999
TIME_UNIT_S = 30.0
WARM_AFTER = 20  # encounters relevant to an SA before PRoPHET replaces epidemic for it


@dataclass
class Router:
    p: dict[str, float] = field(default_factory=dict)  # delivery predictability per SA
    encounters: dict[str, int] = field(default_factory=dict)
    aged_at: float = 0.0

    def warm(self, sa: str) -> bool:
        return self.encounters.get(sa, 0) >= WARM_AFTER

    def encounter(self, peer_sa: str | None, peer_table: dict[str, float], now_s: float) -> None:
        self._age(now_s)
        if peer_sa is not None:
            old = self.p.get(peer_sa, 0.0)
            self.p[peer_sa] = old + (1 - DELTA - old) * P_ENCOUNTER
            self.encounters[peer_sa] = self.encounters.get(peer_sa, 0) + 1
        for sa, theirs in peer_table.items():
            if sa == peer_sa or theirs <= 0:
                continue
            self.p[sa] = max(self.p.get(sa, 0.0), P_ENCOUNTER * theirs * BETA)
            self.encounters[sa] = self.encounters.get(sa, 0) + 1  # evidence, even if no rise

    def forward(self, dst: str, peer_sa: str | None, peer_table: dict[str, float]) -> bool:
        """Offer a shard for `dst` to this peer? Epidemic while `dst` is cold."""
        return not self.warm(dst) or self.better(dst, peer_sa, peer_table)

    def better(self, dst: str, peer_sa: str | None, peer_table: dict[str, float]) -> bool:
        """Is the peer better placed than us to deliver to `dst`?"""
        if peer_sa == dst:
            return True
        return self.warm(dst) and peer_table.get(dst, 0.0) > self.p.get(dst, 0.0)

    def _age(self, now_s: float) -> None:
        units = (now_s - self.aged_at) / TIME_UNIT_S
        if units > 0:
            factor = GAMMA**units
            self.p = {sa: v * factor for sa, v in self.p.items()}
            self.aged_at = now_s

    def encode(self) -> bytes:
        return cbor2.dumps([self.p, self.encounters, self.aged_at])

    @classmethod
    def decode(cls, raw: bytes) -> "Router":
        p, encounters, aged_at = cbor2.loads(raw)
        return cls(dict(p), dict(encounters), float(aged_at))


def peer_table(raw: object) -> dict[str, float]:
    """Validate a peer's advertised predictability table."""
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, int | float) and 0 <= v <= 1 for k, v in raw.items()
    ):
        raise FormatError("bad predictability table")
    return {k: float(v) for k, v in raw.items()}
