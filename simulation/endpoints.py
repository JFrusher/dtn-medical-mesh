"""Virtual endpoints (clinic/hospital laptops) attached to site nodes in the simulation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial

from endpoint import fhir
from endpoint.crypto import CryptoError, EndpointKeys
from endpoint.directory import DirectoryEntry, verified_entries
from endpoint.fhir import Message
from endpoint.pipeline import build_shards, open_delivered
from endpoint.state import Outgoing, Status
from node.bundle import Priority, decode_bundle, encode_shard
from node.events import EndpointAck, Inject
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy
from simulation.world import SimNode, World


@dataclass(frozen=True)
class Received:
    sender: str | None  # directory name of the signing endpoint; None if not in the directory
    content: bytes  # FHIR-lite (endpoint.fhir), as signed by the sender
    bundle_id: bytes
    at_s: float  # simulation time it was opened

    @property
    def message(self) -> Message:
        return fhir.decode(self.content)


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
    pending: list[tuple[bytes, ...]] = field(default_factory=list)  # site node was down
    sent_content: dict[bytes, tuple[bytes, Priority, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.site.config.sa is None:
            raise ValueError(f"endpoint {self.name} must attach to a site or hub node")
        self.keys = EndpointKeys.generate(self.entropy)
        self.site.on_deliver = self._on_deliver
        self.site.on_directory = self.update_directory
        self.site.on_boot = self._flush_pending
        self.site.on_delivered = self._on_delivered

    def send_message(self, to: str, message: Message, priority: Priority) -> bytes:
        sent_at = datetime.fromtimestamp(self._now_ms() / 1000, UTC).isoformat()
        return self.send(to, fhir.encode(message, sent_at), priority)

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
        if self.site.up:
            self.world.dispatch(self.site, partial(Inject, shards=encoded))
        else:
            self.pending.append(encoded)  # the laptop keeps it until its node is back
        h = shards[0].header
        out = Outgoing(to, h.bundle_id, h.created, h.lifetime_s)
        self.sent_content[h.bundle_id] = (content, priority, self.world.scheduler.now)
        self.sent[h.bundle_id] = out
        expires_at = out.expires_ms / 1000 - SIM_EPOCH
        self.world.scheduler.schedule(expires_at + 0.001, partial(self._check_expiry, out))
        return h.bundle_id

    def _flush_pending(self) -> None:
        while self.pending:
            self.world.dispatch(self.site, partial(Inject, shards=self.pending.pop(0)))

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
        received = Received(
            by_key.get(opened.signer),
            opened.content,
            bundle.header.bundle_id,
            self.world.scheduler.now,
        )
        self.inbox.append(received)
        self.world.log(self.world.scheduler.now, f"OPENED by {self.name}")
        bid = bundle.header.bundle_id
        self.world.dispatch(self.site, partial(EndpointAck, bundle_id=bid))

    def update_directory(self, doc: bytes, issuer: bytes) -> None:
        now = int(SIM_EPOCH + self.world.scheduler.now)
        for entry in verified_entries(doc, issuer, self.root_pubkey, now):
            self.directory[entry.name] = entry

    def _on_delivered(self, bundle_id: bytes) -> None:
        self.sent[bundle_id].delivered_ms = self._now_ms()
