"""A seeded street grid whose walking distances are known in closed form.

Nodes sit on an n x n grid ``spacing_m`` apart; node (i, j) has id ``i * n + j``
and coordinates ``x = j * spacing_m``, ``y = i * spacing_m``. Facilities sit
exactly on nodes, and every node is an origin, so no snapping distance enters
the closed form.

An optional river runs between rows ``river_row`` and ``river_row + 1``: every
vertical edge across it is removed except the one at ``bridge_col``. That is the
case where a straight-line buffer overstates access: a cell just across the
river from a facility is close as the crow flies and far on foot.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from geonet.city import City

SYNTHETIC_CRS = "LOCAL:metres"


def _segment(x1: float, y1: float, x2: float, y2: float) -> str:
    return f"LINESTRING ({x1} {y1}, {x2} {y2})"


def synthetic_city(
    n: int = 12,
    spacing_m: float = 100.0,
    facility_nodes: tuple[tuple[int, int], ...] = ((2, 2), (9, 9)),
    river_row: int | None = None,
    bridge_col: int | None = None,
    block: int = 4,
    pop_seed: int | None = None,
) -> tuple[City, pd.DataFrame]:
    """Return (city, origins) for the grid. Origins carry one row per node.

    ``pop`` is 1.0 per origin, or a seeded integer 1..99 when ``pop_seed`` is
    given. ``cluster`` groups origins into ``block`` x ``block`` squares, the
    stand-in for the H3 parent cell used by the cluster bootstrap.
    """
    if (river_row is None) != (bridge_col is None):
        raise ValueError("river_row and bridge_col go together")
    if river_row is not None and not (0 <= river_row < n - 1 and 0 <= bridge_col < n):
        raise ValueError("river_row must be in [0, n-2] and bridge_col in [0, n-1]")

    def nid(i: int, j: int) -> int:
        return i * n + j

    nodes = pd.DataFrame(
        [(nid(i, j), j * spacing_m, i * spacing_m) for i in range(n) for j in range(n)],
        columns=["node_id", "x", "y"],
    )

    rows = []
    for i in range(n):
        for j in range(n):
            if j + 1 < n:
                rows.append((i, j, i, j + 1))
            if i + 1 < n and not (river_row is not None and i == river_row and j != bridge_col):
                rows.append((i, j, i + 1, j))
    edges = pd.DataFrame(
        [
            (
                nid(i1, j1),
                nid(i2, j2),
                spacing_m,
                "residential",
                _segment(j1 * spacing_m, i1 * spacing_m, j2 * spacing_m, i2 * spacing_m),
            )
            for i1, j1, i2, j2 in rows
        ],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )

    facilities = pd.DataFrame(
        [
            (f"f{k}", "hospital", j * spacing_m, i * spacing_m)
            for k, (i, j) in enumerate(facility_nodes)
        ],
        columns=["fid", "kind", "x", "y"],
    )

    if pop_seed is None:
        pop = np.ones(n * n)
    else:
        pop = np.random.default_rng(pop_seed).integers(1, 100, size=n * n).astype(float)
    origins = pd.DataFrame(
        {
            "cell_id": [f"c{i}_{j}" for i in range(n) for j in range(n)],
            "x": [j * spacing_m for i in range(n) for j in range(n)],
            "y": [i * spacing_m for i in range(n) for j in range(n)],
            "pop": pop,
            "cluster": [f"b{i // block}_{j // block}" for i in range(n) for j in range(n)],
        }
    )

    extent = (n - 1) * spacing_m
    city = City(nodes, edges, facilities, SYNTHETIC_CRS, (0.0, 0.0, extent, extent))
    return city, origins


def closed_form_network_m(
    n: int = 12,
    spacing_m: float = 100.0,
    facility_nodes: tuple[tuple[int, int], ...] = ((2, 2), (9, 9)),
    river_row: int | None = None,
    bridge_col: int | None = None,
) -> pd.Series:
    """Exact walking distance from every node to its nearest facility, by cell_id."""

    def dist(i: int, j: int, fi: int, fj: int) -> float:
        crosses = river_row is not None and ((i <= river_row) != (fi <= river_row))
        if crosses:
            steps = abs(i - fi) + abs(j - bridge_col) + abs(bridge_col - fj)
        else:
            steps = abs(i - fi) + abs(j - fj)
        return steps * spacing_m

    out = {
        f"c{i}_{j}": min(dist(i, j, fi, fj) for fi, fj in facility_nodes)
        for i in range(n)
        for j in range(n)
    }
    return pd.Series(out, name="network_m")


def closed_form_euclid_m(
    n: int = 12,
    spacing_m: float = 100.0,
    facility_nodes: tuple[tuple[int, int], ...] = ((2, 2), (9, 9)),
) -> pd.Series:
    """Exact straight-line distance from every node to its nearest facility."""
    out = {
        f"c{i}_{j}": min(float(np.hypot(i - fi, j - fj)) * spacing_m for fi, fj in facility_nodes)
        for i in range(n)
        for j in range(n)
    }
    return pd.Series(out, name="euclid_m")
