import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from node.hw.rtc import Rtc, RtcError
from node.hw.secure_element import SecureElement
from simulation.scheduler import Scheduler
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy, SoftwareSecureElement, VirtualRtc


@pytest.mark.req("REQ-SEC-05", "REQ-SIM-02")
def test_secure_element_signs_verifiably_and_deterministically() -> None:
    se: SecureElement = SoftwareSecureElement(SeededEntropy("n1"))
    sig = se.sign(b"msg")
    assert len(sig) == 64 and len(se.public_key()) == 65
    assert sig == SoftwareSecureElement(SeededEntropy("n1")).sign(b"msg")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), se.public_key())
    der = encode_dss_signature(int.from_bytes(sig[:32]), int.from_bytes(sig[32:]))
    pub.verify(der, b"msg", ec.ECDSA(hashes.SHA256()))
    public = [a for a in dir(se) if not a.startswith("_")]
    assert sorted(public) == ["public_key", "read_slot", "sign"]


def test_rtc_follows_scheduler_and_can_fail() -> None:
    s = Scheduler(seed=1)
    rtc: Rtc = VirtualRtc(s)
    s.run_until(10)
    assert rtc.read() == SIM_EPOCH + 10
    rtc.failed = True  # type: ignore[attr-defined]
    with pytest.raises(RtcError):
        rtc.read()
