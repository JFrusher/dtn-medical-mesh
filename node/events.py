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
class HealthTick(EventBase):
    """Time to write a health report (spec §9.3); the driver reads the hardware sensors."""

    battery_pct: float | None
    temp_c: float | None
    version: str
    external_power: bool  # mains, solar in daylight, or the vehicle's engine running


@dataclass(frozen=True, kw_only=True)
class StageUpdate(EventBase):
    """Operator at a hub rolls a release-signed update out to `stage` (spec §9.5)."""

    manifest: bytes
    payload: bytes
    stage: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Inject(EventBase):
    """All shards of one new bundle, handed over by a laptop on the site node's access point
    (spec §9.6), signed with the laptop's endpoint key (see core.inject_signing_bytes)."""

    shards: tuple[bytes, ...]
    laptop_key: bytes  # Ed25519 public key, as listed in the hub-signed directory
    sig: bytes


@dataclass(frozen=True, kw_only=True)
class EndpointAck(EventBase):
    """The attached endpoint opened this bundle: issue a delivery ACK (spec §8.7)."""

    bundle_id: bytes


@dataclass(frozen=True, kw_only=True)
class Revoke(EventBase):
    """Operator at a hub revokes a node (e.g. reported stolen)."""

    subject: str


@dataclass(frozen=True, kw_only=True)
class PublishDirectory(EventBase):
    """Operator at a hub publishes the endpoint directory (CBOR-able entries)."""

    entries: tuple[object, ...]


Event = (
    ContactUp
    | ContactDown
    | FrameReceived
    | Timer
    | PowerLow
    | HealthTick
    | Inject
    | EndpointAck
    | Revoke
    | PublishDirectory
    | StageUpdate
)

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


@dataclass(frozen=True, kw_only=True)
class DirectoryUpdate:
    """Hand a newer hub-signed endpoint directory (and its issuer cert) to the endpoint."""

    doc: bytes
    issuer: bytes


@dataclass(frozen=True, kw_only=True)
class Install:
    """Write the verified payload to the spare A/B slot and reboot into it on probation."""

    version: str
    payload: bytes


Action = (
    SendFrame | Store | Delete | SetTimer | Deliver | Delivered | DirectoryUpdate | Emit | Install
)
