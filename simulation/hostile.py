"""Hostile nodes for the emulator (spec §12.2): they run the real core plus attacks.

attacks:
  forge_shards    re-send held shards with a tampered header (destination changed)
  corrupt_shards  re-send held shards with a flipped data bit
  forge_acks      ACK every bundle it has seen, signed with its own key
  replay          re-send every frame it has captured
  blackhole       advertise a perfect route everywhere, take custody, then discard
"""

from collections.abc import Mapping
from dataclasses import replace

import cbor2

from node.bundle import Ack, encode_ack, encode_shard
from node.core import NodeCore, _summary_signing_bytes
from node.events import Action, ContactUp, Event, FrameReceived, SendFrame, Store
from node.hw.radio import RadioKind

ATTACKS = frozenset({"forge_shards", "corrupt_shards", "forge_acks", "replay", "blackhole"})
REPLAY_CAP = 500  # unique frames kept: two replayers would otherwise feed each other forever


class HostileCore(NodeCore):
    attacks: frozenset[str] = frozenset()

    def handle(self, event: Event) -> list[Action]:
        if not hasattr(self, "_captured"):
            self._captured: dict[bytes, None] = {}  # insertion-ordered set
            self._seen_sas: set[str] = set()
        actions = super().handle(event)
        wifi = isinstance(event, FrameReceived) and event.radio is RadioKind.WIFI
        if wifi and len(self._captured) < REPLAY_CAP:
            self._captured[event.frame] = None  # type: ignore[union-attr]
        self._seen_sas |= {s.header.dst_sa for s in self.shards.values()}
        if "blackhole" in self.attacks:
            stored = {a.key for a in actions if isinstance(a, Store) and a.table == "shards"}
            actions += [self._remove_shard(self.shards[k]) for k in stored if k in self.shards]
        if isinstance(event, ContactUp) and event.radio is RadioKind.WIFI:
            actions += self._attack(event.peer_id)
        return actions

    def _summary(self, peer_id: str) -> dict[str, object]:
        msg = super()._summary(peer_id)
        if "blackhole" in self.attacks:  # lure: "I reach every service area"
            msg["prophet"] = {sa: 0.99 for sa in sorted(self._seen_sas | set(self.router.p))}
            msg["sig"] = self.identity.sign(_summary_signing_bytes(msg))
        return msg

    def _attack(self, peer: str) -> list[Action]:
        out: list[Action] = []
        for s in self.shards.values():
            if "forge_shards" in self.attacks:
                bad = replace(s, header=replace(s.header, dst_sa="SA_ATTACKER"))
                out.append(self._raw(peer, {"t": "shard", "s": encode_shard(bad), "age": 0}))
            if "corrupt_shards" in self.attacks:
                bad = replace(s, data=bytes([s.data[0] ^ 1]) + s.data[1:])
                out.append(self._raw(peer, {"t": "shard", "s": encode_shard(bad), "age": 0}))
        if "forge_acks" in self.attacks:
            for s in self.shards.values():
                ack = Ack(s.header.bundle_id, s.header.dst_sa, self.identity.cert.cert_id)
                ack = replace(ack, sig=self.identity.sign(ack.signing_bytes()))
                out.append(self._raw(peer, {"t": "ack", "a": encode_ack(ack)}))
        if "replay" in self.attacks:
            out += [SendFrame(peer_id=peer, radio=RadioKind.WIFI, frame=f) for f in self._captured]
        return out

    def _raw(self, peer: str, msg: Mapping[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))
