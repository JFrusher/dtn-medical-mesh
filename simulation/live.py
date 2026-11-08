"""Live demo: the emulator as the interface's backend, in (scaled) real time.

uv run dtn-demo [scenario] [--seed N] [--speed X] [--port P]
"""

import argparse
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from endpoint import fhir
from endpoint.fhir import Attachment, Message, Patient, Referral, Result, Vital
from endpoint.keystore import KeyStore
from endpoint.padding import PayloadTooLarge
from endpoint.state import Status
from interface.app import MessageIn, PatientIn, create_app
from interface.telemetry import Telemetry
from node.bundle import Priority
from node.config import PRIORITY_CLASSES
from node.core import NodeCore
from simulation.admin import Admin
from simulation.mobility import Route
from simulation.runner import RunResult, build
from simulation.scenario import load_scenario
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy

# shortcut: every emulated staff member shares one published demo PIN; a real laptop enrols
# each person's own PIN at provisioning (spec §9.4).
DEMO_PIN = "2580"
STAFF = {
    "hospital": ["Dr. N. Mushi", "Nurse R. Kimaro"],
    "clinic": ["Nurse A. Mollel", "Clinical officer J. Laizer"],
}
DEMO_PATIENTS = [
    ("Neema Lyimo", 28, "female"),
    ("Baraka Massawe", 4, "male"),
    ("Rehema Swai", 61, "female"),
]  # fictional


