"""Endpoint keys at rest on the shared clinic laptop (spec §10.2).

The clinic's keys are stored once per staff member, each copy sealed (XSalsa20-Poly1305) under
a key derived with Argon2id from that person's PIN and the laptop's device secret. The device
secret lives in the operating system's key store or TPM, not beside the sealed copies, so a
copied disk alone cannot be brute-forced over the small PIN space; on the laptop itself, five
wrong PINs lock sign-in for a minute, doubling with each further failure.
"""

import re
from dataclasses import dataclass, field

import nacl.exceptions
import nacl.pwhash
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from nacl.secret import SecretBox

from endpoint.crypto import EndpointKeys
from node.hw.entropy import Entropy

PIN = re.compile(r"\d{4,8}")
MAX_TRIES = 5
LOCK_S = 60.0
_SALT = nacl.pwhash.argon2id.SALTBYTES


class SignInError(Exception):
    """Unknown staff member or wrong PIN."""


class LockedOut(Exception):
    def __init__(self, until: float) -> None:
        super().__init__(f"sign-in locked until {until:.0f}")
        self.until = until


@dataclass
class KeyStore:
    device_secret: bytes
    sealed: dict[str, bytes] = field(default_factory=dict)  # staff name -> salt || sealed keys
    failures: int = 0
    locked_until: float = 0.0

    def __post_init__(self) -> None:
        if len(self.device_secret) != 32:
            raise ValueError("device secret must be 32 bytes")

    def enrol(self, staff: str, pin: str, keys: EndpointKeys, entropy: Entropy) -> None:
        if not PIN.fullmatch(pin):
            raise ValueError("a PIN is 4 to 8 digits")
        salt = entropy.random_bytes(_SALT)
        secret = _raw(keys.encryption) + _raw(keys.signing)
        box = SecretBox(self._kek(pin, salt))
        self.sealed[staff] = salt + bytes(box.encrypt(secret, entropy.random_bytes(24)))

    def unlock(self, staff: str, pin: str, now: float) -> EndpointKeys:
        if now < self.locked_until:
            raise LockedOut(self.locked_until)
        blob = self.sealed.get(staff)
        try:
            if blob is None or not PIN.fullmatch(pin):
                raise nacl.exceptions.CryptoError("no such staff member or bad PIN")
            secret = SecretBox(self._kek(pin, blob[:_SALT])).decrypt(blob[_SALT:])
        except nacl.exceptions.CryptoError as e:
            self.failures += 1
            if self.failures >= MAX_TRIES:
                self.locked_until = now + LOCK_S * 2 ** (self.failures - MAX_TRIES)
            raise SignInError("wrong PIN") from e
        self.failures = 0
        return EndpointKeys(
            X25519PrivateKey.from_private_bytes(secret[:32]),
            Ed25519PrivateKey.from_private_bytes(secret[32:]),
        )

    def _kek(self, pin: str, salt: bytes) -> bytes:
        return bytes(
            nacl.pwhash.argon2id.kdf(
                SecretBox.KEY_SIZE,
                self.device_secret + pin.encode(),
                salt,
                opslimit=nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE,
                memlimit=nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE,
            )
        )


def _raw(key: X25519PrivateKey | Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
