"""Build the offline map and the Arusha scenario. Run once; the outputs are committed.

    uv run python tools/build_arusha.py

Downloads (all open licences; credits shown on the map and in docs/soup.md):
  - Arusha street map: Protomaps daily build, (c) OpenStreetMap contributors, ODbL
  - Noto Sans glyphs for map labels (SIL OFL), from protomaps/basemaps-assets
  - MapLibre GL JS, pmtiles and @protomaps/basemaps (BSD-3-Clause), from npm
  - Road routes between sites from the public OSRM demo server (4 requests)
  - Elevation: Mapzen terrain tiles on AWS Open Data (SRTM and others, public domain / open)
Writes interface/static/{vendor,map}/, simulation/scenarios/arusha.yaml and
simulation/geo/arusha/ (elevation and building clutter grids). Run one stage with
    uv run python tools/build_arusha.py terrain
"""

import array
import copy
import gzip
import io
import json
import math
import struct
import sys
import tarfile
import urllib.request
import zlib
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
# Noise on each vehicle's rounds (estimates until a partner shares real schedules).
VAN = {
    "jitter_s": 1200,
    "traffic": 0.3,
    "skip_p": 0.05,
    "breakdown_p": 0.02,
    "repair_s": 7200,
    "night": [20, 6],
    "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
}
SCHEDULES = {
    "AMBULANCE": {"jitter_s": 600, "traffic": 0.2, "breakdown_p": 0.01, "repair_s": 3600},
    "VAN_WEST": copy.deepcopy(VAN),  # copies: YAML writes shared objects as anchors
    "VAN_EAST": copy.deepcopy(VAN),
    "OUTREACH_BIKE": {"jitter_s": 900, "traffic": 0.2, "skip_p": 0.1, "night": [19, 7]},
}
# On foot: a community health worker between Murieti and two villages ~2 km away (illustrative).
WALKERS = {
    "HEALTH_WORKER": [SITES["MURIETI"][2], (-3.43300, 36.64600), (-3.40500, 36.64300)],
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
            "schedule": SCHEDULES[vid],
        }
        nodes.append({"id": vid, "role": "mule", "route": route})
    for wid, places in WALKERS.items():
        walk = {"places": [list(p) for p in places], "speed": 1.2, "dwell_s": [1200, 3600]}
        nodes.append({"id": wid, "role": "mule", "walk": walk})
    for rid, (vid, frac) in RELAYS.items():
        at = paths[vid][int(len(paths[vid]) * frac)]
        nodes.append(
            {
                "id": rid,
                "role": "relay",
                "at": list(at),
                "power": {
                    "kind": "solar",
                    "capacity_wh": 77,
                    "panel_w": 10,
                },  # spec §11.1: 6 Ah 12.8 V
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
        "geo": {
            "lat": SITES["HOSPITAL"][2][0],
            "lon": SITES["HOSPITAL"][2][1],
            "terrain": "arusha",
        },
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


TERRAIN_ZOOM = 12  # ~38 m per pixel, about SRTM's native 30 m
CELL_M = 50.0
TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def png_rgb(data: bytes) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB, non-interlaced PNG (what terrarium tiles are) to raw RGB rows."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        (length,), kind = struct.unpack(">I", data[pos : pos + 4]), data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert (depth, colour, interlace) == (8, 2, 0), "expected 8-bit RGB, no interlace"
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw, stride, bpp = zlib.decompress(idat), width * 3, 3
    out, prev = bytearray(), bytearray(stride)
    for r in range(height):
        f, line = (
            raw[r * (stride + 1)],
            bytearray(raw[r * (stride + 1) + 1 : (r + 1) * (stride + 1)]),
        )
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out += line
        prev = line
    return width, height, bytes(out)


def mvt_buildings(tile: bytes) -> list[list[tuple[int, int]]]:
    """Outer rings (tile coordinates, extent 4096) of the 'buildings' layer of one MVT tile."""

    def fields(buf: bytes) -> list[tuple[int, bytes | int]]:
        out: list[tuple[int, bytes | int]] = []
        i = 0
        while i < len(buf):
            key, i = varint(buf, i)
            if key & 7 == 2:
                n, i = varint(buf, i)
                out.append((key >> 3, buf[i : i + n]))
                i += n
            elif key & 7 == 0:
                v, i = varint(buf, i)
                out.append((key >> 3, v))
            else:
                i += {1: 8, 5: 4}[key & 7]
        return out

    rings: list[list[tuple[int, int]]] = []
    for num, layer in fields(tile):
        if num != 3 or not isinstance(layer, bytes):
            continue
        parts = fields(layer)
        if dict((n, v) for n, v in parts if n == 1).get(1) != b"buildings":
            continue
        for n, feature in parts:
            if n != 2 or not isinstance(feature, bytes):
                continue
            geom = next(v for k, v in fields(feature) if k == 4)
            assert isinstance(geom, bytes)
            ints, i = [], 0
            while i < len(geom):
                v, i = varint(geom, i)
                ints.append(v)
            x = y = j = 0
            ring: list[tuple[int, int]] = []
            while j < len(ints):
                cmd, count = ints[j] & 7, ints[j] >> 3
                j += 1
                if cmd == 7:
                    rings.append(ring)
                    ring = []
                    continue
                for _ in range(count):
                    x += (ints[j] >> 1) ^ -(ints[j] & 1)
                    y += (ints[j + 1] >> 1) ^ -(ints[j + 1] & 1)
                    j += 2
                    ring.append((x, y))
    return rings


def varint(buf: bytes, i: int) -> tuple[int, int]:
    v = shift = 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return v, i


def tile_latlon(z: int, x: float, y: float) -> tuple[float, float]:
    n = 2**z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n)))), x / n * 360 - 180


