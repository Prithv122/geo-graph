"""Shares, the buffer-minus-network gap, its bootstrap interval and the registered verdict."""

from __future__ import annotations

import numpy as np

DEFAULT_SEED = 20261023
DEFAULT_ALPHA = 0.05

# The registered decision rule (PREREGISTRATION.md), in percentage points.
SUPPORTED_MIN_GAP_PP = 5.0
SUPPORTED_MIN_LOWER_PP = 2.0
NULL_MAX_UPPER_PP = 2.0


def _as_arrays(pop, *flags) -> tuple[np.ndarray, ...]:
    """Validate that population and every flag array share one length, and that pop >= 0."""
    pop_a = np.asarray(pop, dtype=float)
    if pop_a.ndim != 1:
        raise ValueError("pop must be one-dimensional")
    out = [pop_a]
    for f in flags:
        f_a = np.asarray(f, dtype=bool)
        if f_a.shape != pop_a.shape:
            raise ValueError(f"length mismatch: pop has {pop_a.shape[0]}, got {f_a.shape[0]}")
        out.append(f_a)
    if not np.all(np.isfinite(pop_a)) or np.any(pop_a < 0):
        raise ValueError("pop must be finite and non-negative")
    return tuple(out)


def weighted_share(pop, ok) -> float:
    """Population-weighted fraction of residents whose cell has ``ok`` set."""
    pop_a, ok_a = _as_arrays(pop, ok)
    total = pop_a.sum()
    if total <= 0:
        raise ValueError("total population is zero, a share is undefined")
    return float(pop_a[ok_a].sum() / total)


def gap_pp(pop, buffer_ok, network_ok) -> float:
    """100 x (share_buffer - share_network): how many points the straight line overstates."""
    return 100.0 * (weighted_share(pop, buffer_ok) - weighted_share(pop, network_ok))


def cluster_bootstrap_ci(
    pop,
    buffer_ok,
    network_ok,
    clusters,
    n_boot: int = 2000,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> tuple[float, float]:
    """Percentile CI of the gap, resampling whole clusters with replacement.

    Neighbouring cells share streets and facilities, so resampling single cells would
    understate the uncertainty; pass ``clusters = cell ids`` for the cell-level version.
    Clusters are taken in sorted id order, so the result does not depend on row order.
    """
    pop_a, buf_a, net_a = _as_arrays(pop, buffer_ok, network_ok)
    cl = np.asarray(clusters)
    if cl.shape != pop_a.shape:
        raise ValueError(f"length mismatch: pop has {pop_a.shape[0]}, clusters {cl.shape[0]}")
    if pop_a.sum() <= 0:
        raise ValueError("total population is zero, a share is undefined")
    if n_boot < 1 or not 0 < alpha < 1:
        raise ValueError("need n_boot >= 1 and 0 < alpha < 1")

    _, inverse = np.unique(cl, return_inverse=True)
    k = int(inverse.max()) + 1
    sums = np.column_stack(
        [
            np.bincount(inverse, weights=w, minlength=k)
            for w in (pop_a, pop_a * buf_a, pop_a * net_a)
        ]
    )
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(k, np.full(k, 1.0 / k), size=n_boot)
    tot = counts @ sums  # (n_boot, 3): replicate population, buffer-ok and network-ok pop
    valid = tot[:, 0] > 0  # a replicate drawing only empty clusters has no share
    if not valid.any():
        raise ValueError("every bootstrap replicate has zero population")
    gaps = 100.0 * (tot[valid, 1] - tot[valid, 2]) / tot[valid, 0]
    lo, hi = np.percentile(gaps, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def verdict(gap: float, lo: float, hi: float) -> str:
    """The registered rule: supported / null / not_supported (boundaries are inclusive)."""
    if gap >= SUPPORTED_MIN_GAP_PP and lo >= SUPPORTED_MIN_LOWER_PP:
        return "supported"
    if hi < NULL_MAX_UPPER_PP:
        return "null"
    return "not_supported"


def weighted_quantile(values, weights, q: float) -> float:
    """Lower weighted quantile: the smallest value whose cumulative weight reaches q.

    NaN and infinite values are dropped together with their weights. Returns NaN when
    nothing usable is left.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if v.shape != w.shape or v.ndim != 1:
        raise ValueError("values and weights must be one-dimensional and the same length")
    if np.any(w < 0) or not np.all(np.isfinite(w)):
        raise ValueError("weights must be finite and non-negative")
    keep = np.isfinite(v)
    v, w = v[keep], w[keep]
    if v.size == 0 or w.sum() <= 0:
        return float("nan")
    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]
    cum = np.cumsum(w)
    target = q * cum[-1]
    # Float cumsum can land a hair under the total; clamp so q=1 returns the maximum.
    idx = min(int(np.searchsorted(cum, target, side="left")), v.size - 1)
    # Skip leading zero-weight values when q=0.
    while idx < v.size - 1 and w[idx] == 0 and cum[idx] <= target:
        idx += 1
    return float(v[idx])
