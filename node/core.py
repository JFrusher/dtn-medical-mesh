"""NodeCore: the sans-IO node state machine (spec §9.1).

handle(event) returns actions; it never touches clocks, radios, disk or randomness.
State is rebuilt from storage records on boot, so a power cut loses nothing committed.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import cbor2

from node import erasure, merkle
from node.bundle import Bundle, FormatError, Shard, decode_shard, encode_bundle, encode_shard
from node.events import (
    Action,
    ContactDown,
    ContactUp,
    Delete,
    Deliver,
    Delivered,
    EndpointAck,
    Event,
    FrameReceived,
    Inject,
    SendFrame,
    Store,
)
from node.hw.radio import RadioKind
from node.pki import Identity, Role

T_SHARDS = "shards"
T_ACKS = "acks"
T_ORIGIN = "origin"  # bundles injected here, awaiting a delivery ACK
T_DELIVERED = "delivered"  # bundles handed to the attached endpoint


class StorageFull(Exception):
    """The site node cannot hold its own endpoint's new bundle; the endpoint must keep it."""


@dataclass(frozen=True)
class NodeConfig:
    node_id: str
    role: Role
    sa: str | None  # service area served; None for mules and relays
    capacity_bytes: int = 32 * 1024**3

    def __post_init__(self) -> None:
        if (self.role in (Role.HUB, Role.SITE)) != (self.sa is not None):
            raise ValueError(f"{self.node_id}: hubs and sites need an SA, mules/relays must not")


