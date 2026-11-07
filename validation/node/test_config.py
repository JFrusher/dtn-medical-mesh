import pytest

from endpoint.padding import BUCKETS
from node.bundle import Priority
from node.config import PRIORITY_CLASSES


@pytest.mark.req("REQ-DUR-06")
def test_parity_scales_with_priority_and_buckets_split_evenly() -> None:
    assert set(PRIORITY_CLASSES) == set(Priority)
    parity = [PRIORITY_CLASSES[p].m for p in (Priority.ROUTINE, Priority.URGENT, Priority.CRITICAL)]
    assert parity == sorted(parity) and parity[0] < parity[-1]
    for cls in PRIORITY_CLASSES.values():
        assert all(b % cls.k == 0 for b in BUCKETS)
