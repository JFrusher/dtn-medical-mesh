import pytest
from hypothesis import given
from hypothesis import strategies as st

from node.merkle import build, verify

leaves = st.lists(st.binary(max_size=64), min_size=1, max_size=17)


@pytest.mark.req("REQ-DUR-01")
@given(leaves)
def test_every_leaf_verifies_alone(data: list[bytes]) -> None:
    root, proofs = build(data)
    for i, d in enumerate(data):
        assert verify(d, i, len(data), proofs[i], root)


@pytest.mark.req("REQ-DUR-01")
@given(leaves, st.data())
def test_tampering_is_detected(data: list[bytes], draw: st.DataObject) -> None:
    root, proofs = build(data)
    i = draw.draw(st.integers(0, len(data) - 1))
    assert not verify(data[i] + b"x", i, len(data), proofs[i], root)
    if proofs[i]:
        assert not verify(data[i], i, len(data), proofs[i][:-1], root)  # truncated proof
    if len(data) > 1:
        j = (i + 1) % len(data)
        assert data[i] == data[j] or not verify(data[i], j, len(data), proofs[j], root)
    assert not verify(data[i], len(data), len(data), proofs[i], root)


def test_empty_tree_rejected() -> None:
    with pytest.raises(ValueError):
        build([])
