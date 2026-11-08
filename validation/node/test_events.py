import dataclasses
import typing

import pytest

from node import events
from node.hw.radio import RadioKind


def test_events_and_actions_are_frozen() -> None:
    for cls in typing.get_args(events.Event) + typing.get_args(events.Action):
        assert cls.__dataclass_params__.frozen, cls


def test_event_carries_time() -> None:
    e = events.ContactUp(mono=1.0, wall=None, peer_id="AMB_04", radio=RadioKind.WIFI)
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.peer_id = "X"  # type: ignore[misc]
    assert e.wall is None
