"""The hub-signed endpoint directory, as an endpoint uses it (spec §7.6)."""

from dataclasses import dataclass

import cbor2

from node.bundle import FormatError
from node.pki import decode_cert, decode_doc, doc_authentic


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    sa: str
    encryption_public: bytes  # X25519
    signing_public: bytes  # Ed25519


def directory_body(entries: list[DirectoryEntry]) -> list[list[object]]:
    return [[e.name, e.sa, e.encryption_public, e.signing_public] for e in entries]


def verified_entries(
    doc: bytes, issuer: bytes, root_pubkey: bytes, now: int
) -> list[DirectoryEntry]:
    """Entries of a directory, after checking the hub's signature chain. Raises if it fails."""
    d, cert = decode_doc(doc), decode_cert(issuer)
    if d.kind != "dir" or not doc_authentic(
        d, cert, root_pubkey, now, {cert.cert_id: cert}, frozenset()
    ):
        raise FormatError("directory is not signed by a valid hub")
    rows = cbor2.loads(d.body)
    try:
        return [DirectoryEntry(str(n), str(sa), bytes(enc), bytes(sig)) for n, sa, enc, sig in rows]
    except (TypeError, ValueError) as e:
        raise FormatError(f"bad directory entry: {e}") from e
