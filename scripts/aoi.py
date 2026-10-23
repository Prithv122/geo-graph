"""Study area shared by the one-off download scripts (registered in PREREGISTRATION.md)."""

from __future__ import annotations

import math

from pyproj import Transformer

CRS = "EPSG:32643"
CENTRE_LAT = 12.9716
CENTRE_LON = 77.5946
AOI_SIZE_M = 10_000
FETCH_BUFFER_M = 2_500


def to_utm() -> Transformer:
    return Transformer.from_crs("EPSG:4326", CRS, always_xy=True)


def to_lonlat() -> Transformer:
    return Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


def aoi_bounds() -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) in metres: the square centred on the registered point."""
    cx, cy = to_utm().transform(CENTRE_LON, CENTRE_LAT)
    cx, cy = round(cx), round(cy)
    half = AOI_SIZE_M / 2
    return (cx - half, cy - half, cx + half, cy + half)


def fetch_bounds(
    aoi: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = aoi or aoi_bounds()
    b = FETCH_BUFFER_M
    return (xmin - b, ymin - b, xmax + b, ymax + b)


def fetch_bbox_lonlat(
    aoi: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Envelope of the fetch square (corners and edge midpoints) as (left, bottom, right, top)."""
    xmin, ymin, xmax, ymax = fetch_bounds(aoi)
    xm, ym = (xmin + xmax) / 2, (ymin + ymax) / 2
    pts = [
        (xmin, ymin), (xm, ymin), (xmax, ymin), (xmax, ym),
        (xmax, ymax), (xm, ymax), (xmin, ymax), (xmin, ym),
    ]  # fmt: skip
    t = to_lonlat()
    lons, lats = zip(*(t.transform(x, y) for x, y in pts), strict=True)
    # round outwards to 6 decimals (~0.1 m) so the envelope always contains the square
    f = 1e6
    return (
        math.floor(min(lons) * f) / f,
        math.floor(min(lats) * f) / f,
        math.ceil(max(lons) * f) / f,
        math.ceil(max(lats) * f) / f,
    )
