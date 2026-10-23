"""Graph construction and snapping on small hand-built networks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from geonet.network import Snapper, build_graph
from geonet.synthetic import synthetic_city


def _nodes(*rows: tuple[int, float, float]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["node_id", "x", "y"])


def _edges(*rows: tuple[int, int, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [(u, v, length, "residential", None) for u, v, length in rows],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )


NODES = _nodes((1, 0.0, 0.0), (2, 100.0, 0.0), (3, 200.0, 0.0))


def test_parallel_edges_keep_the_minimum_length() -> None:
    graph = build_graph(NODES, _edges((1, 2, 120.0), (2, 1, 100.0), (1, 2, 130.0), (2, 3, 100.0)))
    assert graph[1][2]["length_m"] == 100.0
    assert graph.number_of_edges() == 2


def test_self_loops_are_dropped() -> None:
    graph = build_graph(NODES, _edges((1, 1, 5.0), (1, 2, 100.0), (2, 3, 100.0)))
    assert not graph.has_edge(1, 1)
    assert graph.number_of_edges() == 2


def test_graph_is_undirected() -> None:
    graph = build_graph(NODES, _edges((1, 2, 100.0), (2, 3, 100.0)))
    assert graph.has_edge(2, 1)
    assert not graph.is_directed()


def test_unknown_node_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown node ids"):
        build_graph(NODES, _edges((1, 99, 100.0)))


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_invalid_edge_length_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="invalid length"):
        build_graph(NODES, _edges((1, 2, bad)))


def test_largest_component_drops_an_island() -> None:
    nodes = _nodes(
        (1, 0.0, 0.0),
        (2, 100.0, 0.0),
        (3, 200.0, 0.0),
        (10, 900.0, 900.0),
        (11, 950.0, 900.0),
    )
    edges = _edges((1, 2, 100.0), (2, 3, 100.0), (10, 11, 50.0))
    assert set(build_graph(nodes, edges).nodes) == {1, 2, 3}
    assert set(build_graph(nodes, edges, largest_component=False).nodes) == {1, 2, 3, 10, 11}


def test_isolated_node_is_dropped_by_the_component_cut() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 100.0, 0.0), (5, 500.0, 500.0))
    graph = build_graph(nodes, _edges((1, 2, 100.0)))
    assert set(graph.nodes) == {1, 2}


def test_snapper_returns_nearest_node_and_distance() -> None:
    city, _ = synthetic_city(n=3, spacing_m=100.0)
    graph = build_graph(city.nodes, city.edges)
    snapper = Snapper(graph)

    ids, dist = snapper.snap(np.array([[0.0, 0.0], [200.0, 100.0]]))
    assert ids.tolist() == [0, 5]  # node id = i * 3 + j
    assert dist.tolist() == [0.0, 0.0]

    # off the grid: (130, 40) is 50 m from the node at x=100, y=0 (id 1)
    ids, dist = snapper.snap(np.array([[130.0, 40.0]]))
    assert ids.tolist() == [1]
    assert dist[0] == pytest.approx(50.0)


def test_snapper_accepts_a_single_point() -> None:
    graph = build_graph(NODES, _edges((1, 2, 100.0), (2, 3, 100.0)))
    ids, dist = Snapper(graph).snap(np.array([190.0, 10.0]))
    assert ids.tolist() == [3]
    assert dist[0] == pytest.approx(np.hypot(10.0, 10.0))


def test_snapper_on_empty_graph_raises() -> None:
    graph = build_graph(NODES.iloc[0:0], _edges())
    assert graph.number_of_nodes() == 0
    with pytest.raises(ValueError, match="empty graph"):
        Snapper(graph)
