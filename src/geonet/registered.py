"""The registered analysis settings, in one place (PREREGISTRATION.md and Amendment 1).

Anything that differs from these still produces numbers, but is labelled "not the registered
analysis" and carries no verdict. ``scripts/aoi.py`` and ``scripts/fetch_osm.py`` hold the same
study area for the one-off download; ``registered_aoi`` must keep agreeing with them.
"""

from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer

CENTRE_LONLAT = (77.5946, 12.9716)  # (lon, lat) of the Bengaluru study-area centre
CRS = "EPSG:32643"
AOI_SIZE_M = 10_000
FETCH_MARGIN_M = 2_500
SPEED_KMH = 5
MINUTES = 15
N_BOOT = 2000
SEED = 20261023
POP_RESOLUTION = 9
ALLOWED_KINDS = frozenset({"hospital", "clinic", "doctors"})


@lru_cache(maxsize=1)
def registered_aoi() -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) in metres: the square centred on the registered point.

    The centre is projected once and rounded to the metre, so the AOI is the same on every
    machine and a float wobble in pyproj can never move it.
    """
    cx, cy = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(*CENTRE_LONLAT)
    cx, cy = round(cx), round(cy)
    half = AOI_SIZE_M / 2
    return (float(cx - half), float(cy - half), float(cx + half), float(cy + half))


def is_registered_run(
    *,
    synthetic: bool,
    speed_kmh: float,
    minutes: float,
    n_boot: int,
    seed: int,
    extract_problems: list[str],
    pop_resolution: int | None = POP_RESOLUTION,
) -> bool:
    """True only for the registered analysis; any one deviation makes it a sensitivity run.

    ``pop_resolution`` is the H3 resolution of the origin cells: the registered primary run
    uses resolution 9, and the resolution-10 file is a descriptive sensitivity.
    """
    return (
        not synthetic
        and speed_kmh == SPEED_KMH
        and minutes == MINUTES
        and n_boot == N_BOOT
        and seed == SEED
        and pop_resolution == POP_RESOLUTION
        and not extract_problems
    )
