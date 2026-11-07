"""Latitude/longitude <-> local metres around an origin. Equirectangular: accurate to well under
1% over a district (tens of km), which is all the emulator needs."""

import math

M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_EQUATOR = 111_320.0

LatLon = tuple[float, float]


def to_metres(p: LatLon, origin: LatLon) -> tuple[float, float]:
    (lat, lon), (lat0, lon0) = p, origin
    return (
        (lon - lon0) * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0)),
        (lat - lat0) * M_PER_DEG_LAT,
    )


def to_latlon(xy: tuple[float, float], origin: LatLon) -> LatLon:
    (x, y), (lat0, lon0) = xy, origin
    return (
        lat0 + y / M_PER_DEG_LAT,
        lon0 + x / (M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0))),
    )
