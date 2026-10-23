from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict

import networkx as nx
import numpy as np
import pandas as pd
import pytest
from scipy.spatial import Delaunay

from geonet.access import (
    PRIMARY,
    SENSITIVITY_GRID,
    AccessConfig,
    buffer_distance_m,
    network_distance_m,
    run_access,
    write_access_json,
)
from geonet.city import City
from geonet.network import EdgeSnapper, Snapper, build_graph
from geonet.synthetic import closed_form_euclid_m, closed_form_network_m, synthetic_city

N_BOOT = 200
RIVER = {"river_row": 5, "bridge_col": 6}
SUMMARY_KEYS = {
    "threshold_m",
    "share_buffer",
    "share_network",
    "gap_pp",
    "ci_cluster",
    "ci_cell",
    "registered",
    "verdict",
    "snap_method",
    "node_snap_sensitivity",
    "n_cells",
    "n_clusters",
    "pop_total",
    "pop_affected",
    "circuity_median",
    "circuity_p90",
    "snap_median_m",
    "snap_over_200m_share",
    "network_ge_euclid",
}
HALF_KM = AccessConfig(6, 5)  # 500 m exactly


def _by_cell(result: dict, key: str) -> pd.Series:
    return pd.Series({c["cell_id"]: c[key] for c in result["cells"]})


def _hand_gap(euclid: pd.Series, network: pd.Series, pop: pd.Series, d: float) -> float:
    return 100.0 * (pop[euclid <= d].sum() - pop[network <= d].sum()) / pop.sum()


# ---- thresholds -------------------------------------------------------------------------


def test_threshold_and_grid() -> None:
    assert PRIMARY.threshold_m == 1250.0
    assert len(SENSITIVITY_GRID) == 9
    d = sorted({c.threshold_m for c in SENSITIVITY_GRID})
    assert d[0] == pytest.approx(4000 / 60 * 10) and d[-1] == 2000.0
    assert AccessConfig(4, 15).threshold_m == AccessConfig(6, 10).threshold_m == 1000.0


# ---- closed forms on the synthetic grid -------------------------------------------------


@pytest.mark.parametrize("kw", [{}, RIVER], ids=["plain", "river"])
def test_distances_match_closed_forms(kw: dict) -> None:
    city, origins = synthetic_city(**kw)
    res = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)
    pd.testing.assert_series_equal(
        _by_cell(res, "network_m").astype(float),
        closed_form_network_m(**kw).sort_index(),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        _by_cell(res, "euclid_m"), closed_form_euclid_m().sort_index(), check_names=False
    )
    assert res["summary"]["network_ge_euclid"] is True
    assert (_by_cell(res, "snap_m") == 0).all()


def test_river_grid_gap_equals_hand_value() -> None:
    city, origins = synthetic_city(**RIVER)
    res = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)
    pop = origins.set_index("cell_id")["pop"].sort_index()
    expected = _hand_gap(
        closed_form_euclid_m().sort_index(), closed_form_network_m(**RIVER).sort_index(), pop, 500
    )
    s = res["summary"]
    assert s["gap_pp"] == pytest.approx(expected)
    # Pinned from the closed forms: 98 cells within 500 m straight, 78 on foot, of 144.
    assert s["gap_pp"] == pytest.approx(100 * 20 / 144)
    assert s["share_buffer"] == pytest.approx(98 / 144)
    assert s["share_network"] == pytest.approx(78 / 144)
    assert s["pop_affected"] == 20.0
    assert s["gap_pp"] > 0
    assert s["circuity_median"] >= 1.0


def test_plain_grid_gap_is_non_negative() -> None:
    city, origins = synthetic_city()
    res = run_access(city, origins, SENSITIVITY_GRID, primary=HALF_KM, n_boot=N_BOOT)
    assert res["summary"]["gap_pp"] == pytest.approx(100 * 12 / 144)  # 98 vs 86 cells
    assert all(row["gap_pp"] >= 0 for row in res["sensitivity"])


