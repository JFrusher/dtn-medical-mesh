"""The node's configuration file, /etc/satchel/node.toml (spec §9.3), signed at provisioning.

Hubs are signed by the root (present at a hub's provisioning); every other node by the hub
that issued its certificate. A node refuses to start from a file that is unsigned, altered,
or signed by anything that does not chain to its root key.
"""

import json
import tomllib

import cbor2

from node.core import NodeConfig
from node.crypto import verify_p256
from node.pki import Cert, Role, Signer, decode_cert, encode_cert

_CONTEXT = b"dtn-medical-mesh/v1/node-config"
ROOT_SIGNED = "root"


class ConfigError(ValueError):
    pass


def _signing_bytes(c: NodeConfig) -> bytes:
    fields = [
        c.node_id,
        c.role.value,
        c.sa,
        c.capacity_bytes,
        c.max_new_bundles_per_injector_per_hour,
    ]
    return _CONTEXT + cbor2.dumps(fields)


def write(config: NodeConfig, sign: Signer, issuer: Cert | None) -> str:
    """issuer: the hub cert that signs, or None when the root signs (hubs)."""
    sig = sign(_signing_bytes(config))
    rate = config.max_new_bundles_per_injector_per_hour
    q = json.dumps  # TOML basic strings take JSON escapes
    return "\n".join(
        [
            "[node]",
            f"node_id = {q(config.node_id)}",
            f"role = {q(config.role.value)}",
            f"sa = {q(config.sa or '')}",
            f"capacity_bytes = {config.capacity_bytes}",
            f"max_new_bundles_per_injector_per_hour = {rate}",
            "",
            "[signature]",
            f"issuer = {q(encode_cert(issuer).hex() if issuer else ROOT_SIGNED)}",
            f"sig = {q(sig.hex())}",
            "",
        ]
    )


def load(text: str, root_pubkey: bytes) -> NodeConfig:
    try:
        doc = tomllib.loads(text)
        n, s = doc["node"], doc["signature"]
        config = NodeConfig(
            str(n["node_id"]),
            Role(n["role"]),
            str(n["sa"]) or None,
            int(n["capacity_bytes"]),
            int(n["max_new_bundles_per_injector_per_hour"]),
        )
        sig = bytes.fromhex(s["sig"])
        if s["issuer"] == ROOT_SIGNED:
            signer = root_pubkey
            if config.role is not Role.HUB:
                raise ConfigError("only a hub's config is signed by the root")
        else:
            issuer = decode_cert(bytes.fromhex(s["issuer"]))
            if issuer.role is not Role.HUB or not verify_p256(
                root_pubkey, issuer.signing_bytes(), issuer.sig
            ):
                raise ConfigError("config issuer is not a hub certified by this root")
            signer = issuer.pubkey
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"node.toml is missing its signature or malformed: {e}") from e
    if not verify_p256(signer, _signing_bytes(config), sig):
        raise ConfigError("node.toml signature does not match its contents")
    return config
