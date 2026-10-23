"""The synthetic grid is the oracle: Dijkstra must equal the closed form exactly."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from geonet.network import build_graph
from geonet.synthetic import (
    SYNTHETIC_CRS,
    closed_form_euclid_m,
    closed_form_network_m,
    synthetic_city,
)

N = 12
FACILITIES = ((2, 2), (9, 9))


def _dijkstra_by_cell(city, n: int = N) -> dict[str, float]:
    graph = build_graph(city.nodes, city.edges)
    sources = [i * n + j for i, j in FACILITIES]
    dist = nx.multi_source_dijkstra_path_length(graph, sources, weight="length_m")
    return {f"c{nid // n}_{nid % n}": d for nid, d in dist.items()}


@pytest.mark.parametrize(
    ("river_row", "bridge_col"),
    [(None, None), (5, 0), (5, 7), (0, 11), (9, 4)],
)
def test_dijkstra_equals_closed_form(river_row, bridge_col) -> None:
    city, _ = synthetic_city(
        n=N, facility_nodes=FACILITIES, river_row=river_row, bridge_col=bridge_col
    )
    expected = closed_form_network_m(
        n=N, facility_nodes=FACILITIES, river_row=river_row, bridge_col=bridge_col
    )
    got = _dijkstra_by_cell(city)
    assert set(got) == set(expected.index)
    for cell, want in expected.items():
        assert got[cell] == want, cell  # exact, not approximate


def test_dijkstra_equals_closed_form_other_spacing() -> None:
    city, _ = synthetic_city(
        n=8, spacing_m=75.0, facility_nodes=((1, 6),), river_row=3, bridge_col=2
    )
    expected = closed_form_network_m(
        n=8, spacing_m=75.0, facility_nodes=((1, 6),), river_row=3, bridge_col=2
    )
    graph = build_graph(city.nodes, city.edges)
    dist = nx.single_source_dijkstra_path_length(graph, 1 * 8 + 6, weight="length_m")
    for cell, want in expected.items():
        i, j = (int(p) for p in cell[1:].split("_"))
        assert dist[i * 8 + j] == want


def test_river_lengthens_the_walk() -> None:
    plain = closed_form_network_m(n=N, facility_nodes=FACILITIES)
    river = closed_form_network_m(n=N, facility_nodes=FACILITIES, river_row=5, bridge_col=0)
    assert (river >= plain).all()
    assert (river > plain).any()


def test_closed_form_euclid_matches_straight_line() -> None:
    city, origins = synthetic_city(n=N, facility_nodes=FACILITIES)
    fx = city.facilities[["x", "y"]].to_numpy()
    ox = origins[["x", "y"]].to_numpy()
    direct = np.min(np.hypot(*(ox[:, None, :] - fx[None, :, :]).transpose(2, 0, 1)), axis=1)
    expected = closed_form_euclid_m(n=N, facility_nodes=FACILITIES)
    assert np.allclose(expected.to_numpy(), direct, rtol=0, atol=1e-9)
    # a walk can never be shorter than the straight line
    network = closed_form_network_m(n=N, facility_nodes=FACILITIES)
    assert (network.to_numpy() >= expected.to_numpy() - 1e-9).all()


def test_pop_seed_is_reproducible() -> None:
    _, a = synthetic_city(pop_seed=7)
    _, b = synthetic_city(pop_seed=7)
    _, c = synthetic_city(pop_seed=8)
    assert a["pop"].equals(b["pop"])
    assert not a["pop"].equals(c["pop"])
    assert a["pop"].between(1, 99).all()


def test_default_pop_is_one_per_origin() -> None:
    city, origins = synthetic_city()
    assert (origins["pop"] == 1.0).all()
    assert len(origins) == len(city.nodes)
    assert city.crs == SYNTHETIC_CRS


@pytest.mark.parametrize(
    "kwargs",
    [
        {"river_row": 5},
        {"bridge_col": 3},
        {"river_row": -1, "bridge_col": 3},
        {"river_row": N - 1, "bridge_col": 3},
        {"river_row": 5, "bridge_col": N},
        {"river_row": 5, "bridge_col": -1},
    ],
)
def test_invalid_river_arguments_raise(kwargs) -> None:
    with pytest.raises(ValueError):
        synthetic_city(n=N, **kwargs)
