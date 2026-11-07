import pytest

from node.core import _summary_signing_bytes
from node.pki import RENEW_AFTER_S, Role, encode_cert, issue
from node.storage import Storage
from validation.node.helpers import (
    NOW,
    TEST_HUB,
    TEST_HUB_SE,
    frame,
    frames,
    identity,
    make_node,
    node_se,
    summary_from,
)

DAY = 24 * 3600


def dock(node_clock: float) -> list[dict[str, object]]:
    """Mule M (provisioned at NOW) authenticates to hub H at `node_clock`."""
    hub = make_node("H", Role.HUB, "SA_H")
    hub.clock_s = node_clock
    msg = summary_from("M", "H")
    msg["time"] = int(node_clock)
    msg["sig"] = identity("M", Role.MULE, None).sign(_summary_signing_bytes(msg))
    return [f for f in frames(frame(hub, "M", msg)) if f["t"] == "renew"]


@pytest.mark.req("REQ-SEC-08")
def test_hub_renews_a_docked_node_once_a_day_old() -> None:
    assert dock(NOW + 60) == []
    [renewal] = dock(NOW + RENEW_AFTER_S + 60)
    mule = make_node("M", Role.MULE)
    mule.clock_s = NOW + RENEW_AFTER_S + 60
    old = mule.identity.cert
    frame(mule, "H", renewal)
    assert mule.identity.cert.not_after > old.not_after
    assert mule.identity.cert.pubkey == old.pubkey


@pytest.mark.req("REQ-SEC-08")
def test_renewed_cert_survives_reboot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    [renewal] = dock(NOW + 2 * DAY)
    mule = make_node("M", Role.MULE)
    mule.clock_s = NOW + 2 * DAY
    storage = Storage(tmp_path / "m.db", b"k" * 32)
    for action in frame(mule, "H", renewal):
        storage.apply(action)  # type: ignore[arg-type]
    rebooted = make_node("M", Role.MULE, held=storage.load())
    assert rebooted.identity.cert == mule.identity.cert


@pytest.mark.req("REQ-SEC-08")
def test_renewal_for_another_key_or_unknown_issuer_rejected() -> None:
    mule = make_node("M", Role.MULE)
    other_key = issue(
        TEST_HUB_SE.sign, TEST_HUB.cert_id, "M", Role.MULE, None, node_se("X").public_key(), NOW
    )
    assert frame(mule, "H", {"t": "renew", "c": encode_cert(other_key)}) == []
    assert mule.dropped["bad_renewal"] == 1


@pytest.mark.req("REQ-SEC-08")
def test_unrenewed_node_expires_out_of_the_mesh() -> None:
    hub = make_node("H", Role.HUB, "SA_H")
    hub.clock_s = NOW + 15 * DAY
    msg = summary_from("M", "H")
    msg["time"] = int(hub.clock_s)
    assert frame(hub, "M", msg) == []
    assert hub.dropped["unauthenticated_peer"] == 1
