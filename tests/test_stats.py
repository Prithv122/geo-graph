from __future__ import annotations

import math

import numpy as np
import pytest

from geonet.stats import (
    cluster_bootstrap_ci,
    gap_pp,
    verdict,
    weighted_quantile,
    weighted_share,
)


def test_weighted_share_is_population_weighted() -> None:
    pop = [10.0, 30.0, 60.0]
    assert weighted_share(pop, [True, False, True]) == pytest.approx(0.7)
    assert weighted_share(pop, [False, False, False]) == 0.0
    assert weighted_share(pop, [True, True, True]) == 1.0


def test_weighted_share_errors() -> None:
    with pytest.raises(ValueError, match="zero"):
        weighted_share([0.0, 0.0], [True, False])
    with pytest.raises(ValueError, match="length"):
        weighted_share([1.0, 2.0], [True])
    with pytest.raises(ValueError, match="non-negative"):
        weighted_share([1.0, -2.0], [True, True])


def test_gap_pp_is_buffer_minus_network_in_points() -> None:
    pop = [50.0, 50.0]
    assert gap_pp(pop, [True, True], [True, False]) == pytest.approx(50.0)
    assert gap_pp(pop, [True, False], [True, False]) == 0.0
    assert gap_pp(pop, [False, False], [True, True]) == pytest.approx(-100.0)


def _clustered_fixture(n_clusters: int = 40, per: int = 10, seed: int = 3):
    """Whole clusters are either fully reached by the network or not."""
    rng = np.random.default_rng(seed)
    clusters = np.repeat([f"z{k:03d}" for k in range(n_clusters)], per)
    cell_ids = np.array([f"c{i:05d}" for i in range(n_clusters * per)])
    pop = np.ones(n_clusters * per)
    buffer_ok = np.ones(n_clusters * per, dtype=bool)
    cluster_ok = rng.random(n_clusters) < 0.5
    network_ok = np.repeat(cluster_ok, per)
    return pop, buffer_ok, network_ok, clusters, cell_ids


def test_bootstrap_ci_contains_point_estimate() -> None:
    pop, b, n, clusters, _ = _clustered_fixture()
    lo, hi = cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200)
    assert lo <= gap_pp(pop, b, n) <= hi
    assert lo < hi


def test_cell_level_ci_is_narrower_than_cluster_ci_when_outcomes_cluster() -> None:
    pop, b, n, clusters, cell_ids = _clustered_fixture()
    c_lo, c_hi = cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200)
    s_lo, s_hi = cluster_bootstrap_ci(pop, b, n, cell_ids, n_boot=200)
    assert (s_hi - s_lo) < (c_hi - c_lo)


def test_bootstrap_does_not_depend_on_row_order() -> None:
    pop, b, n, clusters, _ = _clustered_fixture()
    pop = pop * np.random.default_rng(1).integers(1, 9, size=pop.size)
    perm = np.random.default_rng(2).permutation(pop.size)
    a = cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200)
    c = cluster_bootstrap_ci(pop[perm], b[perm], n[perm], clusters[perm], n_boot=200)
    assert a == c


def test_bootstrap_is_seeded() -> None:
    pop, b, n, clusters, _ = _clustered_fixture()
    one = cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200, seed=5)
    assert one == cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200, seed=5)
    assert one != cluster_bootstrap_ci(pop, b, n, clusters, n_boot=200, seed=6)


def test_bootstrap_with_no_gap_is_degenerate_at_zero() -> None:
    pop, b, _, clusters, _ = _clustered_fixture()
    assert cluster_bootstrap_ci(pop, b, b, clusters, n_boot=100) == (0.0, 0.0)


def test_bootstrap_errors() -> None:
    with pytest.raises(ValueError, match="length"):
        cluster_bootstrap_ci([1.0, 1.0], [True, True], [True, True], ["a"], n_boot=10)
    with pytest.raises(ValueError, match="zero"):
        cluster_bootstrap_ci([0.0, 0.0], [True, True], [True, True], ["a", "b"], n_boot=10)


def test_verdict_boundaries_are_inclusive_where_registered() -> None:
    assert verdict(5.0, 2.0, 9.0) == "supported"
    assert verdict(4.999, 2.0, 9.0) == "not_supported"
    assert verdict(5.0, 1.999, 9.0) == "not_supported"
    assert verdict(12.0, 3.0, 20.0) == "supported"
    # Null needs the upper bound strictly below 2.
    assert verdict(0.5, -1.0, 1.999) == "null"
    assert verdict(0.5, -1.0, 2.0) == "not_supported"
    assert verdict(3.0, 0.5, 6.0) == "not_supported"


def test_weighted_quantile_basic_and_weights() -> None:
    assert weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.5) == 2.0
    assert weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 1.0) == 4.0
    assert weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.0) == 1.0
    # A heavy weight on the largest value pulls the median up.
    assert weighted_quantile([1, 2, 3], [1, 1, 10], 0.5) == 3.0
    assert weighted_quantile([5, 1, 3], [1, 1, 1], 0.9) == 5.0


def test_weighted_quantile_ignores_non_finite_and_zero_weights() -> None:
    values = [1.0, math.nan, math.inf, 2.0, 100.0]
    weights = [1.0, 50.0, 50.0, 1.0, 0.0]
    assert weighted_quantile(values, weights, 1.0) == 2.0
    assert weighted_quantile([0.0, 7.0], [0.0, 1.0], 0.0) == 7.0
    assert math.isnan(weighted_quantile([math.nan, math.inf], [1.0, 1.0], 0.5))


def test_weighted_quantile_errors() -> None:
    with pytest.raises(ValueError):
        weighted_quantile([1.0], [1.0], 1.5)
    with pytest.raises(ValueError):
        weighted_quantile([1.0, 2.0], [1.0], 0.5)
