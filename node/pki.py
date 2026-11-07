"""Certificates (spec §7.6). Chain: offline root -> hub certs -> other node certs.

Hubs are the issuing CAs. Certificates are short-lived and checked against revocation.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from enum import Enum

import cbor2

from node.bundle import FormatError
from node.crypto import verify_p256

ROOT = "ROOT"
CERT_LIFETIME_S = 14 * 24 * 3600
_CONTEXT = b"dtn-medical-mesh/v1/cert"

Signer = Callable[[bytes], bytes]


class Role(Enum):
    HUB = "hub"
    SITE = "site"
    MULE = "mule"
    RELAY = "relay"


@dataclass(frozen=True)
class Cert:
    subject: str  # node ID
    role: Role
    sa: str | None
    pubkey: bytes  # uncompressed P-256
    not_before: int  # unix seconds
    not_after: int
    issuer: str  # cert_id of the issuing hub, or ROOT
    sig: bytes = b""

    @property
    def cert_id(self) -> str:
        return hashlib.sha256(encode_cert(self)).hexdigest()[:32]

    def signing_bytes(self) -> bytes:
        fields = asdict(replace(self, sig=b""))
        fields["role"] = self.role.value
        return _CONTEXT + cbor2.dumps(fields)


def issue(
    issuer_sign: Signer,
    issuer_id: str,
    subject: str,
    role: Role,
    sa: str | None,
    pubkey: bytes,
    now: int,
    lifetime_s: int = CERT_LIFETIME_S,
) -> Cert:
    cert = Cert(subject, role, sa, pubkey, now, now + lifetime_s, issuer_id)
    return replace(cert, sig=issuer_sign(cert.signing_bytes()))


def encode_cert(cert: Cert) -> bytes:
    fields = asdict(cert)
    fields["role"] = cert.role.value
    return cbor2.dumps(fields)


def decode_cert(data: bytes) -> Cert:
    try:
        f = cbor2.loads(data)
        if set(f) != set(Cert.__dataclass_fields__):
            raise FormatError("certificate fields do not match")
        cert = Cert(**{**f, "role": Role(f["role"])})
        if not (isinstance(cert.subject, str) and isinstance(cert.pubkey, bytes)):
            raise FormatError("bad certificate field types")
        return cert
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


def valid(
    cert: Cert,
    known: Mapping[str, Cert],
    root_pubkey: bytes,
    now: int,
    revoked: frozenset[str] = frozenset(),
) -> bool:
    """Signature chain to the root, validity window and revocation (by subject)."""
    if not cert.not_before <= now < cert.not_after or cert.subject in revoked:
        return False
    if cert.issuer == ROOT:
        return cert.role is Role.HUB and verify_p256(root_pubkey, cert.signing_bytes(), cert.sig)
    issuer = known.get(cert.issuer)
    if issuer is None or issuer.issuer != ROOT:
        return False
    return valid(issuer, known, root_pubkey, now, revoked) and verify_p256(
        issuer.pubkey, cert.signing_bytes(), cert.sig
    )


@dataclass(frozen=True)
class Identity:
    """What a node is provisioned with: its certificate chain, the root key and its signer."""

    cert: Cert
    issuer: Cert | None  # the issuing hub's cert; None for hubs (issued by the root)
    root_pubkey: bytes
    sign: Signer


@dataclass(frozen=True)
class RevocationList:
    """A hub's cumulative list of revoked node IDs; a higher seq replaces a lower one (§7.6)."""

    issuer: str  # hub cert ID
    seq: int
    subjects: tuple[str, ...]
    sig: bytes = b""

    def signing_bytes(self) -> bytes:
        body = [self.issuer, self.seq, list(self.subjects)]
        return b"dtn-medical-mesh/v1/crl" + cbor2.dumps(body)


def encode_crl(crl: RevocationList) -> bytes:
    return cbor2.dumps([crl.issuer, crl.seq, list(crl.subjects), crl.sig])


def decode_crl(data: bytes) -> RevocationList:
    try:
        issuer, seq, subjects, sig = cbor2.loads(data)
        if not (isinstance(issuer, str) and isinstance(seq, int) and isinstance(sig, bytes)):
            raise FormatError("bad revocation list field types")
        if not all(isinstance(s, str) for s in subjects):
            raise FormatError("revoked subjects must be strings")
        return RevocationList(issuer, seq, tuple(subjects), sig)
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e
