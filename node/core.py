"""NodeCore: the sans-IO node state machine (spec §9.1).

handle(event) returns actions; it never touches clocks, radios, disk or randomness.
State is rebuilt from storage records on boot, so a power cut loses nothing committed.
"""

import hashlib
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, replace

import cbor2

from node import erasure, merkle
from node.bundle import (
    Ack,
    Bundle,
    FormatError,
    Header,
    Receipt,
    Shard,
    decode_ack,
    decode_receipt,
    decode_shard,
    encode_ack,
    encode_bundle,
    encode_receipt,
    encode_shard,
    shard_id,
)
from node.clock import Hlc
from node.crypto import verify_p256
from node.events import (
    Action,
    ContactDown,
    ContactUp,
    Delete,
    Deliver,
    Delivered,
    DirectoryUpdate,
    EndpointAck,
    Event,
    FrameReceived,
    Inject,
    PublishDirectory,
    Revoke,
    SendFrame,
    SetTimer,
    Store,
    Timer,
)
from node.hw.radio import RadioKind
from node.pki import (
    RENEW_AFTER_S,
    Cert,
    HubDoc,
    Identity,
    Role,
    decode_cert,
    decode_doc,
    doc_authentic,
    encode_cert,
    encode_doc,
    issue,
    revoked_subjects,
    sign_doc,
    valid,
)
from node.routing import Router, peer_table
from node.summary import Bloom

T_SHARDS = "shards"
T_ACKS = "acks"
T_ORIGIN = "origin"  # bundles injected here, awaiting a delivery ACK
T_DELIVERED = "delivered"  # bundles handed to the attached endpoint
T_CERTS = "certs"
T_DOCS = "docs"  # hub-signed documents: revocation lists and endpoint directories
T_IDENTITY = "identity"  # renewed certificate, replacing the provisioned one
T_META = "meta"  # node-wide values such as the last HLC
T_LEDGER = "ledger"  # receipts this node issued, hash-chained, keyed by sequence number
T_OUTBOX = "receipt_outbox"  # issued receipts not yet handed to their sender
T_RECEIPTS_IN = "receipts_in"  # receipts others gave this node, for audit
RECEIPT_BATCH_S = 0.5
LORA_MAX_FRAME = 200  # one LoRa frame (§8.10)
LORA_KINDS = frozenset({"ack", "receipt", "doc"})  # control only: never shards or summaries
BROADCAST = "*"
SWEEP_EVERY_S = 60.0  # purge expired shards at most this often, on the next event
SUMMARY_MAX_SKEW_S = 3600
EXACT_AFTER_S = 2.0  # send exact lists if the link outlives the first transfer burst (§8.3)
_SUMMARY_CONTEXT = b"dtn-medical-mesh/v1/summary"


class StorageFull(Exception):
    """The site node cannot hold its own endpoint's new bundle; the endpoint must keep it."""


