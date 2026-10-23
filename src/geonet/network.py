"""Street graph construction and point snapping, in projected metres."""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

# Registered rule: edge lengths come from projected geometry, so an edge may not be shorter
# than the straight line between its end nodes. This much slack covers float rounding only.
CHORD_TOLERANCE_M = 0.05


def build_graph(
    nodes: pd.DataFrame, edges: pd.DataFrame, *, largest_component: bool = True
) -> nx.Graph:
    """Undirected walking graph with a ``length_m`` weight on every edge.

    Self-loops are dropped and parallel edges keep the shortest length (walking
    is two-way, so direction never matters). With ``largest_component`` the
    graph is cut to its largest connected component, so no origin can snap onto
    an island it cannot leave. An edge shorter than the straight line between its
    end nodes (by more than 0.05 m) is rejected: it would let a walk beat the crow.
    """
    known = set(nodes["node_id"].astype(int))
    referenced = set(edges["u"].astype(int)) | set(edges["v"].astype(int))
    unknown = referenced - known
    if unknown:
        raise ValueError(f"edges reference {len(unknown)} unknown node ids, e.g. {min(unknown)}")

    graph = nx.Graph()
    graph.add_nodes_from(
        (int(n), {"x": float(x), "y": float(y)})
        for n, x, y in nodes[["node_id", "x", "y"]].itertuples(index=False)
    )
    coords = graph.nodes
    for u, v, length in edges[["u", "v", "length_m"]].itertuples(index=False):
        u, v, length = int(u), int(v), float(length)
        if not math.isfinite(length) or length < 0:
            raise ValueError(f"edge ({u}, {v}) has invalid length {length}")
        if u == v:
            continue
        chord = math.hypot(coords[u]["x"] - coords[v]["x"], coords[u]["y"] - coords[v]["y"])
        if length < chord - CHORD_TOLERANCE_M:
            raise ValueError(
                f"edge ({u}, {v}) length {length:.3f} m is shorter than the straight line "
                f"between its nodes ({chord:.3f} m)"
            )
        if graph.has_edge(u, v) and graph[u][v]["length_m"] <= length:
            continue
        graph.add_edge(u, v, length_m=length)

    if largest_component and graph.number_of_nodes():
        keep = max(nx.connected_components(graph), key=len)
        graph = graph.subgraph(keep).copy()
    return graph


