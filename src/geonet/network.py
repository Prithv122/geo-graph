"""Street graph construction and point snapping, in projected metres."""

from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


def build_graph(
    nodes: pd.DataFrame, edges: pd.DataFrame, *, largest_component: bool = True
) -> nx.Graph:
    """Undirected walking graph with a ``length_m`` weight on every edge.

    Self-loops are dropped and parallel edges keep the shortest length (walking
    is two-way, so direction never matters). With ``largest_component`` the
    graph is cut to its largest connected component, so no origin can snap onto
    an island it cannot leave.
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
    for u, v, length in edges[["u", "v", "length_m"]].itertuples(index=False):
        u, v, length = int(u), int(v), float(length)
        if not math.isfinite(length) or length < 0:
            raise ValueError(f"edge ({u}, {v}) has invalid length {length}")
        if u == v:
            continue
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
