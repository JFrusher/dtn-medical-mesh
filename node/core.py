"""NodeCore: the sans-IO node state machine (spec §9.1).

handle(event) returns actions; it never touches clocks, radios, disk or randomness.
State is rebuilt from storage records on boot, so a power cut loses nothing committed.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace

import cbor2

from node import erasure, merkle
from node.bundle import (
    Ack,
    Bundle,
    FormatError,
    Header,
    Shard,
    decode_ack,
    decode_shard,
    encode_ack,
    encode_bundle,
    encode_shard,
)
from node.crypto import verify_p256
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
from node.pki import Cert, Identity, Role, decode_cert, encode_cert, valid

T_SHARDS = "shards"
T_ACKS = "acks"
T_ORIGIN = "origin"  # bundles injected here, awaiting a delivery ACK
T_DELIVERED = "delivered"  # bundles handed to the attached endpoint
T_CERTS = "certs"
SUMMARY_MAX_SKEW_S = 3600
_SUMMARY_CONTEXT = b"dtn-medical-mesh/v1/summary"


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
        self.clock_s = 0.0  # newest RTC reading seen; replaced by the HLC in P4.S06
        self.certs: dict[str, Cert] = {
            k.decode(): decode_cert(v) for k, v in records.get(T_CERTS, {}).items()
        }
        for c in (identity.cert, identity.issuer):
            if c is not None:
                self.certs[c.cert_id] = c
        self.revoked: frozenset[str] = frozenset()
        self._verified_headers: set[bytes] = set()
        self.shards: dict[bytes, Shard] = {
            k: decode_shard(v) for k, v in records.get(T_SHARDS, {}).items()
        }
        self.acks: dict[bytes, Ack] = {k: decode_ack(v) for k, v in records.get(T_ACKS, {}).items()}
        self.origin: set[bytes] = set(records.get(T_ORIGIN, {}))
        self.delivered: set[bytes] = set(records.get(T_DELIVERED, {}))
        self.used_bytes = sum(len(s.data) for s in self.shards.values())
        self.dropped = Counter[str]()
        self.peer_has: dict[str, set[bytes]] = {}  # connected peers' shard IDs, from their summary
        self.peer_acks: dict[str, set[bytes]] = {}
        self.peer_certs: dict[str, set[str]] = {}

    def handle(self, event: Event) -> list[Action]:
        if event.wall is not None:
            self.clock_s = max(self.clock_s, event.wall)
        match event:
            case ContactUp(radio=RadioKind.WIFI):
                return [self._send(event.peer_id, self._summary(event.peer_id))]
            case ContactDown(radio=RadioKind.WIFI):
                for table in (self.peer_has, self.peer_acks, self.peer_certs):
                    table.pop(event.peer_id, None)
                return []
            case FrameReceived(radio=RadioKind.WIFI):
                return self._on_frame(event.peer_id, event.frame)
            case Inject():
                return self._inject([decode_shard(s) for s in event.shards])
            case EndpointAck():
                return self._endpoint_ack(event.bundle_id)
            case _:
                return []

    # --- frames from peers (untrusted) -------------------------------------

    def _on_frame(self, peer_id: str, frame: bytes) -> list[Action]:
        try:
            msg = cbor2.loads(frame)
            kind = msg["t"]
            if kind == "summary":
                if not self._summary_authentic(peer_id, msg):
                    self.dropped["unauthenticated_peer"] += 1
                    return []
                self.peer_has[peer_id] = {_bytes(i) for i in msg["ids"]}
                self.peer_acks[peer_id] = {_bytes(i) for i in msg["acks"]}
                self.peer_certs[peer_id] = {str(i) for i in msg["certs"]}
                return self._offer(peer_id)
            if kind == "cert":
                return self._on_cert(decode_cert(_bytes(msg["c"])))
            if kind == "shard":
                return self._accept(decode_shard(_bytes(msg["s"])), source=peer_id)
            if kind == "ack":
                return self._on_ack(decode_ack(_bytes(msg["a"])), source=peer_id)
            raise FormatError(f"unknown frame type {kind!r}")
        except (FormatError, KeyError, TypeError, cbor2.CBORDecodeError):
            self.dropped["malformed"] += 1
            return []

    def _summary(self, peer_id: str) -> dict[str, object]:
        """Opening message: what we hold, plus proof we hold our certificate's key (§8.2)."""
        body = {"ids": sorted(self.shards), "acks": sorted(self.acks), "certs": sorted(self.certs)}
        issuer = self.identity.issuer
        msg: dict[str, object] = {
            "t": "summary",
            **body,
            "from": encode_cert(self.identity.cert),
            "issuer": encode_cert(issuer) if issuer else None,
            "to": peer_id,
            "time": int(self.clock_s),
        }
        msg["sig"] = self.identity.sign(_summary_signing_bytes(msg))
        return msg

    def _summary_authentic(self, peer_id: str, msg: dict[str, object]) -> bool:
        """Peer's cert chain is valid, names this peer, and signed a summary meant for us now."""
        cert = decode_cert(_bytes(msg["from"]))
        if msg["issuer"] is not None:
            self._on_cert(decode_cert(_bytes(msg["issuer"])))
        sent_at = msg["time"]
        if not isinstance(sent_at, int):
            raise FormatError("summary time must be an integer")
        fresh = abs(sent_at - self.clock_s) <= SUMMARY_MAX_SKEW_S
        ok = (
            cert.subject == peer_id
            and msg["to"] == self.config.node_id
            and fresh
            and self._valid(cert)
            and verify_p256(cert.pubkey, _summary_signing_bytes(msg), _bytes(msg["sig"]))
        )
        if ok:
            self._on_cert(cert)
        return ok

    def _offer(self, peer_id: str) -> list[Action]:
        """Send a connected peer every ACK and shard it lacks, most important first."""
        known_acks = self.peer_acks[peer_id]
        new_acks = [self.acks[a] for a in sorted(self.acks.keys() - known_acks)]
        known_acks.update(a.bundle_id for a in new_acks)
        actions = self._certs_for(peer_id, {a.signer for a in new_acks})
        actions += [self._send(peer_id, {"t": "ack", "a": encode_ack(a)}) for a in new_acks]
        has = self.peer_has[peer_id]
        missing = [s for i, s in self.shards.items() if i not in has]
        missing.sort(key=lambda s: (s.header.priority, s.header.created, s.shard_id))
        has.update(s.shard_id for s in missing)
        actions += self._certs_for(peer_id, {s.header.injector for s in missing})
        return actions + [
            self._send(peer_id, {"t": "shard", "s": encode_shard(s)}) for s in missing
        ]

    def _certs_for(self, peer_id: str, cert_ids: set[str]) -> list[Action]:
        """Send the peer each certificate (issuer first) it needs to verify what follows."""
        known = self.peer_certs.setdefault(peer_id, set())
        out: list[Action] = []
        for cid in sorted(cert_ids):
            cert = self.certs[cid]
            for c in (self.certs.get(cert.issuer), cert):
                if c is not None and c.cert_id not in known:
                    known.add(c.cert_id)
                    out.append(self._send(peer_id, {"t": "cert", "c": encode_cert(c)}))
        return out

    def _on_cert(self, cert: Cert) -> list[Action]:
        cid = cert.cert_id
        if cid in self.certs or not self._valid(cert):
            return []
        self.certs[cid] = cert
        return [Store(table=T_CERTS, key=cid.encode(), value=encode_cert(cert))]

    def _valid(self, cert: Cert) -> bool:
        root = self.identity.root_pubkey
        return valid(cert, self.certs, root, int(self.clock_s), self.revoked)

    def _header_ok(self, header: Header) -> bool:
        """Injector is a valid site/hub in the source SA and signed this exact header."""
        key = header.bundle_id + header.sig
        if key in self._verified_headers:
            return True
        cert = self.certs.get(header.injector)
        ok = (
            cert is not None
            and cert.role in (Role.SITE, Role.HUB)
            and cert.sa == header.src_sa
            and self._valid(cert)
            and verify_p256(cert.pubkey, header.signing_bytes(), header.sig)
        )
        if ok:
            self._verified_headers.add(key)
        return ok

    # --- shards -------------------------------------------------------------

    def _inject(self, shards: list[Shard]) -> list[Action]:
        bids = {s.header.bundle_id for s in shards}
        if len(bids) != 1:
            raise ValueError("an injection carries the shards of exactly one bundle")
        bid = bids.pop()
        if not all(_intact(s) for s in shards):
            raise ValueError("endpoint injected a shard that fails verification")
        if shards[0].header.src_sa != self.config.sa:
            raise ValueError("endpoint injected a bundle for another source SA")
        header = replace(shards[0].header, injector=self.identity.cert.cert_id, sig=b"")
        header = replace(header, sig=self.identity.sign(header.signing_bytes()))
        shards = [replace(s, header=header) for s in shards]
        if self._acked(shards[0].header) or any(s.shard_id in self.shards for s in shards):
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
        if sid in self.shards or self._acked(shard.header):
            return []
        if not _intact(shard):
            self.dropped["corrupt"] += 1
            return []
        if not self._header_ok(shard.header):
            self.dropped["bad_signature"] += 1
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

    def _acked(self, header: Header) -> bool:
        ack = self.acks.get(header.bundle_id)
        return ack is not None and ack.dst_sa == header.dst_sa

    def _endpoint_ack(self, bid: bytes) -> list[Action]:
        held = self._shards_of(bid)
        if not held or self.config.sa != held[0].header.dst_sa:
            raise ValueError("endpoint acknowledged a bundle this node did not deliver")
        ack = Ack(bid, held[0].header.dst_sa, self.identity.cert.cert_id)
        return self._on_ack(replace(ack, sig=self.identity.sign(ack.signing_bytes())), None)

    def _ack_authentic(self, ack: Ack) -> bool:
        """Signed by a valid site/hub of the destination SA it names (REQ-PROTO-06)."""
        cert = self.certs.get(ack.signer)
        held = self._shards_of(ack.bundle_id)
        return (
            cert is not None
            and cert.role in (Role.SITE, Role.HUB)
            and cert.sa == ack.dst_sa
            and (not held or held[0].header.dst_sa == ack.dst_sa)
            and self._valid(cert)
            and verify_p256(cert.pubkey, ack.signing_bytes(), ack.sig)
        )

    def _on_ack(self, ack: Ack, source: str | None) -> list[Action]:
        bid = ack.bundle_id
        if source in self.peer_acks:
            self.peer_acks[source].add(bid)
        if bid in self.acks:
            return []
        if not self._ack_authentic(ack):
            self.dropped["bad_ack"] += 1
            return []
        self.acks[bid] = ack
        actions: list[Action] = [Store(table=T_ACKS, key=bid, value=encode_ack(ack))]
        actions += [self._remove_shard(s) for s in self._shards_of(bid)]
        if bid in self.origin:
            self.origin.discard(bid)
            actions += [Delete(table=T_ORIGIN, key=bid), Delivered(bundle_id=bid)]
        for peer in sorted(self.peer_has):
            actions += self._offer(peer)
        return actions

    def _send(self, peer_id: str, msg: Mapping[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer_id, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))


def _summary_signing_bytes(msg: Mapping[str, object]) -> bytes:
    body = cbor2.dumps([msg["ids"], msg["acks"], msg["certs"]])
    return _SUMMARY_CONTEXT + cbor2.dumps([msg["from"], msg["to"], msg["time"], body])


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
