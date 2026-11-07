"""Secure element: holds the node's P-256 signing key and storage-key slots (spec §7.2, §7.7)."""

from typing import Protocol

P256_SIGNATURE_LEN = 64  # raw r||s
P256_PUBLIC_KEY_LEN = 65  # uncompressed SEC1 point


class SecureElement(Protocol):
    """No method exports the private key (REQ-SEC-05)."""

    def public_key(self) -> bytes:
        """Uncompressed SEC1 P-256 public key (65 bytes)."""
        ...

    def sign(self, message: bytes) -> bytes:
        """ECDSA P-256 / SHA-256 signature over `message`, raw r||s (64 bytes)."""
        ...

    def read_slot(self, slot: int) -> bytes:
        """Read a protected data slot (e.g. the storage encryption key)."""
        ...
