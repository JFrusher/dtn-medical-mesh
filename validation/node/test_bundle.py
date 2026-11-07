import cbor2
import pytest

from node.bundle import (
    Bundle,
    FormatError,
    Header,
    Priority,
    decode_bundle,
    encode_bundle,
    make_bundle_id,
)


def sample() -> Bundle:
    payload = b"\x00ciphertext"
    root = b"r" * 32
    h = Header(
        make_bundle_id(root, 7),
        "SA_CLINIC",
        "SA_HOSP",
        Priority.URGENT,
        7,
        3600,
        size_bucket=4096,
        k=4,
        m=4,
        merkle_root=root,
        injector="CLINIC_01",
        sig=b"s" * 64,
    )
    return Bundle(h, payload)


def test_round_trip() -> None:
    b = sample()
    assert decode_bundle(encode_bundle(b)) == b


@pytest.mark.parametrize(
    "bad",
    [b"\xff", cbor2.dumps({"v": 99}), cbor2.dumps({"v": 1, "h": {}, "p": b""}), cbor2.dumps([1])],
)
def test_rejects_malformed(bad: bytes) -> None:
    with pytest.raises(FormatError):
        decode_bundle(bad)


def test_rejects_forged_bundle_id() -> None:
    obj = cbor2.loads(encode_bundle(sample()))
    obj["h"]["bundle_id"] = b"x" * 32
    with pytest.raises(FormatError):
        decode_bundle(cbor2.dumps(obj))


def test_rejects_wrong_field_type() -> None:
    obj = cbor2.loads(encode_bundle(sample()))
    obj["h"]["src_sa"] = 5
    with pytest.raises(FormatError):
        decode_bundle(cbor2.dumps(obj))
