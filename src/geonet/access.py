"""The two access arms and the full comparison.

Both arms share destinations, origins, weights and threshold. Only the distance method
differs: straight line to the nearest facility, or, on the walking network, the straight leg
to the nearest street + the walk along it + the shortest path + the same on the facility's
street. The nearest-node snap is kept as a sensitivity. ``run_access`` returns one
JSON-serialisable dict.
"""

from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from geonet.city import ORIGIN_COLUMNS, City, require_columns
from geonet.network import EdgeSnapper, Snapper, build_graph
from geonet.stats import (
    DEFAULT_ALPHA,
    DEFAULT_SEED,
    cluster_bootstrap_ci,
    gap_pp,
    verdict,
    weighted_quantile,
    weighted_share,
)

FACILITY_KINDS = frozenset({"hospital", "clinic", "doctors"})  # the three registered tags
LENGTH_SLACK_M = 1e-6  # a network distance may undercut the straight line by float noise only

NetworkFn = Callable[[nx.Graph, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class AccessConfig:
    """Walking speed and time budget; together they fix the distance threshold."""

    speed_kmh: float
    minutes: float

    @property
    def threshold_m(self) -> float:
        # Multiply before dividing: 4 km/h x 15 min and 6 km/h x 10 min then both give
        # exactly 1000.0, so a distance sitting on the threshold is classified the same.
        return self.speed_kmh * 1000.0 * self.minutes / 60.0


PRIMARY = AccessConfig(5, 15)
SENSITIVITY_GRID: tuple[AccessConfig, ...] = tuple(
    AccessConfig(s, m) for s in (4, 5, 6) for m in (10, 15, 20)
)


def buffer_distance_m(
    origins_xy: np.ndarray, facilities_xy: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Straight-line distance to the nearest facility, and that facility's row index."""
    fac = np.asarray(facilities_xy, dtype=float).reshape(-1, 2)
    if len(fac) == 0:
        raise ValueError("at least one facility is required")
    dist, idx = cKDTree(fac).query(np.asarray(origins_xy, dtype=float).reshape(-1, 2))
    return np.asarray(dist, dtype=float), np.asarray(idx, dtype=np.int64)


def _labelled_dijkstra(
    graph: nx.Graph, sources: dict[int, tuple[float, int]]
) -> tuple[dict[int, float], dict[int, int]]:
    """Multi-source Dijkstra that remembers which source reached each node first.

    ``sources`` maps node -> (initial distance, label). Equal distances are broken by
    the lower label, so the result does not depend on iteration order.
    """
    dist = {n: d for n, (d, _) in sources.items()}
    label = {n: lab for n, (_, lab) in sources.items()}
    heap = [(d, lab, n) for n, (d, lab) in sources.items()]
    heapq.heapify(heap)
    while heap:
        d, lab, u = heapq.heappop(heap)
        if d > dist[u] or lab != label[u]:
            continue  # stale entry
        for v, data in graph[u].items():
            nd = d + data["length_m"]
            cur = dist.get(v, math.inf)
            if nd < cur or (nd == cur and lab < label[v]):
                dist[v] = nd
                label[v] = lab
                heapq.heappush(heap, (nd, lab, v))
    return dist, label


def _node_snap_distance_m(
    graph: nx.Graph, origins_xy: np.ndarray, facilities_xy: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-node method (the sensitivity): leg to a node + path + leg from a node."""
    fac = np.asarray(facilities_xy, dtype=float).reshape(-1, 2)
    org = np.asarray(origins_xy, dtype=float).reshape(-1, 2)
    snapper = Snapper(graph)
    f_nodes, f_leg = snapper.snap(fac)
    sources: dict[int, tuple[float, int]] = {}
    for k, (node, leg) in enumerate(zip(f_nodes.tolist(), f_leg.tolist(), strict=True)):
        if node not in sources or leg < sources[node][0]:
            sources[node] = (leg, k)
    dist_map, label_map = _labelled_dijkstra(graph, sources)

    o_nodes, o_leg = snapper.snap(org)
    dist = np.full(len(org), np.inf)
    idx = np.full(len(org), -1, dtype=np.int64)
    for i, (node, leg) in enumerate(zip(o_nodes.tolist(), o_leg.tolist(), strict=True)):
        if node in dist_map:
            dist[i] = leg + dist_map[node]
            idx[i] = label_map[node]
    return dist, idx


def _edge_snap_distance_m(
    graph: nx.Graph, origins_xy: np.ndarray, facilities_xy: np.ndarray, snapper: EdgeSnapper
) -> tuple[np.ndarray, np.ndarray]:
    """Registered method: join each point at the nearest street, walk along it to a node."""
    fac = np.asarray(facilities_xy, dtype=float).reshape(-1, 2)
    org = np.asarray(origins_xy, dtype=float).reshape(-1, 2)
    f = snapper.snap(fac)
    o = snapper.snap(org)

    # Both ends of each facility's street are Dijkstra sources; strict "<" keeps the lower
    # facility index when two reach a node at the same distance.
    sources: dict[int, tuple[float, int]] = {}
    on_edge: dict[int, list[int]] = defaultdict(list)
    for k in range(len(fac)):
        on_edge[int(f.edge[k])].append(k)
        for node, extra in ((int(f.u[k]), f.along_u_m[k]), (int(f.v[k]), f.along_v_m[k])):
            init = float(f.leg_m[k] + extra)
            if node not in sources or init < sources[node][0]:
                sources[node] = (init, k)
    dist_map, label_map = _labelled_dijkstra(graph, sources)

    dist = np.full(len(org), np.inf)
    idx = np.full(len(org), -1, dtype=np.int64)
    for i in range(len(org)):
        best = (math.inf, -1)
        leg = float(o.leg_m[i])
        for node, along in ((int(o.u[i]), o.along_u_m[i]), (int(o.v[i]), o.along_v_m[i])):
            if node in dist_map:
                cand = (leg + float(along) + dist_map[node], label_map[node])
                if cand < best:
                    best = cand
        # Origin and facility on one street: walking straight along it may beat any detour.
        for k in on_edge.get(int(o.edge[i]), ()):
            direct = leg + abs(float(o.pos_m[i] - f.pos_m[k])) + float(f.leg_m[k])
            if (direct, k) < best:
                best = (direct, k)
        dist[i], idx[i] = best
    return dist, idx


def network_distance_m(
    graph: nx.Graph,
    origins_xy: np.ndarray,
    facilities_xy: np.ndarray,
    edges: pd.DataFrame | EdgeSnapper | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Walking distance to the nearest facility on the street network.

    With ``edges`` (the registered method; a prebuilt ``EdgeSnapper`` also works) every
    point joins the network at the closest point of the nearest street: distance =
    origin leg + walk along its street to an end node + shortest path + walk along the
    facility's street + facility leg, minimised over both ends on each side. Facilities on
    the origin's own street add the direct walk along it. Ties go to the lower facility index.

    Without ``edges`` points snap to the nearest node (the sensitivity method): leg + path +
    leg. One Dijkstra seeded at every facility's source nodes gives all origins at once.
    Unreachable origins get ``inf`` and index -1. No cutoff: the circuity statistic needs
    distances beyond the threshold.
    """
    fac = np.asarray(facilities_xy, dtype=float).reshape(-1, 2)
    if len(fac) == 0:
        raise ValueError("at least one facility is required")
    if edges is None:
        return _node_snap_distance_m(graph, origins_xy, fac)
    snapper = edges if isinstance(edges, EdgeSnapper) else EdgeSnapper(graph, edges)
    return _edge_snap_distance_m(graph, origins_xy, fac, snapper)


# Amendment 2: population is counted by pixel centre inside the AOI, so a cell on the AOI edge
# has residents inside it while its centroid (the origin point) may sit just outside. A
# resolution-9 cell's centroid is at most about 175 m from any point in the cell.
AOI_ORIGIN_TOLERANCE_M = 250.0


def _validate(city: City, origins: pd.DataFrame) -> pd.DataFrame:
    """Check the inputs and return the origins sorted by cell_id."""
    require_columns(origins, ORIGIN_COLUMNS, "origins")
    if len(origins) == 0:
        raise ValueError("origins is empty")
    if len(city.facilities) == 0:
        raise ValueError("the city has no facilities")
    bad_kinds = sorted(set(city.facilities["kind"].astype(str)) - FACILITY_KINDS)
    if bad_kinds:
        raise ValueError(f"unknown facility kind {bad_kinds}, expected {sorted(FACILITY_KINDS)}")
    if origins["cell_id"].duplicated().any():
        raise ValueError("origins has duplicate cell_id values")
    pop = origins["pop"].to_numpy(dtype=float)
    if not np.all(np.isfinite(pop)) or np.any(pop < 0):
        raise ValueError("origin pop must be finite and non-negative")
    if pop.sum() <= 0:
        raise ValueError("total origin population is zero")
    x = origins["x"].to_numpy(dtype=float)
    y = origins["y"].to_numpy(dtype=float)
    xmin, ymin, xmax, ymax = city.aoi
    tol = AOI_ORIGIN_TOLERANCE_M
    inside = (x >= xmin - tol) & (x <= xmax + tol) & (y >= ymin - tol) & (y <= ymax + tol)
    if not inside.all():
        bad = origins.loc[~inside, "cell_id"].tolist()[:3]
        raise ValueError(
            f"{int((~inside).sum())} origins lie more than {tol:g} m outside the AOI, e.g. {bad}"
        )
    return origins.sort_values("cell_id", kind="stable").reset_index(drop=True)


def _num(x: float) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _native(value):
    return value.item() if isinstance(value, np.generic) else value


def _pair(ci: tuple[float, float]) -> list[float]:
    return [float(ci[0]), float(ci[1])]


def run_access(
    city: City,
    origins: pd.DataFrame,
    configs: Sequence[AccessConfig] = SENSITIVITY_GRID,
    *,
    primary: AccessConfig = PRIMARY,
    network_fn: NetworkFn | None = None,
    n_boot: int = 2000,
    seed: int = DEFAULT_SEED,
    graph: nx.Graph | None = None,
    registered: bool = False,
) -> dict:
    """Compute both arms once, classify at every threshold, and summarise.

    The network arm snaps to the nearest street (``city.edges``). ``network_fn(graph,
    origins_xy, facilities_xy) -> (dist, idx)`` replaces it. Passing the buffer distances
    through it must give a gap of exactly zero, which is how the tests show that only the
    distance method differs. The nearest-node snap is always computed as a sensitivity.

    ``summary["verdict"]`` is the registered rule's outcome only with ``registered=True``
    (the caller has checked the registered settings); otherwise it is None. Raises if any
    network distance is shorter than the straight line.
    """
    cells = _validate(city, origins)
    if graph is None:
        graph = build_graph(city.nodes, city.edges)
    fac = city.facilities.reset_index(drop=True)
    fac_xy = fac[["x", "y"]].to_numpy(dtype=float)
    fids = fac["fid"].astype(str).tolist()
    xy = cells[["x", "y"]].to_numpy(dtype=float)
    pop = cells["pop"].to_numpy(dtype=float)
    cluster = cells["cluster"].to_numpy()
    cell_ids = cells["cell_id"].to_numpy()

    euclid, e_idx = buffer_distance_m(xy, fac_xy)
    edge_snapper = EdgeSnapper(graph, city.edges)
    if network_fn is not None:
        network, n_idx = network_fn(graph, xy, fac_xy)
    else:
        network, n_idx = network_distance_m(graph, xy, fac_xy, edge_snapper)
    network = np.asarray(network, dtype=float)
    n_idx = np.asarray(n_idx, dtype=np.int64)
    short = network < euclid - LENGTH_SLACK_M
    if short.any():
        i = int(np.argmax(short))
        raise ValueError(
            f"{int(short.sum())} network distances are shorter than the straight line, "
            f"e.g. cell {cells['cell_id'].iat[i]}: {network[i]:.3f} m < {euclid[i]:.3f} m"
        )
    node_network, _ = network_distance_m(graph, xy, fac_xy)
    snap = edge_snapper.snap(xy).leg_m

    def classify(threshold: float) -> tuple[np.ndarray, np.ndarray]:
        return euclid <= threshold, network <= threshold  # inf compares False

    def ci(b_ok: np.ndarray, n_ok: np.ndarray, groups: np.ndarray) -> list[float]:
        return _pair(cluster_bootstrap_ci(pop, b_ok, n_ok, groups, n_boot=n_boot, seed=seed))

    sensitivity = []
    for cfg in configs:
        b_ok, n_ok = classify(cfg.threshold_m)
        sensitivity.append(
            {
                "speed_kmh": float(cfg.speed_kmh),
                "minutes": float(cfg.minutes),
                "threshold_m": float(cfg.threshold_m),
                "share_buffer": weighted_share(pop, b_ok),
                "share_network": weighted_share(pop, n_ok),
                "gap_pp": gap_pp(pop, b_ok, n_ok),
                "ci_cluster": ci(b_ok, n_ok, cluster),
            }
        )

    threshold = primary.threshold_m
    b_ok, n_ok = classify(threshold)
    gap = gap_pp(pop, b_ok, n_ok)
    ci_cluster = ci(b_ok, n_ok, cluster)
    ci_cell = ci(b_ok, n_ok, cell_ids)
    node_ok = node_network <= threshold

    usable = (euclid > 0) & np.isfinite(network)
    circuity = np.where(usable, network / np.where(euclid > 0, euclid, 1.0), np.nan)
    affected = pop * (b_ok & ~n_ok)

    summary = {
        "threshold_m": float(threshold),
        "share_buffer": weighted_share(pop, b_ok),
        "share_network": weighted_share(pop, n_ok),
        "gap_pp": gap,
        "ci_cluster": ci_cluster,
        "ci_cell": ci_cell,
        "registered": bool(registered),
        "verdict": verdict(gap, *ci_cluster) if registered else None,
        "snap_method": "edge",
        "node_snap_sensitivity": {
            "share_network": weighted_share(pop, node_ok),
            "gap_pp": gap_pp(pop, b_ok, node_ok),
            "ci_cluster": ci(b_ok, node_ok, cluster),
        },
        "n_cells": len(cells),
        "n_clusters": len(np.unique(cluster)),
        "pop_total": float(pop.sum()),
        "pop_affected": float(affected.sum()),
        "circuity_median": _num(weighted_quantile(circuity, pop, 0.5)),
        "circuity_p90": _num(weighted_quantile(circuity, pop, 0.9)),
        "snap_median_m": _num(weighted_quantile(snap, pop, 0.5)),
        "snap_over_200m_share": weighted_share(pop, snap > 200.0),
        "network_ge_euclid": bool(np.all(network >= euclid - LENGTH_SLACK_M)),
    }

    zones = []
    for key in np.unique(cluster):
        m = cluster == key
        zone_pop = float(pop[m].sum())
        if zone_pop > 0:
            sb, sn = weighted_share(pop[m], b_ok[m]), weighted_share(pop[m], n_ok[m])
            zone_gap = 100.0 * (sb - sn)
        else:
            sb = sn = zone_gap = None
        zones.append(
            {
                "cluster": _native(key),
                "pop": zone_pop,
                "share_buffer": sb,
                "share_network": sn,
                "gap_pp": zone_gap,
                "pop_affected": float(affected[m].sum()),
            }
        )
    zones.sort(
        key=lambda z: (-(z["gap_pp"] if z["gap_pp"] is not None else -math.inf), z["cluster"])
    )

    def fid_of(idx_arr: np.ndarray, i: int) -> str | None:
        return fids[idx_arr[i]] if idx_arr[i] >= 0 else None

    cell_rows = [
        {
            "cell_id": _native(cell_ids[i]),
            "x": float(xy[i, 0]),
            "y": float(xy[i, 1]),
            "pop": float(pop[i]),
            "cluster": _native(cluster[i]),
            "snap_m": float(snap[i]),
            "euclid_m": float(euclid[i]),
            "network_m": _num(network[i]),
            "nearest_fid_euclid": fid_of(e_idx, i),
            "nearest_fid_network": fid_of(n_idx, i),
            "buffer_ok": bool(b_ok[i]),
            "network_ok": bool(n_ok[i]),
        }
        for i in range(len(cells))
    ]

    xmin, ymin, xmax, ymax = city.aoi
    return {
        "config": {
            "crs": city.crs,
            "aoi": [float(xmin), float(ymin), float(xmax), float(ymax)],
            "primary": {
                "speed_kmh": float(primary.speed_kmh),
                "minutes": float(primary.minutes),
                "threshold_m": float(threshold),
            },
            "bootstrap": {"n": int(n_boot), "seed": int(seed), "alpha": DEFAULT_ALPHA},
            "n_nodes": int(graph.number_of_nodes()),
            "n_edges": int(graph.number_of_edges()),
            "n_facilities": len(fac),
        },
        "facilities": [
            {
                "fid": fids[k],
                "kind": str(fac["kind"].iat[k]),
                "x": float(fac_xy[k, 0]),
                "y": float(fac_xy[k, 1]),
            }
            for k in range(len(fac))
        ],
        "cells": cell_rows,
        "summary": summary,
        "sensitivity": sensitivity,
        "zones": zones,
    }


def write_access_json(result: dict, path: str | Path) -> None:
    """Write the result with sorted keys, 2-space indent and a trailing newline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