def test_weighted_population_gap() -> None:
    city, origins = synthetic_city(pop_seed=7, **RIVER)
    res = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)
    pop = origins.set_index("cell_id")["pop"].sort_index()
    expected = _hand_gap(
        closed_form_euclid_m().sort_index(), closed_form_network_m(**RIVER).sort_index(), pop, 500
    )
    assert res["summary"]["gap_pp"] == pytest.approx(expected)
    lo, hi = res["summary"]["ci_cluster"]
    assert lo <= hi


# ---- the registered swap test -----------------------------------------------------------


def test_swapping_network_for_buffer_distance_gives_zero_gap_everywhere() -> None:
    city, origins = synthetic_city(pop_seed=3, **RIVER)
    res = run_access(
        city,
        origins,
        SENSITIVITY_GRID,
        network_fn=lambda g, o, f: buffer_distance_m(o, f),
        n_boot=N_BOOT,
    )
    assert res["summary"]["gap_pp"] == 0.0
    assert res["summary"]["share_buffer"] == res["summary"]["share_network"]
    assert res["summary"]["ci_cluster"] == [0.0, 0.0]
    assert res["summary"]["verdict"] is None  # not the registered run
    assert res["summary"]["node_snap_sensitivity"]["gap_pp"] >= 0.0
    for row in res["sensitivity"]:
        assert row["gap_pp"] == 0.0
        assert row["share_buffer"] == row["share_network"]
    assert all(z["gap_pp"] == 0.0 for z in res["zones"])
    assert res["summary"]["circuity_median"] == 1.0
    registered = run_access(
        city,
        origins,
        SENSITIVITY_GRID,
        network_fn=lambda g, o, f: buffer_distance_m(o, f),
        n_boot=N_BOOT,
        registered=True,
    )
    assert registered["summary"]["verdict"] == "null"


# ---- sensitivity grid -------------------------------------------------------------------


def test_equal_thresholds_give_identical_sensitivity_rows() -> None:
    city, origins = synthetic_city(pop_seed=11, **RIVER)
    res = run_access(
        city, origins, [AccessConfig(4, 15), AccessConfig(6, 10)], primary=PRIMARY, n_boot=N_BOOT
    )
    a, b = res["sensitivity"]
    assert (a["speed_kmh"], a["minutes"]) == (4.0, 15.0)
    assert (b["speed_kmh"], b["minutes"]) == (6.0, 10.0)
    for key in ("threshold_m", "share_buffer", "share_network", "gap_pp", "ci_cluster"):
        assert a[key] == b[key]


# ---- random graphs ----------------------------------------------------------------------


def _random_city(
    seed: int, n_nodes: int = 60, n_fac: int = 4, n_org: int = 40, stretch: bool = False
) -> tuple[City, pd.DataFrame]:
    """Delaunay street graph on random points; facilities and origins sit off the nodes."""
    rng = np.random.default_rng(seed)
    pts = rng.random((n_nodes, 2)) * 1000.0
    tri = Delaunay(pts)
    pairs = set()
    for s in tri.simplices:
        for a, b in ((0, 1), (1, 2), (0, 2)):
            u, v = sorted((int(s[a]), int(s[b])))
            pairs.add((u, v))
    edges = []
    for u, v in sorted(pairs):
        length = float(np.hypot(*(pts[u] - pts[v])))
        if stretch:
            length *= 1.0 + rng.random()  # winding street: longer than the straight line
        edges.append((u, v, length, "residential", None))
    nodes = pd.DataFrame({"node_id": range(n_nodes), "x": pts[:, 0], "y": pts[:, 1]})
    edges_df = pd.DataFrame(edges, columns=["u", "v", "length_m", "highway", "geometry"])
    fac_xy = rng.random((n_fac, 2)) * 1000.0
    facilities = pd.DataFrame(
        {
            "fid": [f"f{k}" for k in range(n_fac)],
            "kind": "clinic",
            "x": fac_xy[:, 0],
            "y": fac_xy[:, 1],
        }
    )
    org_xy = rng.random((n_org, 2)) * 1000.0
    origins = pd.DataFrame(
        {
            "cell_id": [f"o{i:03d}" for i in range(n_org)],
            "x": org_xy[:, 0],
            "y": org_xy[:, 1],
            "pop": rng.integers(1, 50, n_org).astype(float),
            "cluster": [f"z{i % 5}" for i in range(n_org)],
        }
    )
    city = City(nodes, edges_df, facilities, "LOCAL:metres", (0.0, 0.0, 1000.0, 1000.0))
    return city, origins


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("stretch", [False, True])
def test_network_never_shorter_than_straight_line(seed: int, stretch: bool) -> None:
    city, origins = _random_city(seed, stretch=stretch)
    res = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    net = _by_cell(res, "network_m").astype(float)
    euc = _by_cell(res, "euclid_m")
    assert (net >= euc - 1e-6).all()
    assert res["summary"]["network_ge_euclid"] is True
    assert res["summary"]["circuity_median"] >= 1.0 - 1e-9
    assert res["summary"]["gap_pp"] >= 0.0


