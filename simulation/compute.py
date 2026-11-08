"""CPU and storage time a node spends on each event (spec §12.2).

A node is busy while it verifies signatures, signs in the secure element, hashes what it
receives, and commits to its database. Its actions take effect only when that time has passed,
and events arriving meanwhile wait in line. Endpoint (laptop) work is not charged.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from node.events import Action, Delete, Store
from node.pki import Role
from simulation.assumptions import group


@dataclass(frozen=True)
class Profile:
    verify_s: float
    sha256_bytes_per_s: float
    db_commit_s: float
    db_bytes_per_s: float


def profiles() -> dict[Role, Profile]:
    c = group("compute")

    def board(name: str) -> Profile:
        return Profile(
            c[f"verify_s_{name}"],
            c[f"sha256_bytes_per_s_{name}"],
            c[f"db_commit_s_{name}"],
            c[f"db_bytes_per_s_{name}"],
        )

    zero, pi5 = board("zero2w"), board("pi5")
    return {Role.RELAY: zero, Role.MULE: zero, Role.SITE: pi5, Role.HUB: pi5}


SE_SIGN_S = group("compute")["se_sign_s"]


def flash_bytes(actions: Sequence[Action]) -> int:
    """Bytes that reach flash: whole pages, written twice in WAL mode."""
    s = group("storage")
    page, factor = int(s["page_bytes"]), int(s["wal_factor"])
    total = 0
    for a in actions:
        if isinstance(a, Store | Delete):
            size = len(a.key) + (len(a.value) if isinstance(a, Store) else 0)
            total += -(-size // page) * page * factor
    return total


def endurance_bytes(role: Role) -> float:
    s = group("storage")
    tbw = s["endurance_tbw_nvme"] if role in (Role.SITE, Role.HUB) else s["endurance_tbw_sd"]
    return tbw * 1e12


def cost_s(
    p: Profile, signs: int, verifies: int, received_bytes: int, actions: Sequence[Action]
) -> float:
    writes = [a for a in actions if isinstance(a, Store | Delete)]
    written = sum(len(a.value) for a in writes if isinstance(a, Store))
    return (
        signs * SE_SIGN_S
        + verifies * p.verify_s
        + received_bytes / p.sha256_bytes_per_s
        + len(writes) * p.db_commit_s
        + written / p.db_bytes_per_s
    )
