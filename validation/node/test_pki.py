from dataclasses import replace

import pytest

from node.pki import ROOT, Cert, Role, decode_cert, encode_cert, issue, valid
from simulation.virtual_hw import SeededEntropy, SoftwareSecureElement

NOW = 1_800_000_000
root = SoftwareSecureElement(SeededEntropy("root"))
hub_se = SoftwareSecureElement(SeededEntropy("hub"))
site_se = SoftwareSecureElement(SeededEntropy("site"))
HUB = issue(root.sign, ROOT, "HUB", Role.HUB, "SA_H", hub_se.public_key(), NOW)
SITE = issue(hub_se.sign, HUB.cert_id, "CLINIC", Role.SITE, "SA_C", site_se.public_key(), NOW)
KNOWN = {HUB.cert_id: HUB, SITE.cert_id: SITE}
RK = root.public_key()


@pytest.mark.req("REQ-SEC-04")
def test_chain_validates_and_round_trips() -> None:
    assert valid(HUB, KNOWN, RK, NOW) and valid(SITE, KNOWN, RK, NOW + 10)
    assert decode_cert(encode_cert(SITE)) == SITE


@pytest.mark.req("REQ-SEC-04", "REQ-SEC-07", "REQ-SEC-08")
@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "early",
        "revoked",
        "unknown issuer",
        "tampered",
        "non-hub root cert",
        "deep chain",
    ],
)
def test_invalid_certificates(case: str) -> None:
    cert, known, now, revoked = SITE, dict(KNOWN), NOW + 10, frozenset[str]()
    if case == "expired":
        now = SITE.not_after
    elif case == "early":
        now = SITE.not_before - 1
    elif case == "revoked":
        revoked = frozenset({"CLINIC"})
    elif case == "unknown issuer":
        del known[HUB.cert_id]
    elif case == "tampered":
        cert = replace(SITE, sa="SA_H")
    elif case == "non-hub root cert":
        cert = issue(root.sign, ROOT, "X", Role.SITE, "SA_C", site_se.public_key(), NOW)
    elif case == "deep chain":
        cert = issue(site_se.sign, SITE.cert_id, "Y", Role.MULE, None, hub_se.public_key(), NOW)
    assert not valid(cert, known, RK, now, revoked)


def test_revoking_hub_invalidates_what_it_issued() -> None:
    assert not valid(SITE, KNOWN, RK, NOW, frozenset({"HUB"}))


def test_decode_rejects_junk() -> None:
    from node.bundle import FormatError

    with pytest.raises(FormatError):
        decode_cert(b"\xa1\x61x\x01")
    assert isinstance(decode_cert(encode_cert(HUB)), Cert)