def _dijkstra_reference(graph: nx.Graph, city: City, org_xy: np.ndarray):
    """Network distance via networkx from a virtual super-source joined to facility nodes."""
    snapper = Snapper(graph)
    fac_xy = city.facilities[["x", "y"]].to_numpy()
    f_nodes, f_leg = snapper.snap(fac_xy)
    g = graph.copy()
    legs: dict[int, float] = {}
    for node, leg in zip(f_nodes.tolist(), f_leg.tolist(), strict=True):
        legs[node] = min(leg, legs.get(node, math.inf))
    for node, leg in legs.items():
        g.add_edge("S", node, length_m=leg)
    d = nx.single_source_dijkstra_path_length(g, "S", weight="length_m")
    o_nodes, o_leg = snapper.snap(org_xy)
    ref = np.array([o_leg[i] + d[int(o_nodes[i])] for i in range(len(org_xy))])
    return ref, f_nodes, f_leg, o_nodes, o_leg


@pytest.mark.parametrize("seed", [10, 11, 12])
def test_labelled_dijkstra_matches_networkx(seed: int) -> None:
    city, origins = _random_city(seed, stretch=True)
    graph = build_graph(city.nodes, city.edges)
    org_xy = origins[["x", "y"]].to_numpy()
    fac_xy = city.facilities[["x", "y"]].to_numpy()
    dist, idx = network_distance_m(graph, org_xy, fac_xy)
    ref, f_nodes, f_leg, o_nodes, o_leg = _dijkstra_reference(graph, city, org_xy)
    np.testing.assert_allclose(dist, ref, rtol=0, atol=1e-9)
    assert (idx >= 0).all()

    # The reported facility must itself achieve the minimum.
    per_fac = [
        nx.single_source_dijkstra_path_length(graph, int(n), weight="length_m") for n in f_nodes
    ]
    for i in range(len(org_xy)):
        own = [o_leg[i] + per_fac[k][int(o_nodes[i])] + f_leg[k] for k in range(len(f_nodes))]
        assert own[idx[i]] == pytest.approx(min(own), abs=1e-9)
        assert dist[i] == pytest.approx(min(own), abs=1e-9)


def test_two_facilities_on_one_node_smaller_leg_wins() -> None:
    nodes = pd.DataFrame({"node_id": [0, 1], "x": [0.0, 100.0], "y": [0.0, 0.0]})
    edges = pd.DataFrame(
        [(0, 1, 100.0, "residential", None)], columns=["u", "v", "length_m", "highway", "geometry"]
    )
    graph = build_graph(nodes, edges)
    fac = np.array([[0.0, 30.0], [0.0, 10.0], [0.0, 10.0]])  # legs 30, 10, 10 -> index 1 wins
    dist, idx = network_distance_m(graph, np.array([[100.0, 0.0]]), fac)
    assert dist[0] == pytest.approx(110.0)
    assert idx[0] == 1


