"""Node-side signature verification: P-256 raw r||s between nodes (signing happens in the secure
element), and Ed25519 for the laptops' signed injections (spec §9.6)."""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

verifies = 0  # running count; the emulator charges CPU time per verify (spec §12.2)


def verify_p256(public_key: bytes, message: bytes, signature: bytes) -> bool:
    global verifies
    verifies += 1
    if len(signature) != 64:
        return False
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
        der = encode_dss_signature(int.from_bytes(signature[:32]), int.from_bytes(signature[32:]))
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return False
    return True


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True
