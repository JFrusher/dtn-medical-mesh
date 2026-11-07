"""Bundle wire format (spec §6). CBOR, versioned; inspired by but not compatible with BPv7."""

import hashlib
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import TypeVar

import cbor2

FORMAT_VERSION = 1
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
    bundle_id: bytes
    src_sa: str
    dst_sa: str
    priority: Priority
    created: int  # HLC (spec §8.9)
    lifetime_s: int


@dataclass(frozen=True)
class Bundle:
    header: Header
    payload: bytes  # endpoint ciphertext; nodes never decrypt it


def make_bundle_id(payload: bytes, created: int) -> bytes:
    return hashlib.sha256(payload + created.to_bytes(8)).digest()


def encode_bundle(bundle: Bundle) -> bytes:
    return cbor2.dumps({"v": FORMAT_VERSION, "h": asdict(bundle.header), "p": bundle.payload})


def decode_bundle(data: bytes) -> Bundle:
    try:
        obj = cbor2.loads(data)
        if obj["v"] != FORMAT_VERSION:
            raise FormatError(f"unsupported format version {obj['v']}")
        h = obj["h"]
        header = Header(
            bundle_id=_typed(h["bundle_id"], bytes),
            src_sa=_typed(h["src_sa"], str),
            dst_sa=_typed(h["dst_sa"], str),
            priority=Priority(h["priority"]),
            created=_typed(h["created"], int),
            lifetime_s=_typed(h["lifetime_s"], int),
        )
        return Bundle(header, _typed(obj["p"], bytes))
    except (KeyError, TypeError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


def _typed(value: object, kind: type[T]) -> T:
    if not isinstance(value, kind):
        raise FormatError(f"expected {kind.__name__}, got {type(value).__name__}")
    return value