def terrain() -> None:
    """Elevation and building-clutter grids on CELL_M squares around the scenario origin."""
    sys.path.insert(0, str(ROOT))
    from simulation.geo import to_latlon, to_metres

    origin = SITES["HOSPITAL"][2]
    (x0, y0), (x1, y1) = (
        to_metres((BBOX[1], BBOX[0]), origin),
        to_metres((BBOX[3], BBOX[2]), origin),
    )
    cols, rows = int((x1 - x0) // CELL_M), int((y1 - y0) // CELL_M)

    z, n = TERRAIN_ZOOM, 2**TERRAIN_ZOOM
    cache: dict[tuple[int, int], bytes] = {}
    elevation = array.array("h")
    for r in range(rows):
        for c in range(cols):
            lat, lon = to_latlon((x0 + (c + 0.5) * CELL_M, y0 + (r + 0.5) * CELL_M), origin)
            fx = (lon + 180) / 360 * n
            fy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
            key = (int(fx), int(fy))
            if key not in cache:
                cache[key] = png_rgb(get(TERRAIN_URL.format(z=z, x=key[0], y=key[1])))[2]
            px, py = int((fx % 1) * 256), int((fy % 1) * 256)
            red, green, blue = cache[key][(py * 256 + px) * 3 : (py * 256 + px) * 3 + 3]
            elevation.append(round(red * 256 + green + blue / 256 - 32768))

    f = (STATIC / "map/arusha.pmtiles").open("rb")
    reader = Reader(lambda offset, length: (f.seek(offset), f.read(length))[1])
    built = [0.0] * (rows * cols)
    xs, ys = tile_range(MAX_ZOOM)
    buildings = 0
    for tx in xs:
        for ty in ys:
            data = reader.get(MAX_ZOOM, tx, ty)
            if not data:
                continue
            for ring in mvt_buildings(gzip.decompress(data)):
                pts = [
                    to_metres(tile_latlon(MAX_ZOOM, tx + u / 4096, ty + v / 4096), origin)
                    for u, v in ring
                ]
                area = abs(
                    sum(
                        a[0] * b[1] - b[0] * a[1]
                        for a, b in zip(pts, pts[1:] + pts[:1], strict=True)
                    )
                )
                cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                c, r = int((cx - x0) // CELL_M), int((cy - y0) // CELL_M)
                if 0 <= c < cols and 0 <= r < rows:
                    built[r * cols + c] += area / 2
                    buildings += 1
    f.close()
    clutter = bytes(min(255, round(255 * a / CELL_M**2)) for a in built)

    out = ROOT / "simulation/geo/arusha"
    out.mkdir(parents=True, exist_ok=True)
    (out / "elevation.i16.gz").write_bytes(gzip.compress(elevation.tobytes(), mtime=0))
    (out / "clutter.u8.gz").write_bytes(gzip.compress(clutter, mtime=0))
    meta = {
        "origin": list(origin),
        "x0": x0,
        "y0": y0,
        "cell_m": CELL_M,
        "cols": cols,
        "rows": rows,
        "elevation": "elevation.i16.gz: int16 metres, little-endian, row 0 south",
        "clutter": "clutter.u8.gz: building footprint share of each cell, 0-255",
        "sources": {
            "elevation": f"Mapzen terrarium tiles z{z} (AWS Open Data); mostly SRTM 30 m",
            "clutter": "OpenStreetMap building footprints from the Protomaps extract (ODbL)",
        },
    }
    (out / "terrain.json").write_text(json.dumps(meta, indent=2) + "\n")
    low, high = min(elevation), max(elevation)
    size = sum(p.stat().st_size for p in out.iterdir()) / 1e6
    print(
        f"terrain: {cols}x{rows} cells of {CELL_M:.0f} m, elevation {low}-{high} m, "
        f"{len(cache)} elevation tiles, {buildings} buildings, {size:.2f} MB"
    )


if __name__ == "__main__":
    stages = {
        "vendor": vendor,
        "fonts": fonts,
        "tiles": tiles,
        "scenario": scenario,
        "terrain": terrain,
    }
    for name in sys.argv[1:] or list(stages):
        stages[name]()
