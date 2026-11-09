import array

import pytest

from simulation.radio_model import WIFI_WAVELENGTH_M
from simulation.scenario import load_scenario
from simulation.terrain import Terrain, knife_edge_db


def flat(cols: int = 40, ridge_m: int = 0, built: int = 0) -> Terrain:
    """A 40 x 3 grid of 50 m cells at 1000 m, optionally with a ridge across column 20."""
    heights = [1000 + (ridge_m if c == 20 else 0) for _ in range(3) for c in range(cols)]
    return Terrain(
        (0.0, 0.0), 0, 0, 50, cols, 3, array.array("h", heights), bytes([built]) * cols * 3
    )


A, B = (25.0, 75.0), (1975.0, 75.0)


@pytest.mark.req("REQ-SIM-03")
def test_knife_edge_matches_itu_p526() -> None:
    assert knife_edge_db(-1) == 0
    assert knife_edge_db(0) == pytest.approx(6.0, abs=0.1)  # grazing: the textbook 6 dB
    assert knife_edge_db(2.4) == pytest.approx(20.6, abs=0.2)


@pytest.mark.req("REQ-SIM-03")
def test_clear_flat_path_costs_nothing_and_a_ridge_blocks() -> None:
    assert flat().loss_db(A, B, 6, 6, WIFI_WAVELENGTH_M, 20) == 0
    blocked = flat(ridge_m=30).loss_db(A, B, 6, 6, WIFI_WAVELENGTH_M, 20)
    assert blocked > 25
    higher = flat(ridge_m=30).loss_db(A, B, 40, 40, WIFI_WAVELENGTH_M, 20)
    assert higher < blocked  # taller masts see over it


@pytest.mark.req("REQ-SIM-03")
def test_clutter_loss_scales_with_built_share_and_length() -> None:
    full = flat(built=255).loss_db(A, B, 6, 6, WIFI_WAVELENGTH_M, 20)
    half = flat(built=128).loss_db(A, B, 6, 6, WIFI_WAVELENGTH_M, 20)
    assert full == pytest.approx(20 * 1950 / 100, rel=0.06) and half == pytest.approx(
        full / 2, rel=0.02
    )


def test_points_outside_the_grid_fail_fast() -> None:
    with pytest.raises(ValueError, match="outside"):
        flat().ground_m((-10, 0))


@pytest.mark.req("REQ-SIM-03")
def test_arusha_terrain_matches_the_scenario_and_known_heights() -> None:
    s = load_scenario("arusha")
    t = Terrain.load("arusha")
    assert s.terrain == "arusha" and t.origin == s.geo
    hospital = next(n for n in s.nodes if n.config.node_id == "HOSPITAL").mobility.position(0)
    assert 1350 < t.ground_m(hospital) < 1450  # Arusha town sits at about 1400 m
