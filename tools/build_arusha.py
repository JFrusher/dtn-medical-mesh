"""Build the offline map and the Arusha scenario. Run once; the outputs are committed.

    uv run python tools/build_arusha.py

Downloads (all open licences; credits shown on the map and in docs/soup.md):
  - Arusha street map: Protomaps daily build, (c) OpenStreetMap contributors, ODbL
  - Noto Sans glyphs for map labels (SIL OFL), from protomaps/basemaps-assets
  - MapLibre GL JS, pmtiles and @protomaps/basemaps (BSD-3-Clause), from npm
  - Road routes between sites from the public OSRM demo server (4 requests)
Writes interface/static/{vendor,map}/ and simulation/scenarios/arusha.yaml.
"""

import io
import json
import math
import tarfile
import urllib.request
from pathlib import Path

import yaml
from pmtiles.reader import Reader
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "interface/static"
UA = {"User-Agent": "dtn-medical-mesh map build"}

PROTOMAPS_BUILD = "https://build.protomaps.com/20261107.pmtiles"
BBOX = (36.54, -3.45, 36.87, -3.28)  # west, south, east, north: Kisongo to Usa River
MAX_ZOOM = 14  # MapLibre over-zooms beyond this
NPM = {  # package: (version, files to vendor)
    "maplibre-gl": ("5.24.0", ["dist/maplibre-gl.js", "dist/maplibre-gl.css", "LICENSE.txt"]),
    "pmtiles": ("4.5.0", ["dist/pmtiles.js"]),
    "@protomaps/basemaps": ("5.7.2", ["dist/basemaps.js"]),
}
FONTS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]
GLYPH_RANGES = ["0-255", "256-511", "8192-8447"]  # Latin, Latin extended, punctuation

# Real places, generic names (no affiliation implied). Murieti is approximate: the health
# centre is not in OpenStreetMap, so it sits at the Muriet ward office.
SITES = {
    "HOSPITAL": ("hub", "SA_HOSPITAL", (-3.36557, 36.69487)),  # Mount Meru Regional Hospital area
    "MURIETI": ("site", "SA_MURIETI", (-3.41846, 36.65715)),
    "NGARAMTONI": ("site", "SA_NGARAMTONI", (-3.31087, 36.63970)),
    "KISONGO": ("site", "SA_KISONGO", (-3.37190, 36.57195)),
    "USA_RIVER": ("site", "SA_USA_RIVER", (-3.37808, 36.83929)),
}
VEHICLES = {  # id: (stops in order, speed m/s, dwell s, start s)
    "AMBULANCE": (["HOSPITAL", "MURIETI"], 11, 600, 300),
    "VAN_WEST": (["HOSPITAL", "KISONGO", "NGARAMTONI"], 10, 900, 0),
    "VAN_EAST": (["HOSPITAL", "USA_RIVER"], 12, 900, 1200),
    "OUTREACH_BIKE": (["MURIETI", "KISONGO"], 8, 1200, 600),
}
RELAYS = {  # solar street lights part-way along a vehicle's road: (vehicle, fraction of path)
    "LIGHT_MOSHI_ROAD": ("VAN_EAST", 0.22),
    "LIGHT_NAIROBI_ROAD": ("VAN_WEST", 0.17),
}


def get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return bytes(r.read())


def vendor() -> None:
    out = STATIC / "vendor"
    out.mkdir(parents=True, exist_ok=True)
    for pkg, (version, files) in NPM.items():
        name = pkg.split("/")[-1]
        tgz = get(f"https://registry.npmjs.org/{pkg}/-/{name}-{version}.tgz")
        with tarfile.open(fileobj=io.BytesIO(tgz)) as tar:
            for f in files:
                data = tar.extractfile(f"package/{f}")
                assert data is not None, f
                target = out / (f"{name}-LICENSE.txt" if f.startswith("LICENSE") else Path(f).name)
                target.write_bytes(data.read())
        print(f"vendored {pkg}@{version}")


def fonts() -> None:
    base = "https://raw.githubusercontent.com/protomaps/basemaps-assets/main/fonts"
    for font in FONTS:
        d = STATIC / "map/fonts" / font
        d.mkdir(parents=True, exist_ok=True)
        for rng in GLYPH_RANGES:
            (d / f"{rng}.pbf").write_bytes(get(f"{base}/{font.replace(' ', '%20')}/{rng}.pbf"))
    print(f"fonts: {len(FONTS)} faces x {len(GLYPH_RANGES)} ranges")


def tile_range(z: int) -> tuple[range, range]:
    def xy(lon: float, lat: float) -> tuple[int, int]:
        n = 2**z
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        return int((lon + 180) / 360 * n), int(y)

    (x0, y0), (x1, y1) = xy(BBOX[0], BBOX[3]), xy(BBOX[2], BBOX[1])
    return range(x0, x1 + 1), range(y0, y1 + 1)


