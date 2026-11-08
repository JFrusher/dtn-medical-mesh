"""Terrain and building clutter between two antennas (spec §12.2), for geo scenarios.

Diffraction uses the single worst knife edge on the path (ITU-R P.526-15 §4.1, eq. 31), with
the earth's bulge for an effective radius of 4/3 Earth (ITU-R P.453). Clutter adds a loss per
metre of path in proportion to how built-up each cell is.
"""

import array
import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path

GEO_DIR = Path(__file__).parent / "geo"
EARTH_EFFECTIVE_M = 6_371_000 * 4 / 3
Point = tuple[float, float]


def knife_edge_db(v: float) -> float:
    """ITU-R P.526-15 eq. 31: loss for Fresnel-Kirchhoff parameter v (0 when v <= -0.78)."""
    if v <= -0.78:
        return 0.0
    return 6.9 + 20 * math.log10(math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)


@dataclass(frozen=True)
class Terrain:
    origin: tuple[float, float]  # lat, lon the grid's metres are measured from
    x0: float
    y0: float
    cell_m: float
    cols: int
    rows: int
    elevation: array.array  # type: ignore[type-arg]  # int16 metres, row-major, row 0 south
    clutter: bytes  # building share of each cell, 0-255

    @classmethod
    def load(cls, name: str) -> "Terrain":
        d = GEO_DIR / name
        meta = json.loads((d / "terrain.json").read_text())
        elevation = array.array("h")
        elevation.frombytes(gzip.decompress((d / "elevation.i16.gz").read_bytes()))
        clutter = gzip.decompress((d / "clutter.u8.gz").read_bytes())
        cells = meta["cols"] * meta["rows"]
        if len(elevation) != cells or len(clutter) != cells:
            raise ValueError(f"terrain {name}: grid size does not match terrain.json")
        return cls((meta["origin"][0], meta["origin"][1]), meta["x0"], meta["y0"],
                   meta["cell_m"], meta["cols"], meta["rows"], elevation, clutter)  # fmt: skip

    def _cell(self, p: Point) -> int:
        c = int((p[0] - self.x0) // self.cell_m)
        r = int((p[1] - self.y0) // self.cell_m)
        if not (0 <= c < self.cols and 0 <= r < self.rows):
            raise ValueError(f"point {p} is outside the terrain grid")
        return r * self.cols + c

    def ground_m(self, p: Point) -> float:
        return float(self.elevation[self._cell(p)])

    def built(self, p: Point) -> float:
        return self.clutter[self._cell(p)] / 255

    def loss_db(
        self, a: Point, b: Point, ha_m: float, hb_m: float, wavelength_m: float,
        clutter_db_per_100m: float,
    ) -> float:  # fmt: skip
        """Extra loss beyond free-space-style path loss: worst knife edge plus clutter."""
        d = math.dist(a, b)
        steps = int(d // (self.cell_m / 2))
        if steps < 2:
            return 0.0
        top_a, top_b = self.ground_m(a) + ha_m, self.ground_m(b) + hb_m
        worst_v, built_m = -math.inf, 0.0
        for i in range(1, steps):
            f = i / steps
            p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            d1, d2 = d * f, d * (1 - f)
            ray = top_a + (top_b - top_a) * f
            ground = self.ground_m(p) + d1 * d2 / (2 * EARTH_EFFECTIVE_M)
            v = (ground - ray) * math.sqrt(2 * d / (wavelength_m * d1 * d2))
            worst_v = max(worst_v, v)
            built_m += self.built(p) * d / steps
        return knife_edge_db(worst_v) + clutter_db_per_100m * built_m / 100
