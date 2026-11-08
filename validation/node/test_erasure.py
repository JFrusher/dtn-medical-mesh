from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from node.erasure import decode, encode


@pytest.mark.req("REQ-DUR-02")
@settings(max_examples=25)
@given(st.sampled_from([(1, 0), (4, 2), (4, 4), (4, 8)]), st.binary(min_size=1, max_size=64))
def test_any_k_of_k_plus_m_rebuilds(km: tuple[int, int], chunk: bytes) -> None:
    k, m = km
    data = chunk * k  # length is a multiple of k, as padded buckets always are
    shares = encode(data, k, m)
    assert len(shares) == k + m
    for subset in combinations(range(k + m), k):
        assert decode({i: shares[i] for i in subset}, k, m) == data


def test_too_few_shares_and_bad_length() -> None:
    shares = encode(b"abcd" * 4, 4, 2)
    with pytest.raises(ValueError):
        decode({0: shares[0], 5: shares[5], 2: shares[2]}, 4, 2)
    with pytest.raises(ValueError):
        encode(b"abc", 4, 2)