class NodeCore:
    def __init__(
        self, config: NodeConfig, records: dict[str, dict[bytes, bytes]], identity: Identity
    ) -> None:
        cert = identity.cert
        if (cert.subject, cert.role, cert.sa) != (config.node_id, config.role, config.sa):
            raise ValueError(f"{config.node_id}: certificate does not match the node config")
        self.config = config
        self.identity = identity
        self.shards: dict[bytes, Shard] = {
            k: decode_shard(v) for k, v in records.get(T_SHARDS, {}).items()
        }
        self.acks: set[bytes] = set(records.get(T_ACKS, {}))
        self.origin: set[bytes] = set(records.get(T_ORIGIN, {}))
        self.delivered: set[bytes] = set(records.get(T_DELIVERED, {}))
        self.used_bytes = sum(len(s.data) for s in self.shards.values())
        self.dropped = Counter[str]()
        self.peer_has: dict[str, set[bytes]] = {}  # connected peers' shard IDs, from their summary
        self.peer_acks: dict[str, set[bytes]] = {}

    def handle(self, event: Event) -> list[Action]:
        match event:
            case ContactUp(radio=RadioKind.WIFI):
                summary = {"t": "summary", "ids": sorted(self.shards), "acks": sorted(self.acks)}
                return [self._send(event.peer_id, summary)]
            case ContactDown(radio=RadioKind.WIFI):
                self.peer_has.pop(event.peer_id, None)
                self.peer_acks.pop(event.peer_id, None)
                return []
            case FrameReceived(radio=RadioKind.WIFI):
                return self._on_frame(event.peer_id, event.frame)
            case Inject():
                return self._inject([decode_shard(s) for s in event.shards])
            case EndpointAck():
                return self._on_ack(event.bundle_id, source=None)
            case _:
                return []

    # --- frames from peers (untrusted) -------------------------------------

    def _on_frame(self, peer_id: str, frame: bytes) -> list[Action]:
        try:
            msg = cbor2.loads(frame)
            kind = msg["t"]
            if kind == "summary":
                self.peer_has[peer_id] = {_bytes(i) for i in msg["ids"]}
                self.peer_acks[peer_id] = {_bytes(i) for i in msg["acks"]}
                return self._offer(peer_id)
            if kind == "shard":
                return self._accept(decode_shard(_bytes(msg["s"])), source=peer_id)
            if kind == "ack":
                return self._on_ack(_bytes(msg["id"]), source=peer_id)
            raise FormatError(f"unknown frame type {kind!r}")
        except (FormatError, KeyError, TypeError, cbor2.CBORDecodeError):
            self.dropped["malformed"] += 1
            return []

    def _offer(self, peer_id: str) -> list[Action]:
        """Send a connected peer every ACK and shard it lacks, most important first."""
        known_acks = self.peer_acks[peer_id]
        new_acks = sorted(self.acks - known_acks)
        known_acks.update(new_acks)
        actions: list[Action] = [self._send(peer_id, {"t": "ack", "id": a}) for a in new_acks]
        has = self.peer_has[peer_id]
        missing = [s for i, s in self.shards.items() if i not in has]
        missing.sort(key=lambda s: (s.header.priority, s.header.created, s.shard_id))
        has.update(s.shard_id for s in missing)
        return actions + [
            self._send(peer_id, {"t": "shard", "s": encode_shard(s)}) for s in missing
        ]

    # --- shards -------------------------------------------------------------

    def _inject(self, shards: list[Shard]) -> list[Action]:
        bids = {s.header.bundle_id for s in shards}
        if len(bids) != 1:
            raise ValueError("an injection carries the shards of exactly one bundle")
        bid = bids.pop()
        if not all(_intact(s) for s in shards):
            raise ValueError("endpoint injected a shard that fails verification")
        if bid in self.acks or any(s.shard_id in self.shards for s in shards):
            return []
        actions = self._make_room(shards[0], sum(len(s.data) for s in shards))
        if actions is None:
            raise StorageFull(f"{self.config.node_id} cannot hold bundle {bid.hex()[:12]}")
        self.origin.add(bid)
        actions.append(Store(table=T_ORIGIN, key=bid, value=b""))
        for s in shards:
            actions += self._store_shard(s)
        return actions + self._after_new_shards(bid)

    def _accept(self, shard: Shard, source: str | None) -> list[Action]:
        sid, bid = shard.shard_id, shard.header.bundle_id
        if source in self.peer_has:
            self.peer_has[source].add(sid)
        if sid in self.shards or bid in self.acks:
            return []
        if not _intact(shard):
            self.dropped["corrupt"] += 1
            return []
        evictions = self._make_room(shard, len(shard.data))
        if evictions is None:
            self.dropped["full"] += 1  # refusing is safe: the sender keeps its copy
            return []
        return evictions + self._store_shard(shard) + self._after_new_shards(bid)

    def _make_room(self, incoming: Shard, size: int) -> list[Action] | None:
        """Evict less important shards to fit `size` bytes, or return None and evict nothing.

        Only strictly lower classes go (TELEMETRY first, oldest first), so CRITICAL is never
        evicted for a lower class, and the origin's own copies are never evicted (§6.3, §8.5).
        """
        free = self.config.capacity_bytes - self.used_bytes
        if size <= free:
            return []
        candidates = sorted(
            (
                s
                for s in self.shards.values()
                if s.header.priority > incoming.header.priority
                and s.header.bundle_id not in self.origin
            ),
            key=lambda s: (-s.header.priority, s.header.created, s.shard_id),
        )
        victims: list[Shard] = []
        for s in candidates:
            if free >= size:
                break
            victims.append(s)
            free += len(s.data)
        if free < size:
            return None
        self.dropped["evicted"] += len(victims)
        return [self._remove_shard(s) for s in victims]

    def _store_shard(self, shard: Shard) -> list[Action]:
        self.shards[shard.shard_id] = shard
        self.used_bytes += len(shard.data)
        return [Store(table=T_SHARDS, key=shard.shard_id, value=encode_shard(shard))]

    def _remove_shard(self, shard: Shard) -> Delete:
        del self.shards[shard.shard_id]
        self.used_bytes -= len(shard.data)
        return Delete(table=T_SHARDS, key=shard.shard_id)

    def _after_new_shards(self, bid: bytes) -> list[Action]:
        actions = self._maybe_deliver(bid)
        for peer in sorted(self.peer_has):  # contacts already open get new shards straight away
            actions += self._offer(peer)
        return actions

    def _maybe_deliver(self, bid: bytes) -> list[Action]:
        held = self._shards_of(bid)
        header = held[0].header
        if header.dst_sa != self.config.sa or bid in self.delivered or len(held) < header.k:
            return []
        data = erasure.decode({s.index: s.data for s in held}, header.k, header.m)
        self.delivered.add(bid)
        return [
            Store(table=T_DELIVERED, key=bid, value=b""),
            Deliver(bundle=encode_bundle(Bundle(header, data))),
        ]

    def _shards_of(self, bid: bytes) -> list[Shard]:
        return [s for s in self.shards.values() if s.header.bundle_id == bid]

    # --- delivery ACKs ------------------------------------------------------

    def _on_ack(self, bid: bytes, source: str | None) -> list[Action]:
        if source in self.peer_acks:
            self.peer_acks[source].add(bid)
        if bid in self.acks:
            return []
        self.acks.add(bid)
        actions: list[Action] = [Store(table=T_ACKS, key=bid, value=b"")]
        actions += [self._remove_shard(s) for s in self._shards_of(bid)]
        if bid in self.origin:
            self.origin.discard(bid)
            actions += [Delete(table=T_ORIGIN, key=bid), Delivered(bundle_id=bid)]
        for peer in sorted(self.peer_has):
            actions += self._offer(peer)
        return actions

    def _send(self, peer_id: str, msg: Mapping[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer_id, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))


def _intact(shard: Shard) -> bool:
    """Shard data matches its header's Merkle root and expected length (REQ-DUR-01)."""
    h = shard.header
    return len(shard.data) * h.k == h.size_bucket and merkle.verify(
        shard.data, shard.index, h.k + h.m, list(shard.proof), h.merkle_root
    )


def _bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise FormatError(f"expected bytes, got {type(value).__name__}")
    return value
