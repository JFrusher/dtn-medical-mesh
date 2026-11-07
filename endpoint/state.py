"""Outgoing message status at the endpoint (spec §8.8, §10.2). Never resends automatically."""

from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    IN_TRANSIT = "in transit"
    DELIVERED = "delivered"
    NOT_DELIVERED = "NOT DELIVERED - use another channel"


@dataclass
class Outgoing:
    recipient: str
    bundle_id: bytes
    created_ms: int
    lifetime_s: int
    delivered_ms: int | None = None

    def status(self, now_ms: int) -> Status:
        if self.delivered_ms is not None:
            return Status.DELIVERED  # a late ACK still beats the alarm
        if now_ms > self.created_ms + self.lifetime_s * 1000:
            return Status.NOT_DELIVERED
        return Status.IN_TRANSIT

    @property
    def expires_ms(self) -> int:
        return self.created_ms + self.lifetime_s * 1000