class Snapper:
    """Nearest graph node for arbitrary points (straight-line distance)."""

    def __init__(self, graph: nx.Graph) -> None:
        if graph.number_of_nodes() == 0:
            raise ValueError("cannot snap to an empty graph")
        self.node_ids = np.array(sorted(graph.nodes), dtype=np.int64)
        xy = np.array([(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in self.node_ids])
        self._tree = cKDTree(xy)

    def snap(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (node_ids, distance_m) for an (n, 2) array of points."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        dist, idx = self._tree.query(xy)
        return self.node_ids[idx], np.asarray(dist, dtype=float)


@dataclass(frozen=True)
class EdgeSnap:
    """Where each point joins the street network (all arrays have one entry per point).

    ``edge`` indexes the snapper's candidate edges. ``leg_m`` is the straight leg to the
    street; ``along_u_m`` / ``along_v_m`` the walk along it to either end node, measured in
    the edge's ``length_m``; ``pos_m`` is the distance from node ``u`` (equal to ``along_u_m``).
    """

    edge: np.ndarray
    u: np.ndarray
    v: np.ndarray
    leg_m: np.ndarray
    along_u_m: np.ndarray
    along_v_m: np.ndarray
    pos_m: np.ndarray


class EdgeSnapper:
    """Nearest street (edge) for arbitrary points, with the position along it.

    Snapping to the nearest node instead lengthens routes when the nearest node lies behind
    the resident (Amendment 1), so points join the network at the closest point of the
    closest edge. Candidates are the edges of ``edges`` whose end nodes are both in ``graph``
    (the largest component), self-loops excluded. Each geometry is oriented to start at ``u``.
    The position is scaled to the edge's ``length_m``, so along-edge walks stay consistent
    with the graph weights even if a stored geometry was simplified.
    """

    def __init__(self, graph: nx.Graph, edges: pd.DataFrame) -> None:
        if graph.number_of_nodes() == 0:
            raise ValueError("cannot snap to an empty graph")
        ids = np.array(sorted(graph.nodes), dtype=np.int64)
        xs = np.array([graph.nodes[n]["x"] for n in ids], dtype=float)
        ys = np.array([graph.nodes[n]["y"] for n in ids], dtype=float)

        u_all = edges["u"].to_numpy(dtype=np.int64)
        v_all = edges["v"].to_numpy(dtype=np.int64)
        keep = np.isin(u_all, ids) & np.isin(v_all, ids) & (u_all != v_all)
        if not keep.any():
            raise ValueError("no edge of the network has both end nodes in the graph")
        u, v = u_all[keep], v_all[keep]
        length = edges["length_m"].to_numpy(dtype=float)[keep]
        iu, iv = np.searchsorted(ids, u), np.searchsorted(ids, v)
        xu, yu, xv, yv = xs[iu], ys[iu], xs[iv], ys[iv]

        straight = shapely.linestrings(
            np.stack([np.column_stack([xu, yu]), np.column_stack([xv, yv])], axis=1)
        )
        geoms = straight.copy()
        if "geometry" in edges.columns:
            raw = edges["geometry"].to_numpy(dtype=object)[keep]
            wkt = np.array(
                [w if isinstance(w, str) and w.strip() else None for w in raw], dtype=object
            )
            parsed = shapely.from_wkt(wkt)
            has = ~shapely.is_missing(parsed)
            if has.any() and not np.all(shapely.get_type_id(parsed[has]) == 1):
                raise ValueError("edge geometries must be LineStrings")
            has &= ~shapely.is_empty(parsed)
            geoms[has] = parsed[has]

        # Orient every geometry to start at u (its first point nearer to u than to v).
        start = shapely.get_point(geoms, 0)
        sx, sy = shapely.get_x(start), shapely.get_y(start)
        flip = np.hypot(sx - xv, sy - yv) < np.hypot(sx - xu, sy - yu)
        geoms[flip] = shapely.reverse(geoms[flip])

        self.u, self.v, self.length_m = u, v, length
        self.geoms = geoms
        self._geom_len = shapely.length(geoms)
        self._tree = shapely.STRtree(geoms)

    def __len__(self) -> int:
        return len(self.geoms)

    def snap(self, xy: np.ndarray) -> EdgeSnap:
        """Snap an (n, 2) array of points; ties between equidistant edges go to the lowest index."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        pts = shapely.points(xy)
        (inp, cand), dist = self._tree.query_nearest(pts, return_distance=True, all_matches=True)
        order = np.lexsort((cand, inp))
        inp, cand, dist = inp[order], cand[order], dist[order]
        first = np.ones(len(inp), dtype=bool)
        first[1:] = inp[1:] != inp[:-1]
        inp, cand, dist = inp[first], cand[first], dist[first]
        if len(inp) != len(xy):
            raise ValueError("every point to snap needs finite coordinates")

        edge = cand.astype(np.int64)
        glen = self._geom_len[edge]
        proj = shapely.line_locate_point(self.geoms[edge], pts)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(glen > 0, proj / np.where(glen > 0, glen, 1.0), 0.0)
        length = self.length_m[edge]
        pos = np.clip(frac, 0.0, 1.0) * length
        return EdgeSnap(
            edge=edge,
            u=self.u[edge],
            v=self.v[edge],
            leg_m=np.asarray(dist, dtype=float),
            along_u_m=pos,
            along_v_m=length - pos,
            pos_m=pos,
        )
