"""Graph construction and snapping on small hand-built networks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import shapely

from geonet.network import EdgeSnapper, Snapper, build_graph
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


# ---- edges may not undercut the straight line -------------------------------------------


def test_edge_shorter_than_its_chord_raises() -> None:
    with pytest.raises(ValueError, match="shorter than the straight line"):
        build_graph(NODES, _edges((1, 2, 99.0)))
    with pytest.raises(ValueError, match="shorter than the straight line"):
        build_graph(NODES, _edges((1, 2, 100.0), (2, 3, 99.9)))


def test_edge_within_float_slack_of_its_chord_is_accepted() -> None:
    graph = build_graph(NODES, _edges((1, 2, 99.96), (2, 3, 100.0)))
    assert graph[1][2]["length_m"] == 99.96


# ---- edge snapping ----------------------------------------------------------------------


def _graph_and_edges(nodes: pd.DataFrame, edges: pd.DataFrame):
    return build_graph(nodes, edges), edges


def _wkt_edges(*rows: tuple[int, int, float, str | None]) -> pd.DataFrame:
    return pd.DataFrame(
        [(u, v, length, "residential", wkt) for u, v, length, wkt in rows],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )


def test_edge_snapper_finds_position_along_a_straight_street() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 400.0, 0.0), (3, 1000.0, 0.0))
    graph, edges = _graph_and_edges(nodes, _edges((1, 2, 400.0), (2, 3, 600.0)))
    snap = EdgeSnapper(graph, edges).snap(np.array([[190.0, 30.0], [700.0, -5.0]]))
    assert snap.edge.tolist() == [0, 1]
    assert snap.u.tolist() == [1, 2] and snap.v.tolist() == [2, 3]
    np.testing.assert_allclose(snap.leg_m, [30.0, 5.0])
    np.testing.assert_allclose(snap.pos_m, [190.0, 300.0])
    np.testing.assert_allclose(snap.along_u_m, snap.pos_m)
    np.testing.assert_allclose(snap.along_v_m, [210.0, 300.0])


def test_edge_snapper_orients_a_reversed_geometry_to_start_at_u() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 1000.0, 0.0))
    edges = _wkt_edges((1, 2, 1000.0, "LINESTRING (1000 0, 0 0)"))  # stored v -> u
    snap = EdgeSnapper(*_graph_and_edges(nodes, edges)).snap(np.array([[250.0, 40.0]]))
    assert snap.u.tolist() == [1] and snap.v.tolist() == [2]
    assert snap.pos_m[0] == pytest.approx(250.0)
    assert snap.along_u_m[0] == pytest.approx(250.0)
    assert snap.along_v_m[0] == pytest.approx(750.0)
    assert snap.leg_m[0] == pytest.approx(40.0)


def test_edge_snapper_orients_a_curved_reversed_geometry() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 100.0, 0.0))
    length = 2 * float(np.hypot(50.0, 50.0))
    edges = _wkt_edges((1, 2, length, "LINESTRING (100 0, 50 50, 0 0)"))
    snap = EdgeSnapper(*_graph_and_edges(nodes, edges)).snap(np.array([[0.0, 0.0], [100.0, 0.0]]))
    np.testing.assert_allclose(snap.pos_m, [0.0, length], atol=1e-9)
    np.testing.assert_allclose(snap.along_v_m, [length, 0.0], atol=1e-9)


def test_position_is_scaled_to_length_m_not_the_geometry_length() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 1000.0, 0.0))
    edges = _wkt_edges((1, 2, 1100.0, "LINESTRING (0 0, 1000 0)"))  # simplified geometry
    snap = EdgeSnapper(*_graph_and_edges(nodes, edges)).snap(np.array([[500.0, 0.0]]))
    assert snap.pos_m[0] == pytest.approx(550.0)
    assert snap.along_u_m[0] + snap.along_v_m[0] == pytest.approx(1100.0)


def test_missing_geometry_falls_back_to_the_straight_segment() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 100.0, 100.0))
    edges = _wkt_edges((1, 2, 150.0, None))
    snap = EdgeSnapper(*_graph_and_edges(nodes, edges)).snap(np.array([[100.0, 0.0]]))
    assert snap.leg_m[0] == pytest.approx(100.0 / np.sqrt(2))
    assert snap.pos_m[0] == pytest.approx(75.0)  # halfway along the chord, scaled to 150 m


def test_equidistant_edges_resolve_to_the_lowest_candidate_index() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 100.0, 0.0), (3, 0.0, 100.0), (4, -100.0, 0.0))
    graph, edges = _graph_and_edges(nodes, _edges((1, 2, 100.0), (1, 3, 100.0), (1, 4, 100.0)))
    snapper = EdgeSnapper(graph, edges)
    snap = snapper.snap(np.array([[0.0, 0.0], [-0.0, 0.0]]))
    assert snap.edge.tolist() == [0, 0]
    flipped = EdgeSnapper(graph, edges.iloc[::-1].reset_index(drop=True))
    assert flipped.snap(np.array([[0.0, 0.0]])).edge.tolist() == [0]  # still the first row it holds


def test_snapper_ignores_edges_outside_the_graph_and_self_loops() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 100.0, 0.0), (10, 0.0, 500.0), (11, 100.0, 500.0))
    edges = _edges((1, 2, 100.0), (10, 11, 100.0), (1, 1, 5.0))
    graph = build_graph(nodes, edges)  # the 10-11 island is cut
    snapper = EdgeSnapper(graph, edges)
    assert len(snapper) == 1
    snap = snapper.snap(np.array([[50.0, 490.0]]))  # nearer the island, but it is not in the graph
    assert snap.edge.tolist() == [0]
    assert snap.leg_m[0] == pytest.approx(490.0)


def test_snapper_matches_brute_force_on_random_points() -> None:
    rng = np.random.default_rng(5)
    pts = rng.random((30, 2)) * 1000.0
    nodes = pd.DataFrame({"node_id": range(30), "x": pts[:, 0], "y": pts[:, 1]})
    rows = [(i, (i + 1) % 30, 5000.0) for i in range(30)] + [
        (i, (i + 7) % 30, 5000.0) for i in range(30)
    ]
    graph, edges = _graph_and_edges(nodes, _edges(*rows))
    snapper = EdgeSnapper(graph, edges)
    q = rng.random((50, 2)) * 1000.0
    snap = snapper.snap(q)
    pq = shapely.points(q)
    for i in range(len(q)):
        d = shapely.distance(snapper.geoms, pq[i])
        assert snap.leg_m[i] == pytest.approx(d.min(), abs=1e-9)
        assert d[snap.edge[i]] == pytest.approx(d.min(), abs=1e-9)
    assert (snap.pos_m >= 0).all() and (snap.pos_m <= 5000.0).all()


def test_zero_length_edge_snaps_to_position_zero() -> None:
    nodes = _nodes((1, 0.0, 0.0), (2, 0.0, 0.0))
    graph, edges = _graph_and_edges(nodes, _edges((1, 2, 0.0)))
    snap = EdgeSnapper(graph, edges).snap(np.array([[3.0, 4.0]]))
    assert snap.pos_m[0] == 0.0 and snap.along_v_m[0] == 0.0
    assert snap.leg_m[0] == pytest.approx(5.0)


def test_edge_snapper_on_empty_graph_or_no_edges_raises() -> None:
    empty = build_graph(NODES.iloc[0:0], _edges())
    with pytest.raises(ValueError, match="empty graph"):
        EdgeSnapper(empty, _edges())
    graph = build_graph(NODES, _edges((1, 2, 100.0)))
    with pytest.raises(ValueError, match="no edge"):
        EdgeSnapper(graph, _edges((1, 1, 5.0)))