def test_unreachable_origin_is_inf_and_minus_one() -> None:
    nodes = pd.DataFrame({"node_id": [0, 1, 2, 3], "x": [0.0, 100, 1000, 1100], "y": [0.0] * 4})
    edges = pd.DataFrame(
        [(0, 1, 100.0, "r", None), (2, 3, 100.0, "r", None)],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )
    graph = build_graph(nodes, edges, largest_component=False)
    dist, idx = network_distance_m(
        graph, np.array([[1050.0, 0.0], [50.0, 0.0]]), np.array([[0.0, 0.0]])
    )
    assert np.isinf(dist[0]) and idx[0] == -1
    assert dist[1] == pytest.approx(50.0) and idx[1] == 0

    facilities = pd.DataFrame({"fid": ["f0"], "kind": ["clinic"], "x": [0.0], "y": [0.0]})
    city = City(nodes, edges, facilities, "LOCAL:metres", (0.0, -10.0, 1200.0, 10.0))
    origins = pd.DataFrame(
        {
            "cell_id": ["a", "b"],
            "x": [1050.0, 50.0],
            "y": [0.0, 0.0],
            "pop": [10.0, 10.0],
            "cluster": ["z", "z"],
        }
    )
    res = run_access(city, origins, [AccessConfig(6, 10)], graph=graph, n_boot=N_BOOT)
    a = next(c for c in res["cells"] if c["cell_id"] == "a")
    assert a["network_m"] is None and a["nearest_fid_network"] is None
    assert a["network_ok"] is False
    json.dumps(res, allow_nan=False)


def test_nearest_fids_reported() -> None:
    city, origins = synthetic_city(**RIVER)
    res = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)
    by_id = {c["cell_id"]: c for c in res["cells"]}
    assert by_id["c0_0"]["nearest_fid_network"] == "f0"
    assert by_id["c11_11"]["nearest_fid_euclid"] == "f1"
    assert by_id["c2_2"]["network_m"] == 0.0


# ---- output shape, validation, determinism ----------------------------------------------


def test_output_shape_and_zones() -> None:
    city, origins = synthetic_city(pop_seed=5, **RIVER)
    res = run_access(city, origins, SENSITIVITY_GRID, primary=HALF_KM, n_boot=N_BOOT)
    assert set(res) == {"config", "facilities", "cells", "summary", "sensitivity", "zones"}
    assert len(res["sensitivity"]) == 9
    assert [c["cell_id"] for c in res["cells"]] == sorted(origins["cell_id"])
    assert res["config"]["n_facilities"] == 2 and res["config"]["n_nodes"] == 144
    assert res["config"]["bootstrap"] == {"n": N_BOOT, "seed": 20261023, "alpha": 0.05}
    s = res["summary"]
    assert s["n_cells"] == 144 and s["n_clusters"] == 9
    assert s["pop_total"] == pytest.approx(origins["pop"].sum())
    gaps = [z["gap_pp"] for z in res["zones"]]
    assert gaps == sorted(gaps, reverse=True)
    assert sum(z["pop_affected"] for z in res["zones"]) == pytest.approx(s["pop_affected"])
    assert sum(z["pop"] for z in res["zones"]) == pytest.approx(s["pop_total"])
    assert 0 <= s["snap_over_200m_share"] <= 1
    assert set(s) == SUMMARY_KEYS
    assert s["snap_method"] == "edge" and s["registered"] is False and s["verdict"] is None
    json.dumps(res, allow_nan=False)


def test_snapping_is_measured_for_off_node_origins() -> None:
    city, origins = _random_city(21)
    res = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    assert res["summary"]["snap_median_m"] > 0
    assert (_by_cell(res, "snap_m") > 0).all()


