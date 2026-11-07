"""Endpoint-only payload crypto (spec §7.3, §7.4). Never imported by node/ (REQ-SEC-02).

Sealed blob: version(1) || ephemeral X25519 pub(32) || nonce(24) || XChaCha20-Poly1305 ct
Plaintext:   CBOR [content, signer Ed25519 pub, sig]; sig covers context || recipient || content,
             so signed content cannot be re-encrypted to another recipient.
"""

from dataclasses import dataclass

import cbor2
import nacl.bindings as sodium
import nacl.exceptions
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from node.hw.entropy import Entropy

SEALED_VERSION = 1
_KDF_INFO = b"dtn-medical-mesh/v1/content-key"
_SIG_CONTEXT = b"dtn-medical-mesh/v1/inner-signature"
_HEADER_LEN = 1 + 32 + 24


class CryptoError(Exception):
    """Decryption or signature verification failed: treat the payload as untrusted."""


@dataclass(frozen=True)
class EndpointKeys:
    encryption: X25519PrivateKey
    signing: Ed25519PrivateKey

    @classmethod
    def generate(cls, entropy: Entropy) -> "EndpointKeys":
        return cls(
            X25519PrivateKey.from_private_bytes(entropy.random_bytes(32)),
            Ed25519PrivateKey.from_private_bytes(entropy.random_bytes(32)),
        )

    @property
    def encryption_public(self) -> bytes:
        return _raw(self.encryption.public_key())

    @property
    def signing_public(self) -> bytes:
        return _raw(self.signing.public_key())


@dataclass(frozen=True)
class Opened:
    content: bytes
    signer: bytes  # Ed25519 public key of the authoring endpoint


def seal(content: bytes, recipient: bytes, author: EndpointKeys, entropy: Entropy) -> bytes:
    sig = author.signing.sign(_SIG_CONTEXT + recipient + content)
    return _encrypt(cbor2.dumps([content, author.signing_public, sig]), recipient, entropy)


def open_sealed(blob: bytes, recipient: EndpointKeys) -> Opened:
    inner = _decrypt(blob, recipient)
    try:
        content, signer, sig = cbor2.loads(inner)
        Ed25519PublicKey.from_public_bytes(signer).verify(
            sig, _SIG_CONTEXT + recipient.encryption_public + content
        )
    except (InvalidSignature, ValueError, TypeError, cbor2.CBORDecodeError) as e:
        raise CryptoError(str(e) or type(e).__name__) from e
    return Opened(content, signer)


def _encrypt(inner: bytes, recipient: bytes, entropy: Entropy) -> bytes:
    ephemeral = X25519PrivateKey.from_private_bytes(entropy.random_bytes(32))
    ephemeral_pub = _raw(ephemeral.public_key())
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    key = _content_key(shared, ephemeral_pub, recipient)
    nonce = entropy.random_bytes(24)
    ct = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(inner, None, nonce, key)
    return bytes([SEALED_VERSION]) + ephemeral_pub + nonce + ct


def _decrypt(blob: bytes, recipient: EndpointKeys) -> bytes:
    if len(blob) < _HEADER_LEN or blob[0] != SEALED_VERSION:
        raise CryptoError("not a sealed payload")
    ephemeral_pub, nonce, ct = blob[1:33], blob[33:57], blob[57:]
    shared = recipient.encryption.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub))
    key = _content_key(shared, ephemeral_pub, recipient.encryption_public)
    try:
        return bytes(sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(ct, None, nonce, key))
    except nacl.exceptions.CryptoError as e:
        raise CryptoError("decryption failed") from e


def _content_key(shared: bytes, ephemeral_pub: bytes, recipient_pub: bytes) -> bytes:
    hkdf = HKDF(hashes.SHA256(), 32, salt=ephemeral_pub + recipient_pub, info=_KDF_INFO)
    return hkdf.derive(shared)


def _raw(key: X25519PublicKey | Ed25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)