def tiles() -> None:
    def ranged(offset: int, length: int) -> bytes:
        return get(PROTOMAPS_BUILD, {"Range": f"bytes={offset}-{offset + length - 1}"})

    src = Reader(ranged)
    wanted = []
    for z in range(MAX_ZOOM + 1):
        xs, ys = tile_range(z)
        wanted += [(z, x, y) for x in xs for y in ys]
    target = STATIC / "map/arusha.pmtiles"
    count = 0
    with target.open("wb") as f:
        w = Writer(f)
        for z, x, y in sorted(wanted, key=lambda t: zxy_to_tileid(*t)):
            data = src.get(z, x, y)
            if data:
                w.write_tile(zxy_to_tileid(z, x, y), data)
                count += 1
        meta = src.metadata()
        w.finalize(
            {
                "tile_type": TileType.MVT,
                "tile_compression": Compression.GZIP,
                "min_zoom": 0,
                "max_zoom": MAX_ZOOM,
                "min_lon_e7": int(BBOX[0] * 1e7),
                "min_lat_e7": int(BBOX[1] * 1e7),
                "max_lon_e7": int(BBOX[2] * 1e7),
                "max_lat_e7": int(BBOX[3] * 1e7),
                "center_zoom": 12,
                "center_lon_e7": int((BBOX[0] + BBOX[2]) / 2 * 1e7),
                "center_lat_e7": int((BBOX[1] + BBOX[3]) / 2 * 1e7),
            },
            {
                "attribution": meta.get("attribution"),
                "vector_layers": meta.get("vector_layers"),
                "description": f"Arusha extract of {PROTOMAPS_BUILD}",
            },
        )
    print(f"tiles: {count} written, {target.stat().st_size / 1e6:.1f} MB")


def road(stops: list[str]) -> tuple[list[tuple[float, float]], list[int]]:
    """Road path through the stops and back to the first: (lat/lon points, stop indices)."""
    path: list[tuple[float, float]] = []
    stop_idx: list[int] = []
    loop = stops + stops[:1]
    for a, b in zip(loop, loop[1:], strict=False):
        (lat_a, lon_a), (lat_b, lon_b) = SITES[a][2], SITES[b][2]
        url = (
            f"https://router.project-osrm.org/route/v1/driving/{lon_a},{lat_a};{lon_b},{lat_b}"
            "?overview=full&geometries=geojson"
        )
        coords = json.loads(get(url))["routes"][0]["geometry"]["coordinates"]
        stop_idx.append(len(path))
        path += [(round(lat, 6), round(lon, 6)) for lon, lat in coords[:-1]]  # next leg starts at b
    return path, stop_idx


def scenario() -> None:
    nodes: list[dict[str, object]] = [
        {"id": n, "role": role, "sa": sa, "at": list(ll)} for n, (role, sa, ll) in SITES.items()
    ]
    paths = {}
    for vid, (stops, speed, dwell, start) in VEHICLES.items():
        path, stop_idx = road(stops)
        paths[vid] = path
        route = {
            "waypoints": [list(p) for p in path],
            "stops": stop_idx,
            "speed": speed,
            "dwell_s": dwell,
            "start_s": start,
        }
        nodes.append({"id": vid, "role": "mule", "route": route})
    for rid, (vid, frac) in RELAYS.items():
        at = paths[vid][int(len(paths[vid]) * frac)]
        nodes.append(
            {
                "id": rid,
                "role": "relay",
                "at": list(at),
                "power": {"kind": "solar", "capacity_wh": 30, "load_w": 1, "solar_peak_w": 6},
            }
        )
    clinics = [n.lower().replace("_", "-") for n, (role, _, _) in SITES.items() if role == "site"]
    workload = []
    for i, c in enumerate(clinics):
        off = i * 900
        workload += [
            {
                "from": c,
                "to": "hospital",
                "priority": "URGENT",
                "text": "Vitals",
                "size_bytes": 300,
                "every_s": 7200,
                "start_s": 600 + off,
                "end_s": 84000,
            },
            {
                "from": c,
                "to": "hospital",
                "priority": "ROUTINE",
                "text": "Clinic note",
                "size_bytes": 2000,
                "every_s": 14400,
                "start_s": 1800 + off,
                "end_s": 84000,
            },
            {
                "from": "hospital",
                "to": c,
                "priority": "URGENT",
                "text": "Advice from the hospital",
                "every_s": 21600,
                "start_s": 5400 + off,
                "end_s": 84000,
            },
        ]
    workload += [
        {
            "from": "murieti",
            "to": "hospital",
            "priority": "CRITICAL",
            "text": "Referral: maternal, raised BP",
            "every_s": 28800,
            "start_s": 2400,
            "end_s": 84000,
        },
        {
            "from": "kisongo",
            "to": "hospital",
            "priority": "ROUTINE",
            "text": "Wound photo",
            "size_bytes": 400000,
            "every_s": 43200,
            "start_s": 3600,
            "end_s": 84000,
        },
    ]
    doc = {
        "name": "arusha",
        "duration_s": 86400,
        "tick_s": 2,
        "geo": {"lat": SITES["HOSPITAL"][2][0], "lon": SITES["HOSPITAL"][2][1]},
        "nodes": nodes,
        "endpoints": [{"name": n.lower().replace("_", "-"), "node": n} for n in SITES],
        "workload": workload,
    }
    header = (
        "# Arusha, Tanzania: real roads (OpenStreetMap via OSRM), generic site names,\n"
        "# no affiliation with any facility. Murieti's position is approximate.\n"
        "# Generated by tools/build_arusha.py.\n"
    )
    target = ROOT / "simulation/scenarios/arusha.yaml"
    target.write_text(
        header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=None, width=110)
    )
    print(f"scenario: {len(nodes)} nodes -> {target.relative_to(ROOT)}")


if __name__ == "__main__":
    vendor()
    fonts()
    tiles()
    scenario()