def test_validation_errors() -> None:
    city, origins = synthetic_city()
    outside = origins.copy()
    outside.loc[0, "x"] = -260.0
    with pytest.raises(ValueError, match="outside the AOI"):
        run_access(city, outside, [PRIMARY], n_boot=N_BOOT)
    # A cell centroid just past the edge (a boundary cell, Amendment 2) is accepted.
    edge_cell = origins.copy()
    edge_cell.loc[0, "x"] = -240.0
    run_access(city, edge_cell, [PRIMARY], n_boot=N_BOOT)
    with pytest.raises(ValueError, match="empty"):
        run_access(city, origins.iloc[0:0], [PRIMARY], n_boot=N_BOOT)
    no_fac = City(city.nodes, city.edges, city.facilities.iloc[0:0], city.crs, city.aoi)
    with pytest.raises(ValueError, match="no facilities"):
        run_access(no_fac, origins, [PRIMARY], n_boot=N_BOOT)
    negative = origins.copy()
    negative.loc[3, "pop"] = -1.0
    with pytest.raises(ValueError, match="non-negative"):
        run_access(city, negative, [PRIMARY], n_boot=N_BOOT)
    dup = pd.concat([origins, origins.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        run_access(city, dup, [PRIMARY], n_boot=N_BOOT)
    with pytest.raises(ValueError, match="missing columns"):
        run_access(city, origins.drop(columns=["cluster"]), [PRIMARY], n_boot=N_BOOT)
    with pytest.raises(ValueError, match="zero"):
        run_access(city, origins.assign(pop=0.0), [PRIMARY], n_boot=N_BOOT)


def test_result_does_not_depend_on_origin_row_order() -> None:
    city, origins = synthetic_city(pop_seed=2, **RIVER)
    shuffled = origins.sample(frac=1.0, random_state=0).reset_index(drop=True)
    a = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    b = run_access(city, shuffled, [PRIMARY], n_boot=N_BOOT)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_two_runs_are_byte_identical(tmp_path) -> None:
    city, origins = synthetic_city(pop_seed=4, **RIVER)
    a = run_access(city, origins, SENSITIVITY_GRID, n_boot=N_BOOT)
    b = run_access(city, origins, SENSITIVITY_GRID, n_boot=N_BOOT)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    p1, p2 = tmp_path / "out" / "a.json", tmp_path / "out" / "b.json"
    write_access_json(a, p1)
    write_access_json(b, p2)
    assert p1.read_bytes() == p2.read_bytes()
    text = p1.read_text(encoding="utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert json.loads(text)["summary"]["n_cells"] == 144


# ---- street snapping (Amendment 1) -------------------------------------------------------


def _street_city(origin_y: float = 0.0):
    """One straight street, nodes at x = 0, 400, 1000, a hospital at its far end."""
    nodes = pd.DataFrame({"node_id": [0, 1, 2], "x": [0.0, 400.0, 1000.0], "y": [0.0] * 3})
    edges = pd.DataFrame(
        [(0, 1, 400.0, "residential", None), (1, 2, 600.0, "residential", None)],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )
    fac = pd.DataFrame({"fid": ["h0"], "kind": ["hospital"], "x": [1000.0], "y": [0.0]})
    city = City(nodes, edges, fac, "LOCAL:metres", (0.0, -100.0, 1000.0, 100.0))
    return city, build_graph(nodes, edges), np.array([[190.0, origin_y]])


def test_edge_method_beats_the_nearest_node_on_a_long_street() -> None:
    city, graph, org = _street_city()
    fac = city.facilities[["x", "y"]].to_numpy()
    edge, e_idx = network_distance_m(graph, org, fac, city.edges)
    node, _ = network_distance_m(graph, org, fac)
    assert edge[0] == 810.0 and e_idx[0] == 0
    assert node[0] == 1190.0


def test_origin_off_the_street_adds_its_leg() -> None:
    city, graph, org = _street_city(origin_y=30.0)
    fac = city.facilities[["x", "y"]].to_numpy()
    edge, _ = network_distance_m(graph, org, fac, city.edges)
    assert edge[0] == pytest.approx(30.0 + 810.0)


def test_prebuilt_snapper_gives_the_same_distances() -> None:
    city, graph, org = _street_city(origin_y=12.0)
    fac = city.facilities[["x", "y"]].to_numpy()
    a = network_distance_m(graph, org, fac, city.edges)
    b = network_distance_m(graph, org, fac, EdgeSnapper(graph, city.edges))
    np.testing.assert_array_equal(a[0], b[0])
    np.testing.assert_array_equal(a[1], b[1])


def _one_long_edge() -> tuple[nx.Graph, pd.DataFrame]:
    nodes = pd.DataFrame({"node_id": [0, 1], "x": [0.0, 900.0], "y": [0.0, 0.0]})
    edges = pd.DataFrame(
        [(0, 1, 900.0, "residential", None)],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )
    return build_graph(nodes, edges), edges


def test_origin_and_facility_on_one_edge_walk_directly() -> None:
    graph, edges = _one_long_edge()
    fac = np.array([[600.0, 0.0]])  # 300 m from node 1
    org = np.array([[300.0, 0.0]])  # 300 m from node 0
    dist, idx = network_distance_m(graph, org, fac, edges)
    assert dist[0] == pytest.approx(300.0) and idx[0] == 0
    # legs add: 20 m off the street on one side, 10 m on the other
    dist, _ = network_distance_m(
        graph, np.array([[300.0, 20.0]]), np.array([[600.0, -10.0]]), edges
    )
    assert dist[0] == pytest.approx(20.0 + 300.0 + 10.0)
    # the facility behind the origin is reached the other way round
    dist, _ = network_distance_m(graph, fac, org, edges)
    assert dist[0] == pytest.approx(300.0)


def test_same_edge_ties_go_to_the_lower_facility_index() -> None:
    graph, edges = _one_long_edge()
    fac = np.array([[600.0, 0.0], [200.0, 0.0], [400.0, 0.0]])  # 100 m either side of x = 300
    dist, idx = network_distance_m(graph, np.array([[300.0, 0.0]]), fac, edges)
    assert dist[0] == pytest.approx(100.0) and idx[0] == 1


def test_facility_on_a_reversed_geometry_edge() -> None:
    nodes = pd.DataFrame({"node_id": [0, 1, 2], "x": [0.0, 1000.0, 1000.0], "y": [0.0, 0.0, 500.0]})
    edges = pd.DataFrame(
        [
            (0, 1, 1000.0, "residential", "LINESTRING (1000 0, 0 0)"),  # stored v -> u
            (1, 2, 500.0, "residential", None),
        ],
        columns=["u", "v", "length_m", "highway", "geometry"],
    )
    graph = build_graph(nodes, edges)
    fac = np.array([[800.0, 0.0]])  # 200 m from node 1, 800 m from node 0
    dist, _ = network_distance_m(graph, np.array([[1000.0, 500.0]]), fac, edges)
    assert dist[0] == pytest.approx(500.0 + 200.0)
    dist, _ = network_distance_m(graph, np.array([[0.0, 0.0]]), fac, edges)
    assert dist[0] == pytest.approx(800.0)


def _curved_city(seed: int, stretch: bool) -> tuple[City, pd.DataFrame]:
    """The random Delaunay city with some edges replaced by 2-segment curves (half reversed)."""
    city, origins = _random_city(seed, stretch=stretch)
    rng = np.random.default_rng(seed + 1000)
    xy = city.nodes.set_index("node_id")[["x", "y"]]
    edges = city.edges.copy()
    for row in rng.choice(len(edges), size=len(edges) // 4, replace=False):
        u, v = int(edges["u"].iat[row]), int(edges["v"].iat[row])
        (x1, y1), (x2, y2) = xy.loc[u].to_numpy(), xy.loc[v].to_numpy()
        mx, my = (x1 + x2) / 2 + rng.normal(0, 40), (y1 + y2) / 2 + rng.normal(0, 40)
        pts = [(x1, y1), (mx, my), (x2, y2)]
        if rng.random() < 0.5:
            pts = pts[::-1]
        edges.loc[edges.index[row], "geometry"] = (
            "LINESTRING (" + ", ".join(f"{float(x)!r} {float(y)!r}" for x, y in pts) + ")"
        )
        edges.loc[edges.index[row], "length_m"] = float(np.hypot(mx - x1, my - y1)) + float(
            np.hypot(x2 - mx, y2 - my)
        )
    return City(city.nodes, edges, city.facilities, city.crs, city.aoi), origins


def _split_edge_reference(snapper: EdgeSnapper, org_xy: np.ndarray, fac_xy: np.ndarray):
    """Brute force: insert the origin and each facility as nodes that split their snapped edge."""
    f, o = snapper.snap(fac_xy), snapper.snap(org_xy)
    dist = np.empty(len(org_xy))
    per_fac = np.empty((len(org_xy), len(fac_xy)))
    for i in range(len(org_xy)):
        on_edge: dict[int, list[tuple[float, object]]] = defaultdict(list)
        for k in range(len(fac_xy)):
            on_edge[int(f.edge[k])].append((float(f.pos_m[k]), ("f", k)))
        on_edge[int(o.edge[i])].append((float(o.pos_m[i]), "o"))
        g = nx.Graph()
        for e in range(len(snapper)):
            chain = [(0.0, int(snapper.u[e]))]
            chain += sorted(on_edge.get(e, []), key=lambda t: t[0])
            chain.append((float(snapper.length_m[e]), int(snapper.v[e])))
            for (p1, a), (p2, b) in itertools.pairwise(chain):
                g.add_edge(a, b, w=p2 - p1)
        d = nx.single_source_dijkstra_path_length(g, "o", weight="w")
        per_fac[i] = [o.leg_m[i] + d[("f", k)] + f.leg_m[k] for k in range(len(fac_xy))]
        dist[i] = per_fac[i].min()
    return dist, per_fac


@pytest.mark.parametrize("seed", [31, 32, 33, 34, 35])
@pytest.mark.parametrize("stretch", [False, True])
def test_edge_method_matches_split_edge_reference(seed: int, stretch: bool) -> None:
    city, origins = _curved_city(seed, stretch)
    graph = build_graph(city.nodes, city.edges)
    snapper = EdgeSnapper(graph, city.edges)
    org = origins[["x", "y"]].to_numpy()
    fac = city.facilities[["x", "y"]].to_numpy()
    dist, idx = network_distance_m(graph, org, fac, snapper)
    ref, per_fac = _split_edge_reference(snapper, org, fac)
    np.testing.assert_allclose(dist, ref, rtol=0, atol=1e-7)
    assert (idx >= 0).all()
    np.testing.assert_allclose(per_fac[np.arange(len(org)), idx], ref, rtol=0, atol=1e-7)
    euclid, _ = buffer_distance_m(org, fac)
    assert (dist >= euclid - 1e-6).all()


@pytest.mark.parametrize("seed", [31, 32, 33])
def test_edge_method_with_many_facilities_sharing_streets(seed: int) -> None:
    city, origins = _curved_city(seed, stretch=False)
    fac = np.random.default_rng(seed).random((40, 2)) * 1000.0
    graph = build_graph(city.nodes, city.edges)
    snapper = EdgeSnapper(graph, city.edges)
    org = origins[["x", "y"]].to_numpy()
    assert len(set(snapper.snap(fac).edge.tolist())) < 40  # some streets hold several
    dist, idx = network_distance_m(graph, org, fac, snapper)
    ref, per_fac = _split_edge_reference(snapper, org, fac)
    np.testing.assert_allclose(dist, ref, rtol=0, atol=1e-7)
    np.testing.assert_allclose(per_fac[np.arange(len(org)), idx], ref, rtol=0, atol=1e-7)


@pytest.mark.parametrize("seed", [41, 42, 43])
def test_run_access_on_curved_streets_keeps_network_above_straight_line(seed: int) -> None:
    city, origins = _curved_city(seed, stretch=True)
    res = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    assert res["summary"]["network_ge_euclid"] is True
    assert (_by_cell(res, "network_m").astype(float) >= _by_cell(res, "euclid_m") - 1e-6).all()
    assert (_by_cell(res, "snap_m") >= 0).all()


# ---- run_access guards, snapping outputs, sensitivity ------------------------------------


def test_network_fn_below_the_straight_line_raises() -> None:
    city, origins = synthetic_city(**RIVER)

    def too_short(graph, org, fac):
        dist, idx = buffer_distance_m(org, fac)
        return dist * 0.5, idx

    with pytest.raises(ValueError, match="shorter than the straight line"):
        run_access(city, origins, [PRIMARY], network_fn=too_short, n_boot=N_BOOT)


def test_unknown_facility_kind_raises() -> None:
    city, origins = synthetic_city()

    def with_kinds(kinds: list[str]) -> City:
        fac = city.facilities.assign(kind=kinds)
        return City(city.nodes, city.edges, fac, city.crs, city.aoi)

    with pytest.raises(ValueError, match="facility kind"):
        run_access(with_kinds(["hospital", "pharmacy"]), origins, [PRIMARY], n_boot=N_BOOT)
    run_access(with_kinds(["clinic", "doctors"]), origins, [PRIMARY], n_boot=N_BOOT)


def test_verdict_only_for_the_registered_run() -> None:
    city, origins = synthetic_city(pop_seed=7, **RIVER)
    plain = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)["summary"]
    reg = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT, registered=True)
    assert plain["registered"] is False and plain["verdict"] is None
    assert reg["summary"]["registered"] is True
    assert reg["summary"]["verdict"] in {"supported", "null", "not_supported"}
    assert reg["summary"]["gap_pp"] == plain["gap_pp"]


def test_node_snap_sensitivity_on_the_river_grid_equals_the_edge_arm() -> None:
    city, origins = synthetic_city(pop_seed=9, **RIVER)
    s = run_access(city, origins, [HALF_KM], primary=HALF_KM, n_boot=N_BOOT)["summary"]
    node = s["node_snap_sensitivity"]
    assert set(node) == {"share_network", "gap_pp", "ci_cluster"}
    assert node["gap_pp"] >= s["gap_pp"]
    # grid points sit on nodes, so both snaps give the same walk
    assert node["share_network"] == s["share_network"]
    assert node["gap_pp"] == s["gap_pp"]
    assert node["ci_cluster"] == s["ci_cluster"]
    assert s["snap_method"] == "edge"


def test_node_snap_sensitivity_exposes_the_snapping_bias() -> None:
    city, _, _ = _street_city()
    origins = pd.DataFrame(
        {
            "cell_id": ["a", "b"],
            "x": [190.0, 1000.0],
            "y": [0.0, 0.0],
            "pop": [1.0, 1.0],
            "cluster": ["z", "z"],
        }
    )
    cfg = AccessConfig(6, 10)  # 1,000 m
    s = run_access(city, origins, [cfg], primary=cfg, n_boot=N_BOOT)["summary"]
    assert s["gap_pp"] == 0.0  # edge method: 810 m and 0 m are both inside 1,000 m
    assert s["node_snap_sensitivity"]["share_network"] == 0.5  # nearest node: 1,190 m is not
    assert s["node_snap_sensitivity"]["gap_pp"] == pytest.approx(50.0)


def test_snap_m_is_the_leg_to_the_nearest_street() -> None:
    city, _, _ = _street_city()
    origins = pd.DataFrame(
        {
            "cell_id": ["a", "b"],
            "x": [190.0, 700.0],
            "y": [30.0, 0.0],
            "pop": [1.0, 3.0],
            "cluster": ["z", "z"],
        }
    )
    res = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    legs = _by_cell(res, "snap_m")
    assert legs["a"] == pytest.approx(30.0) and legs["b"] == 0.0  # not the 192 m to a node
    assert res["summary"]["snap_median_m"] == 0.0  # weighted: the 3-person cell is on the street
    assert res["summary"]["snap_over_200m_share"] == 0.0
