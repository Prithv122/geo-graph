from __future__ import annotations

import json
import math

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
from geonet.network import Snapper, build_graph
from geonet.synthetic import closed_form_euclid_m, closed_form_network_m, synthetic_city

N_BOOT = 200
RIVER = {"river_row": 5, "bridge_col": 6}
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
    assert res["summary"]["verdict"] == "null"
    for row in res["sensitivity"]:
        assert row["gap_pp"] == 0.0
        assert row["share_buffer"] == row["share_network"]
    assert all(z["gap_pp"] == 0.0 for z in res["zones"])
    assert res["summary"]["circuity_median"] == 1.0


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
    json.dumps(res, allow_nan=False)


def test_snapping_is_measured_for_off_node_origins() -> None:
    city, origins = _random_city(21)
    res = run_access(city, origins, [PRIMARY], n_boot=N_BOOT)
    assert res["summary"]["snap_median_m"] > 0
    assert (_by_cell(res, "snap_m") > 0).all()


def test_validation_errors() -> None:
    city, origins = synthetic_city()
    outside = origins.copy()
    outside.loc[0, "x"] = -5.0
    with pytest.raises(ValueError, match="outside the AOI"):
        run_access(city, outside, [PRIMARY], n_boot=N_BOOT)
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
