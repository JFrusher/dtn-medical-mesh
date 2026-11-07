"""Inputs to and outputs from the sans-IO NodeCore (spec §9.1).

Drivers (emulator, hardware daemon) turn the world into Events and apply Actions.
Contract: a driver applies a handle() result's actions strictly in list order, and
stops if any action fails. This is how "commit the shard, then send the custody
receipt" (REQ-DUR-03, REQ-PROTO-04) is guaranteed.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from node.hw.radio import RadioKind

# --- Events -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EventBase:
    mono: float  # monotonic seconds since this node booted
    wall: float | None  # RTC seconds since epoch; None if the RTC failed


@dataclass(frozen=True, kw_only=True)
class ContactUp(EventBase):
    peer_id: str
    radio: RadioKind


@dataclass(frozen=True, kw_only=True)
class ContactDown(EventBase):
    peer_id: str
    radio: RadioKind


@dataclass(frozen=True, kw_only=True)
class FrameReceived(EventBase):
    peer_id: str
    radio: RadioKind
    frame: bytes


@dataclass(frozen=True, kw_only=True)
class Timer(EventBase):
    name: str


@dataclass(frozen=True, kw_only=True)
class PowerLow(EventBase):
    """External power lost; the UPS hold-up time is running."""


@dataclass(frozen=True, kw_only=True)
class Inject(EventBase):
    """All shards of one new bundle, handed over by the attached endpoint (site nodes only)."""

    shards: tuple[bytes, ...]


@dataclass(frozen=True, kw_only=True)
class EndpointAck(EventBase):
    """The attached endpoint opened this bundle: issue a delivery ACK (spec §8.7)."""

    bundle_id: bytes


@dataclass(frozen=True, kw_only=True)
class Revoke(EventBase):
    """Operator at a hub revokes a node (e.g. reported stolen)."""

    subject: str


Event = ContactUp | ContactDown | FrameReceived | Timer | PowerLow | Inject | EndpointAck | Revoke

# --- Actions ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SendFrame:
    peer_id: str
    radio: RadioKind
    frame: bytes


@dataclass(frozen=True, kw_only=True)
class Store:
    """Durably write one record; committed before any later action runs."""

    table: str
    key: bytes
    value: bytes


@dataclass(frozen=True, kw_only=True)
class Delete:
    table: str
    key: bytes


@dataclass(frozen=True, kw_only=True)
class SetTimer:
    name: str
    at_mono: float


@dataclass(frozen=True, kw_only=True)
class Deliver:
    """Hand a rebuilt bundle (header + padded ciphertext) to the attached endpoint."""

    bundle: bytes


@dataclass(frozen=True, kw_only=True)
class Emit:
    """Telemetry for the map (spec §10.3)."""

    telemetry: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class Delivered:
    """Tell the attached endpoint that a bundle it sent was delivered (spec §8.7)."""

    bundle_id: bytes


Action = SendFrame | Store | Delete | SetTimer | Deliver | Delivered | Emit