@dataclass(frozen=True)
class NodeConfig:
    node_id: str
    role: Role
    sa: str | None  # service area served; None for mules and relays
    capacity_bytes: int = 32 * 1024**3
    max_new_bundles_per_injector_per_hour: int = 200

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
        renewed = records.get(T_IDENTITY, {})
        if renewed:
            cert, issuer = (decode_cert(renewed[k]) for k in (b"cert", b"issuer"))
            if cert.pubkey == identity.cert.pubkey and cert.not_after > identity.cert.not_after:
                identity = replace(identity, cert=cert, issuer=issuer)
        self.identity = identity
        meta = records.get(T_META, {})
        self.router = Router.decode(meta[b"router"]) if b"router" in meta else Router()
        stored_hlc = meta.get(b"hlc")
        self.hlc = Hlc(int.from_bytes(stored_hlc) if stored_hlc else 0)
        self._rtc_ok = False  # whether the event being handled carried an RTC reading
        self.certs: dict[str, Cert] = {
            k.decode(): decode_cert(v) for k, v in records.get(T_CERTS, {}).items()
        }
        for c in (identity.cert, identity.issuer):
            if c is not None:
                self.certs[c.cert_id] = c
        self.docs: dict[str, HubDoc] = {
            k.decode(): decode_doc(v) for k, v in records.get(T_DOCS, {}).items()
        }
        self.revoked = self._revoked()
        self._verified_headers: set[bytes] = set()
        self._mono = 0.0  # monotonic time of the event being handled
        ledger = records.get(T_LEDGER, {})
        self.ledger_seq = max((int.from_bytes(k) for k in ledger), default=0)
        last = ledger.get(self.ledger_seq.to_bytes(8))
        self.ledger_head = hashlib.sha256(last).digest() if last else bytes(32)
        self.outbox: dict[bytes, Receipt] = {
            k: decode_receipt(v) for k, v in records.get(T_OUTBOX, {}).items()
        }
        self._unreceipted: dict[str, list[bytes]] = defaultdict(list)  # sender -> shard IDs
        self.receipts_in = len(records.get(T_RECEIPTS_IN, {}))
        self._injected_at: dict[str, deque[float]] = defaultdict(deque)  # per injector cert
        self.shards: dict[bytes, Shard] = {}
        # Bundle Age (§8.9): age of our copy when we stored it, and our monotonic time then.
        # After a reboot dwell counting restarts, so age is a lower bound; the HLC check
        # catches the rest.
        self._age_at: dict[bytes, tuple[int, float]] = {}
        for k, v in records.get(T_SHARDS, {}).items():
            encoded, age_ms = cbor2.loads(v)
            self.shards[k] = decode_shard(encoded)
            self._age_at[k] = (age_ms, 0.0)
        self._last_sweep = -SWEEP_EVERY_S
        self.acks: dict[bytes, Ack] = {k: decode_ack(v) for k, v in records.get(T_ACKS, {}).items()}
        self.origin: set[bytes] = set(records.get(T_ORIGIN, {}))
        self.delivered: set[bytes] = set(records.get(T_DELIVERED, {}))
        self.used_bytes = sum(len(s.data) for s in self.shards.values())
        self.dropped = Counter[str]()
        # Per connected (authenticated) peer: its Bloom summary, plus the shard and ACK IDs we
        # know it holds because we exchanged them or it listed them exactly.
        self.peer_bloom: dict[str, Bloom] = {}
        self.peer_has: dict[str, set[bytes]] = {}
        self.peer_acks: dict[str, set[bytes]] = {}
        self.peer_certs: dict[str, set[str]] = {}
        self.peer_sa: dict[str, str | None] = {}
        self.peer_table: dict[str, dict[str, float]] = {}  # peers' PRoPHET predictabilities
        self.peer_docs: dict[str, dict[str, int]] = {}

    def handle(self, event: Event) -> list[Action]:
        self._mono = event.mono
        self._rtc_ok = event.wall is not None
        self.hlc.tick(int(event.wall * 1000) if event.wall is not None else None)
        actions = self._handle(event)
        if self._mono - self._last_sweep >= SWEEP_EVERY_S:
            self._last_sweep = self._mono
            actions += [
                self._remove_shard(s) for s in list(self.shards.values()) if self._expired(s)
            ]
        if any(isinstance(a, Store | Delete) for a in actions):
            actions.append(Store(table=T_META, key=b"hlc", value=self.hlc.value.to_bytes(16)))
        return actions

    @property
    def clock_s(self) -> float:
        """Wall-clock seconds by the HLC: what certificate and TTL checks use."""
        return self.hlc.wall_ms / 1000

    @clock_s.setter
    def clock_s(self, seconds: float) -> None:
        self.hlc.value = max(self.hlc.value, int(seconds * 1000) << 16)

    def _handle(self, event: Event) -> list[Action]:
        match event:
            case ContactUp(radio=RadioKind.WIFI):
                return [self._send(event.peer_id, self._summary(event.peer_id))]
            case ContactDown(radio=RadioKind.WIFI):
                for table in (
                    self.peer_bloom,
                    self.peer_has,
                    self.peer_acks,
                    self.peer_certs,
                    self.peer_docs,
                    self.peer_sa,
                    self.peer_table,
                ):
                    table.pop(event.peer_id, None)
                return self._issue_receipt(event.peer_id)  # anything owed goes to the outbox
            case FrameReceived(radio=RadioKind.WIFI):
                return self._on_frame(event.peer_id, event.frame)
            case FrameReceived(radio=RadioKind.LORA):
                return self._on_lora(event.peer_id, event.frame)
            case Inject():
                return self._inject([decode_shard(s) for s in event.shards])
            case EndpointAck():
                return self._endpoint_ack(event.bundle_id)
            case Timer() if event.name.startswith("receipt:"):
                return self._issue_receipt(event.name.removeprefix("receipt:"))
            case Timer() if event.name.startswith("exact:"):
                peer_id = event.name.removeprefix("exact:")
                if peer_id not in self.peer_bloom:
                    return []  # contact already over
                exact = {"t": "exact", "ids": sorted(self.shards), "acks": sorted(self.acks)}
                return [self._send(peer_id, exact)]
            case Revoke():
                return self._revoke(event.subject)
            case PublishDirectory():
                return self._publish("dir", list(event.entries))
            case _:
                return []

    # --- frames from peers (untrusted) -------------------------------------

    def _on_lora(self, peer_id: str, frame: bytes) -> list[Action]:
        """LoRa carries only small, self-verifying control messages (REQ-PROTO-09)."""
        try:
            kind = cbor2.loads(frame)["t"] if len(frame) <= LORA_MAX_FRAME else None
        except (KeyError, TypeError, cbor2.CBORDecodeError):
            kind = None
        if kind not in LORA_KINDS:
            self.dropped["lora_rejected"] += 1
            return []
        return self._on_frame(peer_id, frame)

    def _lora(self, msg: Mapping[str, object]) -> list[Action]:
        """Broadcast a control message over LoRa if it fits in one frame."""
        frame = cbor2.dumps(msg)
        if len(frame) > LORA_MAX_FRAME:
            return []
        return [SendFrame(peer_id=BROADCAST, radio=RadioKind.LORA, frame=frame)]

    def _on_frame(self, peer_id: str, frame: bytes) -> list[Action]:
        try:
            msg = cbor2.loads(frame)
            kind = msg["t"]
            if kind == "summary":
                peer_cert = self._summary_authentic(peer_id, msg)
                if peer_cert is None:
                    self.dropped["unauthenticated_peer"] += 1
                    return []
                self.peer_bloom[peer_id] = Bloom.decode(_bytes(msg["bloom"]))
                self.peer_sa[peer_id] = peer_cert.sa
                table = peer_table(msg["prophet"])
                self.peer_table[peer_id] = table
                self.router.encounter(peer_cert.sa, table, self.clock_s)
                remember = Store(table=T_META, key=b"router", value=self.router.encode())
                self.peer_has[peer_id] = set()
                self.peer_acks[peer_id] = set()
                self.peer_certs[peer_id] = {str(i) for i in msg["certs"]}
                docs = msg["docs"]
                if not isinstance(docs, dict):
                    raise FormatError("summary docs must be a map")
                self.peer_docs[peer_id] = {str(k): int(v) for k, v in docs.items()}
                follow_up = SetTimer(name=f"exact:{peer_id}", at_mono=self._mono + EXACT_AFTER_S)
                return [
                    follow_up,
                    remember,
                    *self._pay_receipts_owed(peer_id),
                    *self._renew(peer_id, peer_cert),
                    *self._offer(peer_id),
                ]
            if kind == "receipt":
                return self._on_receipt(decode_receipt(_bytes(msg["r"])))
            if kind == "exact":
                if peer_id not in self.peer_bloom:
                    return []  # only from a peer whose summary we accepted
                self.peer_has[peer_id].update(_bytes(i) for i in msg["ids"])
                self.peer_acks[peer_id].update(_bytes(i) for i in msg["acks"])
                self.peer_bloom[peer_id] = Bloom.of([])  # exact knowledge replaces the filter
                return self._offer(peer_id)
            if kind == "doc":
                return self._on_doc(decode_doc(_bytes(msg["d"])))
            if kind == "renew":
                return self._on_renewal(decode_cert(_bytes(msg["c"])))
            if kind == "cert":
                return self._on_cert(decode_cert(_bytes(msg["c"])))
            if kind == "shard":
                age = msg["age"]
                if not isinstance(age, int) or age < 0:
                    raise FormatError("shard age must be a non-negative integer")
                return self._accept(decode_shard(_bytes(msg["s"])), source=peer_id, age_ms=age)
            if kind == "ack":
                return self._on_ack(decode_ack(_bytes(msg["a"])), source=peer_id)
            raise FormatError(f"unknown frame type {kind!r}")
        except (FormatError, KeyError, TypeError, cbor2.CBORDecodeError):
            self.dropped["malformed"] += 1
            return []

    def _summary(self, peer_id: str) -> dict[str, object]:
        """Opening message: what we hold, plus proof we hold our certificate's key (§8.2)."""
        held = [b"s" + i for i in self.shards] + [b"a" + a for a in self.acks]
        body = {
            "bloom": Bloom.of(held).encode(),
            "certs": sorted(self.certs),
            "docs": {k: d.seq for k, d in sorted(self.docs.items())},
            "prophet": {sa: round(p, 4) for sa, p in sorted(self.router.p.items())},
        }
        issuer = self.identity.issuer
        msg: dict[str, object] = {
            "t": "summary",
            **body,
            "from": encode_cert(self.identity.cert),
            "issuer": encode_cert(issuer) if issuer else None,
            "to": peer_id,
            "hlc": self.hlc.value,
        }
        msg["sig"] = self.identity.sign(_summary_signing_bytes(msg))
        return msg

    def _summary_authentic(self, peer_id: str, msg: dict[str, object]) -> Cert | None:
        """Peer's cert chain is valid, names this peer, and signed a summary meant for us now."""
        cert = decode_cert(_bytes(msg["from"]))
        issuer = decode_cert(_bytes(msg["issuer"])) if msg["issuer"] is not None else None
        peer_hlc = msg["hlc"]
        if not isinstance(peer_hlc, int):
            raise FormatError("summary hlc must be an integer")
        peer_s = (peer_hlc >> 16) / 1000
        if self._rtc_ok:
            fresh, at = abs(peer_s - self.clock_s) <= SUMMARY_MAX_SKEW_S, self.clock_s
        else:
            # Our RTC is down, so our clock may be far behind: judge the peer's chain at the
            # time it claims. It can only move us forward within a valid certificate's window.
            fresh, at = True, max(peer_s, self.clock_s)
        known = {**self.certs, **({issuer.cert_id: issuer} if issuer else {})}
        root = self.identity.root_pubkey
        ok = (
            cert.subject == peer_id
            and msg["to"] == self.config.node_id
            and fresh
            and valid(cert, known, root, int(at), self.revoked)
            and verify_p256(cert.pubkey, _summary_signing_bytes(msg), _bytes(msg["sig"]))
        )
        if not ok:
            return None
        self.hlc.merge(peer_hlc, None)
        for c in (issuer, cert):
            if c is not None:
                self._on_cert(c)
        return cert

    def _renew(self, peer_id: str, peer_cert: Cert) -> list[Action]:
        """A hub reissues a docked node's certificate once it is a day old (§7.6)."""
        now = int(self.clock_s)
        if (
            self.config.role is not Role.HUB
            or peer_cert.role is Role.HUB
            or now - peer_cert.not_before < RENEW_AFTER_S
        ):
            return []
        c = peer_cert
        mine = self.identity.cert
        fresh = issue(self.identity.sign, mine.cert_id, c.subject, c.role, c.sa, c.pubkey, now)
        return [
            *self._certs_for(peer_id, {mine.cert_id}),
            self._send(peer_id, {"t": "renew", "c": encode_cert(fresh)}),
        ]

    def _on_renewal(self, cert: Cert) -> list[Action]:
        mine = self.identity.cert
        issuer = self.certs.get(cert.issuer)
        if not (
            (cert.subject, cert.role, cert.sa, cert.pubkey)
            == (mine.subject, mine.role, mine.sa, mine.pubkey)
            and cert.not_after > mine.not_after
            and issuer is not None
            and self._valid(cert)
        ):
            self.dropped["bad_renewal"] += 1
            return []
        self.identity = replace(self.identity, cert=cert, issuer=issuer)
        self.certs[cert.cert_id] = cert
        return [
            Store(table=T_IDENTITY, key=b"cert", value=encode_cert(cert)),
            Store(table=T_IDENTITY, key=b"issuer", value=encode_cert(issuer)),
        ]

    def _offer(self, peer_id: str) -> list[Action]:
        """Send a connected peer every revocation, ACK and shard it lacks, most important first."""
        actions = self._docs_for(peer_id)
        bloom = self.peer_bloom[peer_id]
        known_acks = self.peer_acks[peer_id]
        new_acks = [
            self.acks[a] for a in sorted(self.acks) if a not in known_acks and b"a" + a not in bloom
        ]
        known_acks.update(a.bundle_id for a in new_acks)
        actions += self._certs_for(peer_id, {a.signer for a in new_acks})
        actions += [self._send(peer_id, {"t": "ack", "a": encode_ack(a)}) for a in new_acks]
        has = self.peer_has[peer_id]
        peer_sa = self.peer_sa.get(peer_id)
        table = self.peer_table.get(peer_id, {})
        missing = [
            s
            for i, s in self.shards.items()
            if i not in has
            and b"s" + i not in bloom
            and not self._expired(s)
            and self.router.forward(s.header.dst_sa, peer_sa, table)
        ]

        def still_needed_by_peer(h: Header) -> int:
            if h.dst_sa != peer_sa:
                return h.k
            held = sum(1 for i in range(h.k + h.m) if _held(shard_id(h.bundle_id, i), has, bloom))
            return max(h.k - held, 0)

        missing.sort(
            key=lambda s: (
                s.header.priority,
                still_needed_by_peer(s.header),
                s.header.size_bucket,
                s.header.created,
                s.shard_id,
            )
        )
        has.update(s.shard_id for s in missing)
        actions += self._certs_for(peer_id, {s.header.injector for s in missing})
        return actions + [
            self._send(peer_id, {"t": "shard", "s": encode_shard(s), "age": self._age_ms(s)})
            for s in missing
        ]

    def _docs_for(self, peer_id: str) -> list[Action]:
        known = self.peer_docs.setdefault(peer_id, {})
        newer = [d for k, d in sorted(self.docs.items()) if known.get(k, -1) < d.seq]
        out = self._certs_for(peer_id, {d.issuer for d in newer})
        for d in newer:
            known[d.key] = d.seq
            out.append(self._send(peer_id, {"t": "doc", "d": encode_doc(d)}))
        return out

    def _revoke(self, subject: str) -> list[Action]:
        current = self.docs.get(f"crl:{self.identity.cert.cert_id}")
        subjects = set(revoked_subjects(current)) if current else set()
        return self._publish("crl", sorted(subjects | {subject}))

    def _publish(self, kind: str, body: object) -> list[Action]:
        if self.config.role is not Role.HUB:
            raise ValueError("only a hub can publish revocations or directories")
        current = self.docs.get(f"{kind}:{self.identity.cert.cert_id}")
        return self._on_doc(sign_doc(self.identity, kind, current.seq + 1 if current else 1, body))

    def _on_doc(self, doc: HubDoc) -> list[Action]:
        current = self.docs.get(doc.key)
        if current is not None and current.seq >= doc.seq:
            return []
        issuer = self.certs.get(doc.issuer)
        root = self.identity.root_pubkey
        if not doc_authentic(doc, issuer, root, int(self.clock_s), self.certs, self.revoked):
            self.dropped["bad_doc"] += 1
            return []
        self.docs[doc.key] = doc
        actions: list[Action] = [Store(table=T_DOCS, key=doc.key.encode(), value=encode_doc(doc))]
        if doc.kind == "crl":
            self.revoked = self._revoked()
            self._verified_headers.clear()  # re-check every injector against the new list
            actions += self._lora({"t": "doc", "d": encode_doc(doc)})
        elif self.config.sa is not None and issuer is not None:
            actions.append(DirectoryUpdate(doc=encode_doc(doc), issuer=encode_cert(issuer)))
        for peer in sorted(self.peer_has):
            actions += self._offer(peer)
        return actions

    def _revoked(self) -> frozenset[str]:
        crls = [d for d in self.docs.values() if d.kind == "crl"]
        return frozenset(s for d in crls for s in revoked_subjects(d))

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

    def _accept(self, shard: Shard, source: str | None, age_ms: int = 0) -> list[Action]:
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
        if self._expired(shard, age_ms):
            self.dropped["expired"] += 1
            return []
        if not self._shards_of(bid) and not self._within_rate(shard.header.injector):
            self.dropped["rate_limited"] += 1
            return []
        evictions = self._make_room(shard, len(shard.data))
        if evictions is None:
            self.dropped["full"] += 1  # refusing is safe: no receipt, so the sender keeps it
            return []
        actions = evictions + self._store_shard(shard, age_ms)
        if source is not None:
            if source not in self._unreceipted:
                at = self._mono + RECEIPT_BATCH_S
                actions.append(SetTimer(name=f"receipt:{source}", at_mono=at))
            self._unreceipted[source].append(sid)
        return actions + self._after_new_shards(bid)

    # --- custody (§8.5) -----------------------------------------------------------

    def _issue_receipt(self, sender: str) -> list[Action]:
        """Sign one receipt for everything stored from `sender` since the last one."""
        ids = self._unreceipted.pop(sender, [])
        if not ids:
            return []
        r = Receipt(
            self.identity.cert.cert_id, sender, tuple(ids), self.hlc.value, self.ledger_head
        )
        r = replace(r, sig=self.identity.sign(r.signing_bytes()))
        encoded = encode_receipt(r)
        self.ledger_seq += 1
        self.ledger_head = hashlib.sha256(encoded).digest()
        actions: list[Action] = [
            Store(table=T_LEDGER, key=self.ledger_seq.to_bytes(8), value=encoded)
        ]
        if sender in self.peer_bloom:
            return actions + [self._send(sender, {"t": "receipt", "r": encoded})]
        self.outbox[self.ledger_head] = r
        actions.append(Store(table=T_OUTBOX, key=self.ledger_head, value=encoded))
        return actions + self._lora({"t": "receipt", "r": encoded})

    def _pay_receipts_owed(self, peer_id: str) -> list[Action]:
        """Hand over receipts kept since an earlier contact dropped. If one is lost again the
        sender merely keeps its copy, so each is sent once."""
        actions: list[Action] = []
        for key, r in sorted(self.outbox.items()):
            if r.sender == peer_id:
                del self.outbox[key]
                actions.append(self._send(peer_id, {"t": "receipt", "r": encode_receipt(r)}))
                actions.append(Delete(table=T_OUTBOX, key=key))
        return actions

    def _on_receipt(self, r: Receipt) -> list[Action]:
        if r.sender != self.config.node_id:
            return []
        cert = self.certs.get(r.receiver)
        if not (
            cert is not None
            and self._valid(cert)
            and verify_p256(cert.pubkey, r.signing_bytes(), r.sig)
        ):
            self.dropped["bad_receipt"] += 1
            return []
        encoded = encode_receipt(r)
        self.receipts_in += 1
        actions: list[Action] = [
            Store(table=T_RECEIPTS_IN, key=hashlib.sha256(encoded).digest(), value=encoded)
        ]
        for sid in r.shard_ids:
            shard = self.shards.get(sid)
            if shard is not None and self._may_drop_after_custody(shard, cert):
                actions.append(self._remove_shard(shard))
        return actions

    def _may_drop_after_custody(self, shard: Shard, custodian: Cert) -> bool:
        """REQ-PROTO-05: origins and destination sites keep copies until the delivery ACK;
        a relay lets go once a better-placed node holds the shard."""
        h = shard.header
        if h.bundle_id in self.origin or self.config.sa == h.dst_sa:
            return False
        table = self.peer_table.get(custodian.subject, {})
        return self.router.better(h.dst_sa, custodian.sa, table)

    def _make_room(self, incoming: Shard, size: int) -> list[Action] | None:
        """Evict less important shards to fit `size` bytes, or return None and evict nothing.

        Only strictly lower classes go (TELEMETRY first, oldest first), so CRITICAL is never
        evicted for a lower class, and the origin's own copies are never evicted (§6.3, §8.5).
        """
        free = self.config.capacity_bytes - self.used_bytes
        if size <= free:
            return []
        expired = sorted((s for s in self.shards.values() if self._expired(s)), key=_order)
        lower = sorted(
            (
                s
                for s in self.shards.values()
                if s.header.priority > incoming.header.priority
                and s.header.bundle_id not in self.origin
                and not self._expired(s)
            ),
            key=lambda s: (-s.header.priority, s.header.created, s.shard_id),
        )
        candidates = expired + lower
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

    def _within_rate(self, injector: str) -> bool:
        """Admit a new bundle unless this injector exceeded its hourly budget (REQ-SEC-12)."""
        times = self._injected_at[injector]
        while times and times[0] <= self.clock_s - 3600:
            times.popleft()
        if len(times) >= self.config.max_new_bundles_per_injector_per_hour:
            return False
        times.append(self.clock_s)
        return True

    def _store_shard(self, shard: Shard, age_ms: int = 0) -> list[Action]:
        self.shards[shard.shard_id] = shard
        self._age_at[shard.shard_id] = (age_ms, self._mono)
        self.used_bytes += len(shard.data)
        value = cbor2.dumps([encode_shard(shard), age_ms])
        return [Store(table=T_SHARDS, key=shard.shard_id, value=value)]

    def _age_ms(self, shard: Shard) -> int:
        age, since = self._age_at[shard.shard_id]
        return age + int((self._mono - since) * 1000)

    def _expired(self, shard: Shard, age_ms: int | None = None) -> bool:
        """The stricter of Bundle Age and HLC time since creation (REQ-TIME-02)."""
        h = shard.header
        age = self._age_ms(shard) if age_ms is None else age_ms
        lifetime_ms = h.lifetime_s * 1000
        return age > lifetime_ms or self.hlc.wall_ms - h.created > lifetime_ms

    def _remove_shard(self, shard: Shard) -> Delete:
        del self.shards[shard.shard_id]
        del self._age_at[shard.shard_id]
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
        actions += self._lora({"t": "ack", "a": encode_ack(ack)})
        for peer in sorted(self.peer_has):
            actions += self._offer(peer)
        return actions

    def _send(self, peer_id: str, msg: Mapping[str, object]) -> SendFrame:
        return SendFrame(peer_id=peer_id, radio=RadioKind.WIFI, frame=cbor2.dumps(msg))


def _order(s: Shard) -> tuple[int, int, bytes]:
    return (s.header.priority, s.header.created, s.shard_id)


def _held(sid: bytes, has: set[bytes], bloom: Bloom) -> bool:
    return sid in has or b"s" + sid in bloom


def _summary_signing_bytes(msg: Mapping[str, object]) -> bytes:
    body = cbor2.dumps([msg["bloom"], msg["certs"], msg["docs"], msg["prophet"]])
    return _SUMMARY_CONTEXT + cbor2.dumps([msg["from"], msg["to"], msg["hlc"], body])


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
