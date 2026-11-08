import pytest

from endpoint.crypto import EndpointKeys
from endpoint.keystore import LOCK_S, MAX_TRIES, KeyStore, LockedOut, SignInError
from simulation.virtual_hw import SeededEntropy


def store() -> tuple[KeyStore, EndpointKeys]:
    e = SeededEntropy("k")
    keys = EndpointKeys.generate(e)
    ks = KeyStore(e.random_bytes(32))
    ks.enrol("Nurse A", "2580", keys, e)
    ks.enrol("Officer B", "917364", keys, e)
    return ks, keys


@pytest.mark.req("REQ-SEC-14")
def test_each_staff_pin_unlocks_the_same_clinic_keys() -> None:
    ks, keys = store()
    for staff, pin in (("Nurse A", "2580"), ("Officer B", "917364")):
        got = ks.unlock(staff, pin, now=0)
        assert got.encryption_public == keys.encryption_public
        assert got.signing_public == keys.signing_public
    assert keys.encryption_public not in b"".join(ks.sealed.values())  # nothing in the clear


@pytest.mark.req("REQ-SEC-14")
def test_wrong_pin_unknown_staff_and_another_device_all_fail() -> None:
    ks, _ = store()
    with pytest.raises(SignInError):
        ks.unlock("Nurse A", "0000", now=0)
    with pytest.raises(SignInError):
        ks.unlock("Nobody", "2580", now=0)
    copied = KeyStore(bytes(32), dict(ks.sealed))  # the disk without the device secret
    with pytest.raises(SignInError):
        copied.unlock("Nurse A", "2580", now=0)


@pytest.mark.req("REQ-SEC-14")
def test_five_wrong_pins_lock_sign_in_and_the_lock_doubles() -> None:
    ks, _ = store()
    for _ in range(MAX_TRIES):
        with pytest.raises(SignInError):
            ks.unlock("Nurse A", "1111", now=10)
    with pytest.raises(LockedOut) as locked:
        ks.unlock("Nurse A", "2580", now=11)  # even the right PIN waits
    assert locked.value.until == 10 + LOCK_S
    with pytest.raises(SignInError):
        ks.unlock("Nurse A", "1111", now=10 + LOCK_S)
    assert ks.locked_until == 10 + LOCK_S + 2 * LOCK_S
    ks.unlock("Nurse A", "2580", now=10 + 4 * LOCK_S)
    assert ks.failures == 0


def test_pin_policy() -> None:
    e = SeededEntropy("p")
    ks = KeyStore(e.random_bytes(32))
    for bad in ("123", "abcd", "123456789"):
        with pytest.raises(ValueError):
            ks.enrol("X", bad, EndpointKeys.generate(e), e)
