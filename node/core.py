"""NodeCore: the sans-IO node state machine (spec §9.1).

handle(event) returns actions; it never touches clocks, radios, disk or randomness.
State is rebuilt from storage records on boot, so a power cut loses nothing committed.
"""

from collections import Counter
from dataclasses import dataclass
from enum import Enum

import cbor2

from node.bundle import Bundle, FormatError, decode_bundle, encode_bundle
from node.events import (
    Action,
    ContactUp,
    Deliver,
    Event,
    FrameReceived,
    Inject,
    SendFrame,
    Store,
)
from node.hw.radio import RadioKind

T_BUNDLES = "bundles"


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
        self.dropped = Counter[str]()

    def handle(self, event: Event) -> list[Action]:
        match event:
            case ContactUp(radio=RadioKind.WIFI):
                return [self._send(event.peer_id, {"t": "summary", "ids": sorted(self.bundles)})]
            case FrameReceived(radio=RadioKind.WIFI):
                return self._on_frame(event.peer_id, event.frame)
            case Inject():
                return self._accept(decode_bundle(event.bundle))
            case _:
                return []

    def _on_frame(self, peer_id: str, frame: bytes) -> list[Action]:
        try:
            msg = cbor2.loads(frame)
            kind = msg["t"]
            if kind == "summary":
                return self._offer(peer_id, set(msg["ids"]))
            if kind == "bundle":
                return self._accept(decode_bundle(msg["b"]))
            raise FormatError(f"unknown frame type {kind!r}")
        except (FormatError, KeyError, TypeError, cbor2.CBORDecodeError):
            self.dropped["malformed"] += 1
            return []

    def _offer(self, peer_id: str, peer_has: set[bytes]) -> list[Action]:
        missing = [b for i, b in self.bundles.items() if i not in peer_has]
        missing.sort(key=lambda b: (b.header.priority, b.header.created))
        return [self._send(peer_id, {"t": "bundle", "b": encode_bundle(b)}) for b in missing]

    def _accept(self, bundle: Bundle) -> list[Action]:
        bid = bundle.header.bundle_id
        if bid in self.bundles:
            return []
        self.bundles[bid] = bundle
        encoded = encode_bundle(bundle)
        actions: list[Action] = [Store(table=T_BUNDLES, key=bid, value=encoded)]
        if bundle.header.dst_sa == self.config.sa:
            actions.append(Deliver(bundle=encoded))
        return actions

    def _send(self, peer_id: str, msg: dict[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer_id, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))
