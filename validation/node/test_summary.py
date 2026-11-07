import hashlib

import pytest

from node.bundle import FormatError
from node.summary import Bloom


@pytest.mark.req("REQ-PROTO-02")
def test_no_false_negatives_and_about_one_percent_false_positives() -> None:
    held = [hashlib.sha256(i.to_bytes(4)).digest() for i in range(2000)]
    bloom = Bloom.decode(Bloom.of(held).encode())
    assert all(h in bloom for h in held)
    others = [hashlib.sha256(b"x" + i.to_bytes(4)).digest() for i in range(20000)]
    rate = sum(o in bloom for o in others) / len(others)
    assert rate < 0.02
    assert len(Bloom.of(held).encode()) < 2600  # ~1.2 bytes per item, vs 33 for exact IDs


def test_empty_and_hostile_filters() -> None:
    assert b"x" not in Bloom.of([])
    with pytest.raises(FormatError):
        Bloom.decode(Bloom(64, 3).encode()[:-1])
    with pytest.raises(FormatError):
        Bloom(10**9, 3)
