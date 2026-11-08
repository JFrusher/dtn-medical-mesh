"""Emulated releases, signed by the run's offline release key (spec §9.5)."""

import hashlib

from node.update import Manifest, sign_manifest
from simulation.assumptions import group
from simulation.provision import release_key
from simulation.virtual_hw import SeededEntropy

ALL_ROLES = ("relay", "mule", "site", "hub")


def make_release(
    seed: int, version: str, min_version: str, roles: tuple[str, ...] = ALL_ROLES
) -> tuple[Manifest, bytes]:
    payload = SeededEntropy(f"{seed}:payload:{version}").random_bytes(
        int(group("node")["update_bytes"])
    )
    m = Manifest(version, roles, min_version, hashlib.sha256(payload).digest(), len(payload))
    return sign_manifest(m, release_key(seed).sign), payload
