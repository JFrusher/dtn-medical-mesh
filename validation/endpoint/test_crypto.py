import cbor2
import pytest

from endpoint.crypto import CryptoError, EndpointKeys, _decrypt, _encrypt, open_sealed, seal
from simulation.virtual_hw import SeededEntropy

E = SeededEntropy(1)
CLINIC, HOSPITAL, OTHER = (EndpointKeys.generate(E) for _ in range(3))


@pytest.mark.req("REQ-SEC-01", "REQ-SEC-03")
def test_round_trip_reveals_author() -> None:
    blob = seal(b"referral: chest pain", HOSPITAL.encryption_public, CLINIC, E)
    opened = open_sealed(blob, HOSPITAL)
    assert opened.content == b"referral: chest pain"
    assert opened.signer == CLINIC.signing_public
    assert b"chest pain" not in blob


@pytest.mark.req("REQ-SEC-01")
def test_wrong_recipient_cannot_open() -> None:
    blob = seal(b"x", HOSPITAL.encryption_public, CLINIC, E)
    with pytest.raises(CryptoError):
        open_sealed(blob, OTHER)


@pytest.mark.req("REQ-SEC-01")
@pytest.mark.parametrize("pos", [0, 5, 40, 60, -1])
def test_any_bit_flip_is_rejected(pos: int) -> None:
    blob = bytearray(seal(b"vitals", HOSPITAL.encryption_public, CLINIC, E))
    blob[pos] ^= 1
    with pytest.raises(CryptoError):
        open_sealed(bytes(blob), HOSPITAL)


@pytest.mark.req("REQ-SEC-03")
def test_resealed_content_for_other_recipient_is_rejected() -> None:
    """A recipient can't re-encrypt signed content and pass it off as sent to someone else."""
    inner = _decrypt(seal(b"x", HOSPITAL.encryption_public, CLINIC, E), HOSPITAL)
    forged = _encrypt(inner, OTHER.encryption_public, E)
    with pytest.raises(CryptoError):
        open_sealed(forged, OTHER)
    assert cbor2.loads(inner)[0] == b"x"  # the forgery really carried the original content
