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
HUB_CERT_LIFETIME_S = 365 * 24 * 3600  # hubs are renewed by the offline root, by hand
RENEW_AFTER_S = 24 * 3600  # a docked node's cert is reissued once it is a day old
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


DOC_KINDS = ("crl", "dir")  # revocation list, endpoint directory


@dataclass(frozen=True)
class HubDoc:
    """A hub-signed document that spreads over the mesh; a higher seq replaces a lower one.

    kind "crl": body is the hub's cumulative list of revoked node IDs (§7.6).
    kind "dir": body is the endpoint directory: names, service areas and public keys (§7.6).
    """

    kind: str
    issuer: str  # hub cert ID
    seq: int
    body: bytes  # CBOR, meaning depends on kind
    sig: bytes = b""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.issuer}"

    def signing_bytes(self) -> bytes:
        return b"dtn-medical-mesh/v1/hubdoc" + cbor2.dumps(
            [self.kind, self.issuer, self.seq, self.body]
        )


def sign_doc(identity: "Identity", kind: str, seq: int, body: object) -> HubDoc:
    doc = HubDoc(kind, identity.cert.cert_id, seq, cbor2.dumps(body))
    return replace(doc, sig=identity.sign(doc.signing_bytes()))


def encode_doc(doc: HubDoc) -> bytes:
    return cbor2.dumps([doc.kind, doc.issuer, doc.seq, doc.body, doc.sig])


def decode_doc(data: bytes) -> HubDoc:
    try:
        kind, issuer, seq, body, sig = cbor2.loads(data)
        doc = HubDoc(kind, issuer, seq, body, sig)
        types_ok = isinstance(issuer, str) and isinstance(seq, int)
        if kind not in DOC_KINDS or not types_ok or not isinstance(body, bytes):
            raise FormatError("bad hub document")
        return doc
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e


def doc_authentic(
    doc: HubDoc,
    issuer: Cert | None,
    root_pubkey: bytes,
    now: int,
    known: Mapping[str, Cert],
    revoked: frozenset[str],
) -> bool:
    return (
        issuer is not None
        and issuer.cert_id == doc.issuer
        and issuer.role is Role.HUB
        and valid(issuer, known, root_pubkey, now, revoked)
        and verify_p256(issuer.pubkey, doc.signing_bytes(), doc.sig)
    )


def revoked_subjects(doc: HubDoc) -> list[str]:
    subjects = cbor2.loads(doc.body)
    if not isinstance(subjects, list) or not all(isinstance(x, str) for x in subjects):
        raise FormatError("revocation list body must be a list of node IDs")
    return subjects
