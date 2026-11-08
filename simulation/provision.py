"""Provision virtual nodes the way a hub would in person (spec §7.6)."""

from functools import partial

from endpoint.directory import DirectoryEntry, directory_body
from node.events import PublishDirectory
from node.pki import HUB_CERT_LIFETIME_S, ROOT, Identity, Role, encode_cert, encode_doc, issue
from simulation.endpoints import VirtualEndpoint
from simulation.virtual_hw import SIM_EPOCH, SeededEntropy, SoftwareSecureElement
from simulation.world import SimNode, World


def provision(
    nodes: list[SimNode], seed: int, unregistered: frozenset[str] = frozenset()
) -> SoftwareSecureElement:
    """Issue every node a certificate: hubs from the root, everyone else from the first hub.
    Nodes in `unregistered` get a look-alike chain from a rogue root instead.

    Returns the root's secure element (offline in reality; kept for tests and hub tooling).
    """
    root = SoftwareSecureElement(SeededEntropy(f"{seed}:ROOT"))
    rogue = SoftwareSecureElement(SeededEntropy(f"{seed}:ROGUE_ROOT"))
    now = int(SIM_EPOCH)
    hubs = [n for n in nodes if n.config.role is Role.HUB]
    if not hubs:
        raise ValueError("a deployment needs at least one hub to act as CA")
    hub_certs = {}
    for hub in hubs:
        c = hub.config
        cert = issue(
            root.sign, ROOT, c.node_id, c.role, c.sa, hub.se.public_key(), now, HUB_CERT_LIFETIME_S
        )
        hub_certs[c.node_id] = cert
        hub.identity = Identity(cert, None, root.public_key(), hub.se.sign)
    issuer, issuer_cert = hubs[0], hub_certs[hubs[0].config.node_id]
    for node in nodes:
        if node.config.role is Role.HUB:
            continue
        c = node.config
        if c.node_id in unregistered:
            fake = issue(
                rogue.sign, ROOT, "ROGUE_HUB", Role.HUB, "SA_ROGUE", rogue.public_key(), now
            )
            cert = issue(
                rogue.sign, fake.cert_id, c.node_id, c.role, c.sa, node.se.public_key(), now
            )
            node.identity = Identity(cert, fake, rogue.public_key(), node.se.sign)
            continue
        cert = issue(
            issuer.se.sign, issuer_cert.cert_id, c.node_id, c.role, c.sa, node.se.public_key(), now
        )
        node.identity = Identity(cert, issuer_cert, root.public_key(), node.se.sign)
    return root


def provision_one(node: SimNode, hub: SimNode) -> None:
    """A spare node brought to the hub after the network started: the hub issues its cert."""
    c, issuer = node.config, hub.core.identity
    now = int(hub.core.clock_s)
    cert = issue(
        issuer.sign, issuer.cert.cert_id, c.node_id, c.role, c.sa, node.se.public_key(), now
    )
    node.identity = Identity(cert, issuer.cert, issuer.root_pubkey, node.se.sign)


def publish_directory(world: World, endpoints: dict[str, VirtualEndpoint]) -> None:
    """The first hub publishes the directory; endpoints get it at registration, in person."""
    hub = next(n for n in world.nodes.values() if n.config.role is Role.HUB)
    entries = [
        DirectoryEntry(
            e.name, e.site.config.sa or "", e.keys.encryption_public, e.keys.signing_public
        )
        for e in endpoints.values()
    ]
    world.dispatch(hub, partial(PublishDirectory, entries=tuple(directory_body(entries))))
    doc = hub.core.docs[f"dir:{hub.core.identity.cert.cert_id}"]
    for e in endpoints.values():
        e.update_directory(encode_doc(doc), encode_cert(hub.core.identity.cert))
