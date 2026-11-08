import pytest

from endpoint.padding import BUCKETS, PayloadTooLarge, bucket_for, pad, unpad


@pytest.mark.req("REQ-SEC-10")
@pytest.mark.parametrize("n", [0, 1, 4092, 4093, 70_000, 2_000_000])
def test_pads_to_bucket_and_round_trips(n: int) -> None:
    data = bytes(range(256)) * (n // 256) + bytes(n % 256)
    padded = pad(data)
    assert len(padded) in BUCKETS
    assert unpad(padded) == data


@pytest.mark.req("REQ-SEC-10")
def test_same_bucket_same_size() -> None:
    assert len(pad(b"a")) == len(pad(b"a" * 4000)) == 4096


def test_over_limit_rejected_and_bad_input() -> None:
    with pytest.raises(PayloadTooLarge):
        bucket_for(BUCKETS[-1])
    with pytest.raises(ValueError):
        unpad(b"\xff\xff\xff\xff" + bytes(4092))
    with pytest.raises(ValueError):
        unpad(b"\x00" * 100)
