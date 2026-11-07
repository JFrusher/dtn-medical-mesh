"""Virtual endpoints (clinic/hospital laptops) attached to site nodes in the simulation."""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from functools import partial

from endpoint.crypto import CryptoError, EndpointKeys, Opened, open_sealed, seal
from node.bundle import Bundle, Header, Priority, decode_bundle, encode_bundle, make_bundle_id
from node.events import EndpointAck, Inject
from simulation.virtual_hw import SeededEntropy
from simulation.world import SimNode, World


@dataclass(frozen=True)
class DirectoryEntry:
    sa: str
    encryption_public: bytes


class Status(Enum):
    IN_TRANSIT = "in transit"
    DELIVERED = "delivered"


@dataclass
class SentMessage:
    recipient: str
    status: Status = Status.IN_TRANSIT


@dataclass
class VirtualEndpoint:
    name: str
    site: SimNode
    world: World
    directory: dict[str, DirectoryEntry]
    entropy: SeededEntropy
    keys: EndpointKeys = field(init=False)
    sent: dict[bytes, "SentMessage"] = field(default_factory=dict)
    inbox: list[Opened] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.site.config.sa is None:
            raise ValueError(f"endpoint {self.name} must attach to a site or hub node")
        self.keys = EndpointKeys.generate(self.entropy)
        self.directory[self.name] = DirectoryEntry(self.site.config.sa, self.keys.encryption_public)
        self.site.on_deliver = self._on_deliver
        self.site.on_delivered = self._on_delivered

    def send(self, to: str, content: bytes, priority: Priority, lifetime_s: int) -> bytes:
        entry = self.directory[to]
        payload = seal(content, entry.encryption_public, self.keys, self.entropy)
        created = int(self.world.scheduler.now * 1000)
        site_sa = self.site.config.sa
        assert site_sa is not None
        root = hashlib.sha256(payload).digest()  # shortcut: whole payload until P2.S06 shards it
        header = Header(
            make_bundle_id(root, created),
            site_sa,
            entry.sa,
            priority,
            created,
            lifetime_s,
            len(payload),
            1,
            0,
            root,
            self.site.config.node_id,
            b"",
        )
        encoded = encode_bundle(Bundle(header, payload))
        self.world.dispatch(self.site, partial(Inject, bundle=encoded))
        self.sent[header.bundle_id] = SentMessage(to)
        return header.bundle_id

    def _on_deliver(self, encoded: bytes) -> None:
        bundle = decode_bundle(encoded)
        try:
            self.inbox.append(open_sealed(bundle.payload, self.keys))
        except CryptoError:
            return  # addressed to another endpoint in this service area
        self.world.log(self.world.scheduler.now, f"OPENED by {self.name}")
        bid = bundle.header.bundle_id
        self.world.dispatch(self.site, partial(EndpointAck, bundle_id=bid))

    def _on_delivered(self, bundle_id: bytes) -> None:
        self.sent[bundle_id].status = Status.DELIVERED
