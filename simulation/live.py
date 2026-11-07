"""Live demo: the emulator as the interface's backend, in (scaled) real time.

uv run dtn-demo [scenario] [--seed N] [--speed X] [--port P]
"""

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from endpoint import fhir
from endpoint.fhir import Attachment, Message, Vital
from endpoint.padding import PayloadTooLarge
from endpoint.state import Status
from interface.app import MessageIn, create_app
from interface.telemetry import Telemetry
from node.bundle import Priority
from simulation.mobility import Route
from simulation.runner import RunResult, build
from simulation.scenario import load_scenario
from simulation.virtual_hw import SIM_EPOCH


class LiveSim:
    def __init__(self, result: RunResult, speed: float = 60.0) -> None:
        self.r = result
        self.speed = speed
        self.paused = False

    @property
    def now(self) -> float:
        return self.r.world.scheduler.now

    def advance(self, real_seconds: float) -> None:
        if not self.paused:
            self.r.world.scheduler.run_until(self.now + real_seconds * self.speed)

    def control(self, speed: float | None, paused: bool | None) -> None:
        if speed is not None:
            if not 0 < speed <= 3600:
                raise ValueError("speed must be between 0 and 3600")
            self.speed = speed
        if paused is not None:
            self.paused = paused

    def telemetry(self) -> Telemetry:
        w = self.r.world
        nodes = []
        for n in w.nodes.values():
            x, y = w.position(n.id)
            nodes.append(
                {
                    "id": n.id,
                    "role": n.config.role.value,
                    "sa": n.config.sa,
                    "x": x,
                    "y": y,
                    "up": n.up,
                    "battery_pct": round(n.battery.pct, 1) if n.battery else None,
                    "shards": len(n.core.shards) if n.up else 0,
                    "used_bytes": n.core.used_bytes if n.up else 0,
                    "route": list(n.mobility.waypoints) if isinstance(n.mobility, Route) else None,
                }
            )
        statuses = [e.status(b) for e in self.r.endpoints.values() for b in e.sent]
        return {
            "t": self.now,
            "speed": self.speed,
            "paused": self.paused,
            "nodes": nodes,  # type: ignore[typeddict-item]
            "links": sorted(tuple(sorted(p)) for p in w.contacts),  # type: ignore[misc]
            "lora": sorted({src for t, src in w.lora_log if t > self.now - 5 * self.speed}),
            "events": self.r.log[-12:],
            "stats": {
                "sent": len(statuses),
                "delivered": statuses.count(Status.DELIVERED),
                "in_transit": statuses.count(Status.IN_TRANSIT),
                "not_delivered": statuses.count(Status.NOT_DELIVERED),
            },
        }

    def endpoints(self) -> list[dict[str, Any]]:
        return [
            {
                "name": e.name,
                "node": e.site.id,
                "sa": e.site.config.sa,
                "contacts": sorted(e.directory),
            }
            for e in self.r.endpoints.values()
        ]

    def messages(self, endpoint: str) -> list[dict[str, Any]]:
        e = self.r.endpoints[endpoint]
        out: list[dict[str, Any]] = []
        for bid, sent in e.sent.items():
            content, prio, at = e.sent_content[bid]
            msg = _decode(content)
            out.append(
                {
                    "id": bid.hex(),
                    "direction": "out",
                    "peer": sent.recipient,
                    "priority": prio.name,
                    "status": e.status(bid).value,
                    "flag_at": _clock(sent.expires_ms / 1000 - SIM_EPOCH),
                    "at": _clock(at),
                    "text": msg.text,
                    "summary": msg.summary(),
                    "in_response_to": msg.in_response_to,
                }
            )
        for r in e.inbox:
            msg = r.message
            out.append(
                {
                    "id": r.bundle_id.hex(),
                    "direction": "in",
                    "peer": r.sender or "UNVERIFIED SENDER",
                    "priority": None,
                    "status": "received",
                    "at": _clock(r.at_s),
                    "text": msg.text,
                    "summary": msg.summary(),
                    "in_response_to": msg.in_response_to,
                }
            )
        return sorted(out, key=lambda m: m["at"])

    def send(self, endpoint: str, msg: MessageIn, attachments: list[tuple[str, str, bytes]]) -> str:
        e = self.r.endpoints[endpoint]
        if msg.to not in e.directory or msg.to == endpoint:
            raise ValueError(f"{msg.to!r} is not in {endpoint}'s directory")
        message = Message(
            text=msg.text,
            vitals=tuple(Vital(v.code, v.value) for v in msg.vitals),
            attachments=tuple(Attachment(t, c, d) for t, c, d in attachments),
            in_response_to=msg.in_response_to,
        )
        try:
            bid = e.send_message(msg.to, message, Priority[msg.priority])
        except PayloadTooLarge as err:
            raise ValueError(f"{err}: compress it or send it another way") from err
        self.r.log.append(f"[UI] {endpoint} -> {msg.to} ({msg.priority})")
        return bid.hex()


def _decode(content: bytes) -> Message:
    return fhir.decode(content)


def _clock(t: float) -> str:
    return datetime.fromtimestamp(SIM_EPOCH + t, UTC).strftime("%a %H:%M:%S")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("scenario", nargs="?", default="rural-clinics")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    data = Path(tempfile.mkdtemp(prefix="dtn-demo-"))
    sim = LiveSim(build(load_scenario(args.scenario), args.seed, data), args.speed)
    print(f"dtn-medical-mesh demo: http://127.0.0.1:{args.port}  (scenario {args.scenario})")
    uvicorn.run(create_app(sim), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
