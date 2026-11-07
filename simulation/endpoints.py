"""Virtual endpoints (clinic/hospital laptops) attached to site nodes in the simulation."""

from dataclasses import dataclass, field
from functools import partial

from endpoint.crypto import CryptoError, EndpointKeys
from endpoint.directory import DirectoryEntry, verified_entries
from endpoint.pipeline import build_shards, open_delivered
from endpoint.state import Outgoing, Status
from node.bundle import Priority, decode_bundle, encode_shard
from node.events import EndpointAck, Inject
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy
from simulation.world import SimNode, World


@dataclass(frozen=True)
class Received:
    sender: str | None  # directory name of the signing endpoint; None if not in the directory
    content: bytes


@dataclass
class VirtualEndpoint:
    name: str
    site: SimNode
    world: World
    entropy: SeededEntropy
    root_pubkey: bytes
    keys: EndpointKeys = field(init=False)
    directory: dict[str, DirectoryEntry] = field(default_factory=dict)
    sent: dict[bytes, Outgoing] = field(default_factory=dict)
    inbox: list["Received"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.site.config.sa is None:
            raise ValueError(f"endpoint {self.name} must attach to a site or hub node")
        self.keys = EndpointKeys.generate(self.entropy)
        self.site.on_deliver = self._on_deliver
        self.site.on_directory = self.update_directory
        self.site.on_delivered = self._on_delivered

    def send(self, to: str, content: bytes, priority: Priority) -> bytes:
        entry = self.directory[to]
        site_sa = self.site.config.sa
        assert site_sa is not None  # checked in __post_init__
        shards = build_shards(
            content,
            entry.encryption_public,
            self.keys,
            self.entropy,
            src_sa=site_sa,
            dst_sa=entry.sa,
            priority=priority,
            created=int((SIM_EPOCH + self.world.scheduler.now) * 1000),  # wall-clock ms
        )
        encoded = tuple(encode_shard(s) for s in shards)
        self.world.dispatch(self.site, partial(Inject, shards=encoded))
        h = shards[0].header
        out = Outgoing(to, h.bundle_id, h.created, h.lifetime_s)
        self.sent[h.bundle_id] = out
        expires_at = out.expires_ms / 1000 - SIM_EPOCH
        self.world.scheduler.schedule(expires_at + 0.001, partial(self._check_expiry, out))
        return h.bundle_id

    def status(self, bundle_id: bytes) -> Status:
        return self.sent[bundle_id].status(self._now_ms())

    def _now_ms(self) -> int:
        return int((SIM_EPOCH + self.world.scheduler.now) * 1000)

    def _check_expiry(self, out: Outgoing) -> None:
        if out.status(self._now_ms()) is Status.NOT_DELIVERED:
            self.world.log(
                self.world.scheduler.now, f"NOT DELIVERED {self.name} -> {out.recipient}"
            )

    def _on_deliver(self, encoded: bytes) -> None:
        bundle = decode_bundle(encoded)
        try:
            opened = open_delivered(bundle, self.keys)
        except CryptoError:
            return  # addressed to another endpoint in this service area
        by_key = {e.signing_public: e.name for e in self.directory.values()}
        self.inbox.append(Received(by_key.get(opened.signer), opened.content))
        self.world.log(self.world.scheduler.now, f"OPENED by {self.name}")
        bid = bundle.header.bundle_id
        self.world.dispatch(self.site, partial(EndpointAck, bundle_id=bid))

    def update_directory(self, doc: bytes, issuer: bytes) -> None:
        now = int(SIM_EPOCH + self.world.scheduler.now)
        for entry in verified_entries(doc, issuer, self.root_pubkey, now):
            self.directory[entry.name] = entry

    def _on_delivered(self, bundle_id: bytes) -> None:
        self.sent[bundle_id].delivered_ms = self._now_ms()