class LiveSim:
    """The emulator as the interface's backend. Each endpoint stands for a clinic laptop: its
    keys are sealed per staff member in a KeyStore and sign-in unlocks them. shortcut: the
    emulated laptop keeps decrypting arrivals while nobody is signed in; a real one queues
    ciphertext until someone signs in."""

    def __init__(self, result: RunResult, speed: float = 60.0) -> None:
        self.r = result
        self.speed = speed
        self.paused = False
        self.admin = Admin(result, DEMO_PIN)
        self.keystores: dict[str, KeyStore] = {}
        self.registers: dict[str, list[Patient]] = {}
        for name, e in result.endpoints.items():
            entropy = SeededEntropy(f"{name}:laptop")
            store = KeyStore(entropy.random_bytes(32))
            for staff in STAFF[self.role(name)]:
                store.enrol(staff, DEMO_PIN, e.keys, entropy)
            self.keystores[name] = store
            prefix = name[:3].upper()
            self.registers[name] = (
                [
                    Patient(f"{prefix}-{i + 1:04}", n, a, s)
                    for i, (n, a, s) in enumerate(DEMO_PATIENTS)
                ]
                if self.role(name) == "clinic"
                else []
            )

    def role(self, endpoint: str) -> str:
        site = self.r.endpoints[endpoint].site
        return "hospital" if site.config.role.value == "hub" else "clinic"

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
                    "hostile": n.core_class is not NodeCore,
                    "light": n.core.status_light() if n.up else None,
                }
            )
        statuses = [e.status(b) for e in self.r.endpoints.values() for b in e.sent]
        return {
            "t": self.now,
            "geo": self.r.scenario.geo,
            "speed": self.speed,
            "paused": self.paused,
            "nodes": nodes,  # type: ignore[typeddict-item]
            "links": sorted(tuple(sorted(p)) for p in w.contacts),  # type: ignore[misc]
            "lora": sorted({src for t, src in w.lora_log if t > self.now - 5 * self.speed}),
            "transfers": [
                {
                    "t": t,
                    "from": r.sender,
                    "to": to,
                    "pieces": len(r.shard_ids),
                    "bundles": sorted({sid[:32].hex() for sid in r.shard_ids}),
                }
                for t, to, r in w.receipts
                if t > self.now - 3 * self.speed
            ],
            "events": self.r.log[-12:],
            "stats": {
                "sent": len(statuses),
                "delivered": statuses.count(Status.DELIVERED),
                "in_transit": statuses.count(Status.IN_TRANSIT),
                "not_delivered": statuses.count(Status.NOT_DELIVERED),
                "rejected": sum(
                    sum(n.core.dropped.values())
                    for n in w.nodes.values()
                    if n.up and n.core_class is NodeCore
                ),
            },
        }

    def endpoints(self) -> list[dict[str, Any]]:
        return [
            {
                "name": e.name,
                "node": e.site.id,
                "sa": e.site.config.sa,
                "role": self.role(e.name),
                "staff": STAFF[self.role(e.name)],
                "contacts": sorted(e.directory),
            }
            for e in self.r.endpoints.values()
        ]

    def admin_sign_in(self, operator: str, pin: str) -> None:
        self.admin.sign_in(operator, pin)

    def admin_state(self) -> dict[str, Any]:
        return self.admin.state()

    def admin_act(self, operator: str, action: str, target: str) -> None:
        self.admin.act(operator, action, target)
        self.r.log.append(f"[ADMIN] {operator}: {action} {target}")

    def sign_in(self, endpoint: str, staff: str, pin: str) -> None:
        keys = self.keystores[endpoint].unlock(staff, pin, time.monotonic())
        assert keys.signing_public == self.r.endpoints[endpoint].keys.signing_public

    def patients(self, endpoint: str) -> list[dict[str, Any]]:
        """A clinic's own register; at the hospital, the patients referred to it."""
        if self.role(endpoint) == "clinic":
            known = self.registers[endpoint]
        else:
            seen: dict[str, Patient] = {}
            for r in self.r.endpoints[endpoint].inbox:
                if (p := r.message.patient) is not None:
                    seen[p.local_id] = p
            known = list(seen.values())
        return [_patient(p) for p in known]

    def add_patient(self, endpoint: str, patient: PatientIn) -> None:
        p = Patient(patient.local_id.strip(), patient.name.strip(), patient.age, patient.sex)
        if any(q.local_id == p.local_id for q in self.registers[endpoint]):
            raise ValueError(f"local ID {p.local_id} is already registered")
        self.registers[endpoint].append(p)

    def journeys(self) -> list[dict[str, Any]]:
        out = []
        for e in self.r.endpoints.values():
            for bid, sent in e.sent.items():
                content, _, at = e.sent_content[bid]
                out.append(
                    {
                        "id": bid.hex(),
                        "t": at,
                        "at": _clock(at),
                        "from": e.name,
                        "to": sent.recipient,
                        "text": _decode(content).summary(),
                    }
                )
        return sorted(out, key=lambda j: -float(j["t"]))

    def journey(self, bundle_hex: str) -> dict[str, Any]:
        """Where one message has been and where its pieces are now, from custody receipts."""
        bid = bytes.fromhex(bundle_hex)
        origin = next((e for e in self.r.endpoints.values() if bid in e.sent), None)
        if origin is None:
            raise KeyError(bundle_hex)
        out, (content, prio, sent_at) = origin.sent[bid], origin.sent_content[bid]
        hops = [
            {
                "t": t,
                "at": _clock(t),
                "from": r.sender,
                "to": to,
                "pieces": sum(1 for s in r.shard_ids if s[:32] == bid),
            }
            for t, to, r in self.r.world.receipts
            if any(s[:32] == bid for s in r.shard_ids)
        ]
        holders = {
            n.id: count
            for n in self.r.world.nodes.values()
            if n.up
            and (count := sum(1 for s in n.core.shards.values() if s.header.bundle_id == bid))
        }
        cls = PRIORITY_CLASSES[prio]
        opened = next(
            (r.at_s for e in self.r.endpoints.values() for r in e.inbox if r.bundle_id == bid), None
        )
        acked = out.delivered_ms / 1000 - SIM_EPOCH if out.delivered_ms else None
        return {
            "id": bundle_hex,
            "summary": _decode(content).summary(),
            "priority": prio.name,
            "from": origin.name,
            "from_node": origin.site.id,
            "to": out.recipient,
            "to_node": self.r.endpoints[out.recipient].site.id,
            "status": origin.status(bid).value,
            "sent": _clock(sent_at),
            "opened": _clock(opened) if opened is not None else None,
            "acked": _clock(acked) if acked is not None else None,
            "k": cls.k,
            "total": cls.k + cls.m,
            "hops": hops,
            "holders": holders,
        }

    def messages(self, endpoint: str) -> list[dict[str, Any]]:
        e = self.r.endpoints[endpoint]
        out: list[dict[str, Any]] = []
        for bid, sent in e.sent.items():
            content, prio, at = e.sent_content[bid]
            msg = _decode(content)
            out.append(
                {
                    **_fields(msg),
                    "id": bid.hex(),
                    "direction": "out",
                    "peer": sent.recipient,
                    "priority": prio.name,
                    "status": e.status(bid).value,
                    "flag_at": _clock(sent.expires_ms / 1000 - SIM_EPOCH),
                    "t": at,
                    "at": _clock(at),
                }
            )
        for r in e.inbox:
            out.append(
                {
                    **_fields(r.message),
                    "id": r.bundle_id.hex(),
                    "direction": "in",
                    "peer": r.sender or "UNVERIFIED SENDER",
                    "priority": r.priority.name,
                    "status": "received",
                    "t": r.at_s,
                    "at": _clock(r.at_s),
                    "written_at": _clock(r.sent_s),
                    "waited_s": r.at_s - r.sent_s,
                }
            )
        return sorted(out, key=lambda m: float(m["t"]))

    def send(
        self, endpoint: str, msg: MessageIn, attachments: list[tuple[str, str, bytes]], author: str
    ) -> str:
        e = self.r.endpoints[endpoint]
        if msg.to not in e.directory or msg.to == endpoint:
            raise ValueError(f"{msg.to!r} is not in {endpoint}'s directory")
        message = Message(
            text=msg.text,
            vitals=tuple(Vital(v.code, v.value) for v in msg.vitals),
            attachments=tuple(Attachment(t, c, d) for t, c, d in attachments),
            in_response_to=msg.in_response_to,
            author=author,
            patient=Patient(**msg.patient.model_dump()) if msg.patient else None,
            referral=Referral(**msg.referral.model_dump()) if msg.referral else None,
            decision=msg.decision,
            result=Result(**msg.result.model_dump()) if msg.result else None,
        )
        try:
            bid = e.send_message(msg.to, message, Priority[msg.priority])
        except PayloadTooLarge as err:
            raise ValueError(f"{err}: compress it or send it another way") from err
        self.r.log.append(f"[UI] {endpoint} -> {msg.to} ({msg.priority})")
        return bid.hex()


def _decode(content: bytes) -> Message:
    return fhir.decode(content)


def _patient(p: Patient) -> dict[str, Any]:
    return {"local_id": p.local_id, "name": p.name, "age": p.age, "sex": p.sex}


def _fields(m: Message) -> dict[str, Any]:
    """What the staff app shows of a message's content."""
    return {
        "text": m.text,
        "summary": m.summary(),
        "in_response_to": m.in_response_to,
        "author": m.author,
        "patient": _patient(m.patient) if m.patient else None,
        "referral": m.referral.__dict__ if m.referral else None,
        "decision": m.decision,
        "result": m.result.__dict__ if m.result else None,
        "vitals": [{"code": v.code, "value": v.value} for v in m.vitals],
        "attachments": [{"title": a.title, "bytes": len(a.data)} for a in m.attachments],
    }


def _clock(t: float) -> str:
    return datetime.fromtimestamp(SIM_EPOCH + t, UTC).strftime("%a %H:%M:%S")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("scenario", nargs="?", default="arusha")
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
