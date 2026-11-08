"""The hub's admin console backend (spec §10.6), on the emulated hub.

Everything shown is what the hub itself knows: the health reports that reached it and the
hospital laptop's own messages. Every action is signed with the operator's own key (sealed
under their PIN, like staff keys) and appended to the admin log.
"""

import statistics
import time
from dataclasses import dataclass
from functools import partial
from typing import Any

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from endpoint.crypto import EndpointKeys
from endpoint.directory import DirectoryEntry, directory_body
from endpoint.keystore import KeyStore
from endpoint.state import Status
from node.core import NodeConfig
from node.events import PublishDirectory, Revoke
from node.pki import Role
from simulation.mobility import Static
from simulation.provision import provision_one
from simulation.runner import RunResult
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy

OPERATORS = ["Admin F. Mbise"]  # fictional
NOT_SEEN_S = 6 * 3600  # a node is "not seen recently" when its newest report is older
ACTIONS = ("revoke", "remove_laptop", "publish_directory", "provision_relay", "provision_mule")


@dataclass(frozen=True)
class LogEntry:
    at: float  # simulation time
    operator: str
    action: str
    target: str
    sig: bytes  # Ed25519 by the operator's key over (at, operator, action, target)

    def signing_bytes(self) -> bytes:
        return b"dtn-medical-mesh/v1/admin" + cbor2.dumps(
            [self.at, self.operator, self.action, self.target]
        )


class Admin:
    def __init__(self, result: RunResult, demo_pin: str) -> None:
        self.r = result
        self.hub = next(n for n in result.world.nodes.values() if n.config.role is Role.HUB)
        entropy = SeededEntropy("hub:operators")
        self.store = KeyStore(entropy.random_bytes(32))
        self.operator_public: dict[str, bytes] = {}
        for op in OPERATORS:
            keys = EndpointKeys.generate(entropy)
            self.store.enrol(op, demo_pin, keys, entropy)
            self.operator_public[op] = keys.signing_public
        self.keys: dict[str, EndpointKeys] = {}  # unlocked operators
        self.log: list[LogEntry] = []
        self.removed: set[str] = set()  # laptops taken out of the directory

    def sign_in(self, operator: str, pin: str) -> None:
        self.keys[operator] = self.store.unlock(operator, pin, time.monotonic())

    def act(self, operator: str, action: str, target: str) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        w = self.r.world
        if action == "revoke":
            if target not in w.nodes or target == self.hub.id:
                raise ValueError(f"cannot revoke {target!r}")
            w.dispatch(self.hub, partial(Revoke, subject=target))
        elif action == "remove_laptop":
            if target not in self.r.endpoints:
                raise ValueError(f"no laptop {target!r}")
            self.removed.add(target)
            self.publish()
        elif action == "publish_directory":
            self.publish()
        else:
            role = Role.RELAY if action == "provision_relay" else Role.MULE
            if not target or target in w.nodes:
                raise ValueError(f"give a new, unused node ID (not {target!r})")
            node = w.add_node(NodeConfig(target, role, None), Static(w.position(self.hub.id)))
            provision_one(node, self.hub)
            node.boot(w.scheduler.now)
            w.schedule_health(target)
        entry = LogEntry(w.scheduler.now, operator, action, target, b"")
        sig = self.keys[operator].signing.sign(entry.signing_bytes())
        self.log.append(LogEntry(entry.at, operator, action, target, sig))

    def publish(self) -> None:
        entries = [
            DirectoryEntry(
                e.name, e.site.config.sa or "", e.keys.encryption_public, e.keys.signing_public
            )
            for e in self.r.endpoints.values()
            if e.name not in self.removed
        ]
        self.r.world.dispatch(
            self.hub, partial(PublishDirectory, entries=tuple(directory_body(entries)))
        )

    def log_authentic(self, e: LogEntry) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(self.operator_public[e.operator]).verify(
                e.sig, e.signing_bytes()
            )
        except (InvalidSignature, KeyError):
            return False
        return True

    def state(self) -> dict[str, Any]:
        w, now = self.r.world, self.r.world.scheduler.now
        core = self.hub.core
        nodes = []
        refusals: dict[str, int] = {}
        for node_id in sorted(set(core.health) | set(w.nodes)):
            rep = core.health.get(node_id)
            for reason, count in (rep.refusals if rep else {}).items():
                refusals[reason] = refusals.get(reason, 0) + count
            age = now - (rep.at_ms / 1000 - SIM_EPOCH) if rep else None
            known = w.nodes.get(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "role": known.config.role.value if known else None,
                    "sa": known.config.sa if known else None,
                    "revoked": node_id in core.revoked,
                    "version": rep.version if rep else None,
                    "seen_s_ago": round(age) if age is not None else None,
                    "stale": age is None or age > NOT_SEEN_S,
                    "battery_pct": rep.battery_pct if rep else None,
                    "temp_c": rep.temp_c if rep else None,
                    "storage_pct": round(100 * rep.used_bytes / rep.capacity_bytes, 3)
                    if rep
                    else None,
                    "contacts": rep.contacts if rep else None,
                    "light": rep.light if rep else None,
                    "refusals": rep.refusals if rep else {},
                }
            )
        hospital = next(e for e in self.r.endpoints.values() if e.site is self.hub)
        clinics: dict[str, dict[str, Any]] = {}
        for r in hospital.inbox:
            clinics.setdefault(r.sender or "unverified", {"arrivals": []})["arrivals"].append(
                r.at_s - r.sent_s
            )
        now_ms = int((SIM_EPOCH + now) * 1000)
        for out in hospital.sent.values():
            c = clinics.setdefault(out.recipient, {"arrivals": []})
            c["not_delivered"] = c.get("not_delivered", 0) + (
                out.status(now_ms) is Status.NOT_DELIVERED
            )
        per_clinic = [
            {
                "clinic": name,
                "arrived": len(c["arrivals"]),
                "p50_min": round(statistics.median(c["arrivals"]) / 60) if c["arrivals"] else None,
                "max_min": round(max(c["arrivals"]) / 60) if c["arrivals"] else None,
                "not_delivered": c.get("not_delivered", 0),
            }
            for name, c in sorted(clinics.items())
        ]
        laptops = [
            {"name": e.name, "site": e.site.id, "in_directory": e.name not in self.removed}
            for e in self.r.endpoints.values()
        ]
        log = [
            {
                "at": e.at,
                "operator": e.operator,
                "action": e.action,
                "target": e.target,
                "signed": self.log_authentic(e),
            }
            for e in self.log
        ]
        return {
            "nodes": nodes,
            "laptops": laptops,
            "clinics": per_clinic,
            "refusals": refusals,
            "log": log,
            "operators": OPERATORS,
            "actions": list(ACTIONS),
        }
