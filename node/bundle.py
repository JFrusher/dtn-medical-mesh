"""Bundle wire format (spec §6). CBOR, versioned; inspired by but not compatible with BPv7."""

import hashlib
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import TypeVar

import cbor2

FORMAT_VERSION = 2
MAX_SHARDS = 256  # shard index is one byte in a shard ID
MAX_BUCKET = 32 * 1024 * 1024
T = TypeVar("T")


class FormatError(ValueError):
    """Bytes from the network do not decode to a valid structure."""


class Priority(IntEnum):
    CRITICAL = 0
    URGENT = 1
    ROUTINE = 2
    TELEMETRY = 3


@dataclass(frozen=True)
class Header:
    """Routing metadata readable by relays (spec §6.1). Names no patient or endpoint."""

    bundle_id: bytes
    src_sa: str
    dst_sa: str
    priority: Priority
    created: int  # HLC (spec §8.9)
    lifetime_s: int
    size_bucket: int  # padded ciphertext length
    k: int
    m: int
    merkle_root: bytes
    injector: str  # cert ID of the site node that signed this header
    sig: bytes  # injector's P-256 signature over every other field

    def signing_bytes(self) -> bytes:
        fields = asdict(self)
        del fields["sig"]
        return b"dtn-medical-mesh/v1/header" + cbor2.dumps(fields)


@dataclass(frozen=True)
class Bundle:
    header: Header
    payload: bytes  # endpoint ciphertext; nodes never decrypt it


def make_bundle_id(merkle_root: bytes, created: int) -> bytes:
    return hashlib.sha256(merkle_root + created.to_bytes(8)).digest()


def encode_bundle(bundle: Bundle) -> bytes:
    return cbor2.dumps({"v": FORMAT_VERSION, "h": asdict(bundle.header), "p": bundle.payload})


def decode_bundle(data: bytes) -> Bundle:
    try:
        obj = cbor2.loads(data)
        if obj["v"] != FORMAT_VERSION:
            raise FormatError(f"unsupported format version {obj['v']}")
        return Bundle(decode_header(obj["h"]), _typed(obj["p"], bytes))
    except (KeyError, TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


def decode_header(h: dict[str, object]) -> Header:
    if not isinstance(h, dict) or set(h) != _HEADER_FIELDS:
        raise FormatError("header fields do not match the format")
    header = Header(
        bundle_id=_typed(h["bundle_id"], bytes),
        src_sa=_typed(h["src_sa"], str),
        dst_sa=_typed(h["dst_sa"], str),
        priority=Priority(_typed(h["priority"], int)),
        created=_typed(h["created"], int),
        lifetime_s=_typed(h["lifetime_s"], int),
        size_bucket=_typed(h["size_bucket"], int),
        k=_typed(h["k"], int),
        m=_typed(h["m"], int),
        merkle_root=_typed(h["merkle_root"], bytes),
        injector=_typed(h["injector"], str),
        sig=_typed(h["sig"], bytes),
    )
    # Ranges matter as much as types: these fields feed fixed-width encodings.
    in_range = (
        header.k >= 1
        and header.m >= 0
        and header.k + header.m <= MAX_SHARDS
        and 0 <= header.created < 2**63
        and 0 < header.lifetime_s < 2**31
        and 0 < header.size_bucket <= MAX_BUCKET
        and len(header.merkle_root) == 32
    )
    if not in_range or header.bundle_id != make_bundle_id(header.merkle_root, header.created):
        raise FormatError("header field out of range or inconsistent")
    return header


_HEADER_FIELDS = set(Header.__dataclass_fields__)


def _typed(value: object, kind: type[T]) -> T:
    if not isinstance(value, kind):
        raise FormatError(f"expected {kind.__name__}, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class Shard:
    """One erasure-coded piece of a bundle's ciphertext; the unit that crosses the radio (§6.2)."""

    header: Header
    index: int
    data: bytes
    proof: tuple[bytes, ...]

    @property
    def shard_id(self) -> bytes:
        return shard_id(self.header.bundle_id, self.index)


def shard_id(bundle_id: bytes, index: int) -> bytes:
    return bundle_id + index.to_bytes(1)


def encode_shard(shard: Shard) -> bytes:
    return cbor2.dumps(
        {
            "v": FORMAT_VERSION,
            "h": asdict(shard.header),
            "i": shard.index,
            "d": shard.data,
            "p": list(shard.proof),
        }
    )


def decode_shard(data: bytes) -> Shard:
    try:
        obj = cbor2.loads(data)
        if obj["v"] != FORMAT_VERSION:
            raise FormatError(f"unsupported format version {obj['v']}")
        header = decode_header(obj["h"])
        index = _typed(obj["i"], int)
        if not 0 <= index < header.k + header.m:
            raise FormatError("shard index out of range")
        proof = tuple(_typed(p, bytes) for p in _typed(obj["p"], list))
        return Shard(header, index, _typed(obj["d"], bytes), proof)
    except (KeyError, TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


@dataclass(frozen=True)
class Ack:
    """Delivery ACK, signed by a site or hub in the bundle's destination SA (spec §8.7)."""

    bundle_id: bytes
    dst_sa: str
    signer: str  # cert ID
    sig: bytes = b""

    def signing_bytes(self) -> bytes:
        return b"dtn-medical-mesh/v1/ack" + cbor2.dumps([self.bundle_id, self.dst_sa, self.signer])


def encode_ack(ack: Ack) -> bytes:
    return cbor2.dumps(asdict(ack))


def decode_ack(data: bytes) -> Ack:
    try:
        f = cbor2.loads(data)
        if set(f) != set(Ack.__dataclass_fields__):
            raise FormatError("ACK fields do not match")
        return Ack(
            _typed(f["bundle_id"], bytes),
            _typed(f["dst_sa"], str),
            _typed(f["signer"], str),
            _typed(f["sig"], bytes),
        )
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


@dataclass(frozen=True)
class Receipt:
    """Custody receipt: the receiver durably holds these shards (spec §8.5).

    Receipts a node issues form a hash chain (`prev`), so its ledger is tamper-evident.
    """

    receiver: str  # cert ID
    sender: str  # node ID the shards came from
    shard_ids: tuple[bytes, ...]
    hlc: int
    prev: bytes  # SHA-256 of the receiver's previous receipt, or 32 zero bytes
    sig: bytes = b""

    def signing_bytes(self) -> bytes:
        body = [self.receiver, self.sender, list(self.shard_ids), self.hlc, self.prev]
        return b"dtn-medical-mesh/v1/receipt" + cbor2.dumps(body)


def encode_receipt(r: Receipt) -> bytes:
    return cbor2.dumps([r.receiver, r.sender, list(r.shard_ids), r.hlc, r.prev, r.sig])


def decode_receipt(data: bytes) -> Receipt:
    try:
        receiver, sender, ids, hlc, prev, sig = cbor2.loads(data)
        r = Receipt(receiver, sender, tuple(ids), hlc, prev, sig)
        types = [(receiver, str), (sender, str), (hlc, int), (prev, bytes), (sig, bytes)]
        if not all(isinstance(v, t) for v, t in types) or not all(
            isinstance(i, bytes) for i in r.shard_ids
        ):
            raise FormatError("bad receipt field types")
        return r
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e
