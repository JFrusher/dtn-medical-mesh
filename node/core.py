"""NodeCore: the sans-IO node state machine (spec §9.1).

handle(event) returns actions; it never touches clocks, radios, disk or randomness.
State is rebuilt from storage records on boot, so a power cut loses nothing committed.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

import cbor2

from node.bundle import Bundle, FormatError, decode_bundle, encode_bundle
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

T_BUNDLES = "bundles"
T_ACKS = "acks"
T_ORIGIN = "origin"  # bundles injected here, awaiting a delivery ACK


class Role(Enum):
    HUB = "hub"
    SITE = "site"
    MULE = "mule"
    RELAY = "relay"


@dataclass(frozen=True)
class NodeConfig:
    node_id: str
    role: Role
    sa: str | None  # service area served; None for mules and relays

    def __post_init__(self) -> None:
        if (self.role in (Role.HUB, Role.SITE)) != (self.sa is not None):
            raise ValueError(f"{self.node_id}: hubs and sites need an SA, mules/relays must not")


class NodeCore:
    def __init__(self, config: NodeConfig, records: dict[str, dict[bytes, bytes]]) -> None:
        self.config = config
        self.bundles: dict[bytes, Bundle] = {
            k: decode_bundle(v) for k, v in records.get(T_BUNDLES, {}).items()
        }
        self.acks: set[bytes] = set(records.get(T_ACKS, {}))
        self.origin: set[bytes] = set(records.get(T_ORIGIN, {}))
        self.dropped = Counter[str]()
        self.peer_has: dict[str, set[bytes]] = {}  # connected peers' bundles, from their summary
        self.peer_acks: dict[str, set[bytes]] = {}

    def handle(self, event: Event) -> list[Action]:
        match event:
            case ContactUp(radio=RadioKind.WIFI):
                summary = {"t": "summary", "ids": sorted(self.bundles), "acks": sorted(self.acks)}
                return [self._send(event.peer_id, summary)]
            case ContactDown(radio=RadioKind.WIFI):
                self.peer_has.pop(event.peer_id, None)
                self.peer_acks.pop(event.peer_id, None)
                return []
            case FrameReceived(radio=RadioKind.WIFI):
                return self._on_frame(event.peer_id, event.frame)
            case Inject():
                bundle = decode_bundle(event.bundle)
                if bundle.header.bundle_id in self.bundles.keys() | self.acks:
                    return []
                self.origin.add(bundle.header.bundle_id)
                stored = Store(table=T_ORIGIN, key=bundle.header.bundle_id, value=b"")
                return [stored, *self._accept(bundle, source=None)]
            case EndpointAck():
                return self._on_ack(event.bundle_id, source=None)
            case _:
                return []

    def _on_frame(self, peer_id: str, frame: bytes) -> list[Action]:
        try:
            msg = cbor2.loads(frame)
            kind = msg["t"]
            if kind == "summary":
                self.peer_has[peer_id] = set(msg["ids"])
                self.peer_acks[peer_id] = set(msg["acks"])
                return self._offer(peer_id)
            if kind == "ack":
                return self._on_ack(_bytes(msg["id"]), source=peer_id)
            if kind == "bundle":
                return self._accept(decode_bundle(msg["b"]), source=peer_id)
            raise FormatError(f"unknown frame type {kind!r}")
        except (FormatError, KeyError, TypeError, cbor2.CBORDecodeError):
            self.dropped["malformed"] += 1
            return []

    def _offer(self, peer_id: str) -> list[Action]:
        known_acks = self.peer_acks[peer_id]
        new_acks = sorted(self.acks - known_acks)
        known_acks.update(new_acks)
        actions: list[Action] = [self._send(peer_id, {"t": "ack", "id": a}) for a in new_acks]
        has = self.peer_has[peer_id]
        missing = [b for i, b in self.bundles.items() if i not in has]
        missing.sort(key=lambda b: (b.header.priority, b.header.created))
        has.update(b.header.bundle_id for b in missing)
        return actions + [
            self._send(peer_id, {"t": "bundle", "b": encode_bundle(b)}) for b in missing
        ]

    def _accept(self, bundle: Bundle, source: str | None) -> list[Action]:
        bid = bundle.header.bundle_id
        if source in self.peer_has:
            self.peer_has[source].add(bid)
        if bid in self.bundles or bid in self.acks:
            return []
        self.bundles[bid] = bundle
        encoded = encode_bundle(bundle)
        actions: list[Action] = [Store(table=T_BUNDLES, key=bid, value=encoded)]
        if bundle.header.dst_sa == self.config.sa:
            actions.append(Deliver(bundle=encoded))
        for peer in sorted(self.peer_has):  # contacts already open get it straight away
            actions += self._offer(peer)
        return actions

    def _on_ack(self, bid: bytes, source: str | None) -> list[Action]:
        if source in self.peer_acks:
            self.peer_acks[source].add(bid)
        if bid in self.acks:
            return []
        self.acks.add(bid)
        actions: list[Action] = [Store(table=T_ACKS, key=bid, value=b"")]
        if self.bundles.pop(bid, None):
            actions.append(Delete(table=T_BUNDLES, key=bid))
        if bid in self.origin:
            self.origin.discard(bid)
            actions += [Delete(table=T_ORIGIN, key=bid), Delivered(bundle_id=bid)]
        for peer in sorted(self.peer_has):
            actions += self._offer(peer)
        return actions

    def _send(self, peer_id: str, msg: Mapping[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer_id, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))


def _bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise FormatError(f"expected bytes, got {type(value).__name__}")
    return value
