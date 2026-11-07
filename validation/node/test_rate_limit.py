from dataclasses import replace

import pytest

from node.pki import Role
from validation.node.helpers import frame, make_node, make_shards, shard_msg


@pytest.mark.req("REQ-SEC-12")
def test_flooding_injector_is_capped_per_hour_but_started_bundles_complete() -> None:
    mule = make_node("M", Role.MULE)
    mule.config = replace(mule.config, max_new_bundles_per_injector_per_hour=3)
    bundles = [make_shards(bytes([i]), created=i) for i in range(5)]
    for b in bundles[:3]:
        assert frame(mule, "P", shard_msg(b[0]))
    assert frame(mule, "P", shard_msg(bundles[3][0])) == []
    assert mule.dropped["rate_limited"] == 1
    assert frame(mule, "P", shard_msg(bundles[0][1]))  # more of an admitted bundle is fine
    mule.clock_s += 3601
    assert frame(mule, "P", shard_msg(bundles[4][0]))  # the window has moved on
