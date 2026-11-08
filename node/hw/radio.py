"""Radios (spec §8.2, §8.10). Range and bandwidth belong to the world model."""

from enum import Enum
from typing import Protocol


class RadioKind(Enum):
    WIFI = "wifi"
    BLE = "ble"
    LORA = "lora"


class Radio(Protocol):
    @property
    def kind(self) -> RadioKind: ...

    @property
    def max_frame_bytes(self) -> int: ...

    def send(self, peer_id: str, frame: bytes) -> None:
        """Best effort: the frame may be lost or cut off; the protocol must tolerate that."""
        ...
