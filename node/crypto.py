"""Node-side signature verification (P-256 raw r||s). Signing happens in the secure element."""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def verify_p256(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
        der = encode_dss_signature(int.from_bytes(signature[:32]), int.from_bytes(signature[32:]))
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return False
    return True
