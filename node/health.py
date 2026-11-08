"""Node health reports (spec §9.3): signed by the node, the newest per node replaces older ones.

Not TELEMETRY bundles: relays and mules cannot inject bundles, and only the latest state of
each node matters, so each node keeps one report per node and passes it on after everything
else in a contact.
"""

from dataclasses import dataclass, replace

import cbor2

from node.bundle import FormatError
from node.pki import Signer

_CONTEXT = b"dtn-medical-mesh/v1/health"
FIELDS = (
    "node", "signer", "seq", "at_ms", "version", "battery_pct", "temp_c", "used_bytes",
    "capacity_bytes", "contacts", "refusals", "light",
)  # fmt: skip


@dataclass(frozen=True)
class HealthReport:
    node: str
    signer: str  # the node's cert ID
    seq: int
    at_ms: int  # HLC wall time when written
    version: str  # software version running
    battery_pct: float | None  # None on mains
    temp_c: float | None  # enclosure temperature
    used_bytes: int
    capacity_bytes: int
    contacts: int  # contacts since the previous report
    refusals: dict[str, int]  # cumulative since boot, by reason
    light: str  # status light state (spec §11.4)
    sig: bytes = b""

    def signing_bytes(self) -> bytes:
        return _CONTEXT + cbor2.dumps([getattr(self, f) for f in FIELDS])


def sign_report(report: HealthReport, sign: Signer) -> HealthReport:
    return replace(report, sig=sign(report.signing_bytes()))


def encode_report(r: HealthReport) -> bytes:
    return cbor2.dumps([*(getattr(r, f) for f in FIELDS), r.sig])


def decode_report(data: bytes) -> HealthReport:
    try:
        values = cbor2.loads(data)
        if not isinstance(values, list) or len(values) != len(FIELDS) + 1:
            raise FormatError("bad health report")
        r = HealthReport(*values)
        ints = (r.seq, r.at_ms, r.used_bytes, r.capacity_bytes, r.contacts)
        if not (
            isinstance(r.node, str)
            and isinstance(r.signer, str)
            and all(isinstance(i, int) and i >= 0 for i in ints)
            and isinstance(r.refusals, dict)
            and all(isinstance(k, str) and isinstance(v, int) for k, v in r.refusals.items())
            and isinstance(r.sig, bytes)
        ):
            raise FormatError("bad health report fields")
        return r
    except (TypeError, ValueError, cbor2.CBORDecodeError) as e:
        raise FormatError(str(e)) from e
