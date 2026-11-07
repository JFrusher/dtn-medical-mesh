"""Priority class defaults (spec §6.3). 'Critical' means important to deliver, not time-critical."""

from dataclasses import dataclass
from types import MappingProxyType

from node.bundle import Priority


@dataclass(frozen=True)
class PriorityClass:
    ttl_s: int
    k: int
    m: int


PRIORITY_CLASSES = MappingProxyType(
    {
        Priority.CRITICAL: PriorityClass(ttl_s=6 * 3600, k=4, m=8),
        Priority.URGENT: PriorityClass(ttl_s=24 * 3600, k=4, m=4),
        Priority.ROUTINE: PriorityClass(ttl_s=72 * 3600, k=4, m=2),
        Priority.TELEMETRY: PriorityClass(ttl_s=12 * 3600, k=1, m=0),
    }
)
