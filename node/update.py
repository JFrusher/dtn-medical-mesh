"""Signed software updates (spec §9.5).

The offline release key signs a manifest: version, the roles it may go to, the oldest version
it upgrades, and the payload's hash and size. A hub spreads it in an "update" hub document whose
body also names the stage it is rolled out to now (a subset of the signed roles), so a hub can
pace a rollout but can never push software the release key did not sign.
"""

import hashlib
from dataclasses import dataclass, replace

import cbor2

from node.bundle import FormatError
from node.crypto import verify_p256
from node.pki import Signer

_CONTEXT = b"dtn-medical-mesh/v1/release"


def version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError as e:
        raise FormatError(f"bad version {v!r}") from e


@dataclass(frozen=True)
class Manifest:
    version: str
    roles: tuple[str, ...]
    min_version: str
    payload_hash: bytes  # SHA-256
    size: int
    sig: bytes = b""

    def signing_bytes(self) -> bytes:
        fields = [self.version, list(self.roles), self.min_version, self.payload_hash, self.size]
        return _CONTEXT + cbor2.dumps(fields)

    def authentic(self, release_pubkey: bytes) -> bool:
        return verify_p256(release_pubkey, self.signing_bytes(), self.sig)

    def matches(self, payload: bytes) -> bool:
        return len(payload) == self.size and hashlib.sha256(payload).digest() == self.payload_hash


def sign_manifest(m: Manifest, release_sign: Signer) -> Manifest:
    return replace(m, sig=release_sign(m.signing_bytes()))


def stage_body(m: Manifest, stage: tuple[str, ...]) -> list[object]:
    """The body of an "update" hub document."""
    if not set(stage) <= set(m.roles):
        raise ValueError("a stage can only name roles the release key signed for")
    return [encode_manifest(m), list(stage)]


def parse_stage(body: bytes) -> tuple[Manifest, tuple[str, ...]]:
    try:
        encoded, stage = cbor2.loads(body)
        m = decode_manifest(encoded)
        if not isinstance(stage, list) or not set(stage) <= set(m.roles):
            raise FormatError("update stage must be a subset of the signed roles")
        return m, tuple(str(s) for s in stage)
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


def encode_manifest(m: Manifest) -> bytes:
    return cbor2.dumps([m.version, list(m.roles), m.min_version, m.payload_hash, m.size, m.sig])


def decode_manifest(data: bytes) -> Manifest:
    try:
        version, roles, min_version, payload_hash, size, sig = cbor2.loads(data)
        m = Manifest(
            str(version), tuple(str(r) for r in roles), str(min_version), payload_hash, size, sig
        )
        version_key(m.version), version_key(m.min_version)
        if not (
            isinstance(payload_hash, bytes) and isinstance(size, int) and isinstance(sig, bytes)
        ):
            raise FormatError("bad manifest fields")
        return m
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e
