from pathlib import Path

import pytest

from node import node_toml
from node.core import NodeConfig
from node.node_toml import ConfigError
from node.pki import Role
from simulation.runner import build
from simulation.scenario import load_scenario
from simulation.virtual_hw import SeededEntropy, SoftwareSecureElement
from validation.node.helpers import TEST_HUB, TEST_HUB_SE, TEST_ROOT

RELAY = NodeConfig("LIGHT_1", Role.RELAY, None)
HUB = NodeConfig("H", Role.HUB, "SA_H")
ROOT_PUB = TEST_ROOT.public_key()


@pytest.mark.req("REQ-OPS-04")
def test_signed_configs_load() -> None:
    assert node_toml.load(node_toml.write(RELAY, TEST_HUB_SE.sign, TEST_HUB), ROOT_PUB) == RELAY
    assert node_toml.load(node_toml.write(HUB, TEST_ROOT.sign, None), ROOT_PUB) == HUB


@pytest.mark.req("REQ-OPS-04")
def test_unsigned_altered_or_wrongly_signed_configs_are_refused() -> None:
    good = node_toml.write(RELAY, TEST_HUB_SE.sign, TEST_HUB)
    unsigned = good.split("[signature]")[0]
    altered = good.replace(
        "max_new_bundles_per_injector_per_hour = 200", "max_new_bundles_per_injector_per_hour = 1"
    )
    promoted = good.replace('role = "relay"', 'role = "hub"').replace('sa = ""', 'sa = "SA_X"')
    root_signed_relay = node_toml.write(RELAY, TEST_ROOT.sign, None)  # only hubs are root-signed
    for bad in (unsigned, altered, promoted, root_signed_relay, "", "[node]\n"):
        with pytest.raises(ConfigError):
            node_toml.load(bad, ROOT_PUB)
    other_root = SoftwareSecureElement(SeededEntropy("other root")).public_key()
    with pytest.raises(ConfigError):
        node_toml.load(good, other_root)  # the issuing hub does not chain to this root


@pytest.mark.req("REQ-OPS-04")
def test_emulated_node_will_not_boot_from_a_tampered_config(tmp_path: Path) -> None:
    r = build(load_scenario("skeleton"), 1, tmp_path)
    mule = r.world.nodes["AMBULANCE_01"]
    assert 'role = "mule"' in mule.node_toml
    mule.node_toml = mule.node_toml.replace("= 200", "= 9999")
    with pytest.raises(ConfigError):
        mule.boot(0)
