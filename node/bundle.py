"""Bundle wire format (spec §6). CBOR, versioned; inspired by but not compatible with BPv7."""

import hashlib
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import TypeVar

import cbor2

FORMAT_VERSION = 2
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
    except (KeyError, TypeError, cbor2.CBORDecodeError) as e:
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
    if (
        header.k < 1
        or header.m < 0
        or header.bundle_id != make_bundle_id(header.merkle_root, header.created)
    ):
        raise FormatError("inconsistent header")
    return header


_HEADER_FIELDS = set(Header.__dataclass_fields__)


def _typed(value: object, kind: type[T]) -> T:
    if not isinstance(value, kind):
        raise FormatError(f"expected {kind.__name__}, got {type(value).__name__}")
    return value
