"""Turn ``results/access.json`` into tables, maps and the owner's spot-check sheet.

Every number printed here is read from the access dict. Nothing is recomputed that
the access run already decided (shares, gap, CI, verdict), so the report cannot drift
from the result it describes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import h3
import numpy as np
import pandas as pd
from pyproj import Transformer

from geonet import registered

Access = dict[str, Any]

LOCAL_CRS = "LOCAL:metres"
SPOT_CHECK_SEED = registered.SEED
SPOT_CHECK_N = 20
SPOT_CHECK_TOLERANCE = 0.15
# Amendment 1: a row also passes when the two distances differ by at most 100 m, because the
# outside router joins the street differently, which matters on short routes.
SPOT_CHECK_TOLERANCE_M = 100.0
# Registered rule: at least 16 of 20 rows pass (80%); other sample sizes scale it up.
SPOT_CHECK_PASS_SHARE = 0.8
NOT_VALIDATED = "not validated by the spot-check"
NOT_REGISTERED_WORDS = "not the registered analysis (no verdict)"
NOT_REGISTERED_NOTE = (
    "The registered analysis is 5 km/h for 15 min, 2,000 resamples, seed 20261023, the "
    "registered study area and population resolution, and an extract fetched with a margin of "
    "at least 2,500 m; the numbers below are descriptive and carry no verdict."
)
NA = "n/a"
ROUTER_URL = (
    "https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot"
    "&route={lat1:.6f}%2C{lon1:.6f}%3B{lat2:.6f}%2C{lon2:.6f}"
)
VERDICT_WORDS = {
    "supported": "H1 supported",
    "null": "registered null",
    "not_supported": "H1 not supported",
}
# Okabe-Ito picks: distinguishable under the common forms of colour blindness.
STATUS_COLOURS = {
    "both reach": "#009E73",
    "buffer only": "#D55E00",
    "neither": "#9A9A9A",
    "network only": "#CC79A7",
}
MAPS_HINT = (
    "matplotlib is not installed; install the 'maps' extra (for example "
    "`uv sync --extra maps`) to draw figures."
)


# --------------------------------------------------------------------------- loading


def load_access(path: str | Path) -> Access:
    """Read the access result written by ``access.run_access``."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------ formatting


def _pct(share: float | None) -> str:
    return NA if share is None else f"{100 * share:.2f}%"


def _pp(value: float | None) -> str:
    return NA if value is None else f"{value:.2f} pp"


def _bracket(lo: float | None, hi: float | None) -> str:
    return NA if lo is None or hi is None else f"[{lo:.2f}, {hi:.2f}]"


def _ci(lo: float | None, hi: float | None) -> str:
    return NA if lo is None or hi is None else f"{_bracket(lo, hi)} pp"


def _ratio(value: float | None) -> str:
    return NA if value is None else f"{value:.2f}"


def _people(value: float) -> str:
    return f"{round(value):,}"


def _metres(value: float | None) -> str:
    return NA if value is None else f"{round(value):,} m"


def verdict_words(verdict: str | None) -> str:
    """The registered verdict as a phrase; unknown codes are an error, not a guess.

    ``None`` means the run was not the registered analysis, which carries no verdict.
    """
    if verdict is None:
        return NOT_REGISTERED_WORDS
    try:
        return VERDICT_WORDS[verdict]
    except KeyError:
        raise ValueError(
            f"unknown verdict {verdict!r}; expected one of {list(VERDICT_WORDS)}"
        ) from None


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _confidence_label(access: Access) -> str:
    alpha = access.get("config", {}).get("bootstrap", {}).get("alpha")
    return f"{round(100 * (1 - alpha))}%" if alpha is not None else "95%"


# ---------------------------------------------------------------------------- tables


def summary_table(access: Access) -> str:
    """The headline numbers: both shares, the gap with both CIs, the verdict."""
    s = access["summary"]
    cfg = access.get("config", {})
    conf = _confidence_label(access)
    primary = cfg.get("primary")
    threshold = _metres(s["threshold_m"])
    if primary:
        threshold += f" ({primary['speed_kmh']:g} km/h for {primary['minutes']:g} min)"
    affected_share = s["pop_affected"] / s["pop_total"] if s["pop_total"] else None
    boot = cfg.get("bootstrap", {})
    resamples = (
        f"{boot['n']:,} resamples, seed {boot['seed']}" if "n" in boot and "seed" in boot else NA
    )
    node = s.get("node_snap_sensitivity")
    node_text = (
        f"gap {_pp(node['gap_pp'])} {_bracket(*node['ci_cluster'])}, "
        f"network share {_pct(node['share_network'])}"
        if node
        else NA
    )
    rows = [
        ["Walking threshold", threshold],
        ["Residents with access, straight-line buffer", _pct(s["share_buffer"])],
        ["Residents with access, walking network", _pct(s["share_network"])],
        ["Gap (buffer minus network)", _pp(s["gap_pp"])],
        [f"{conf} CI, cluster bootstrap", _ci(*s["ci_cluster"])],
        [f"{conf} CI, cell bootstrap (sensitivity)", _ci(*s["ci_cell"])],
        ["Bootstrap", resamples],
        ["Snapping method", s.get("snap_method", NA)],
        ["Nearest-node snapping, superseded by Amendment 1 (sensitivity)", node_text],
        ["Verdict", verdict_words(s.get("verdict"))],
        [
            "Residents the buffer counts as served but the network does not",
            f"{_people(s['pop_affected'])} of {_people(s['pop_total'])} ({_pct(affected_share)})",
        ],
        [
            "Circuity (network / straight line), median and 90th percentile",
            f"{_ratio(s.get('circuity_median'))} and {_ratio(s.get('circuity_p90'))}",
        ],
        ["Snapping distance, median", _metres(s.get("snap_median_m"))],
        ["Residents snapped more than 200 m", _pct(s.get("snap_over_200m_share"))],
        [
            "Network distance at least the straight line in every cell",
            "yes" if s["network_ge_euclid"] else "no",
        ],
        ["Origins (cells) and zones", f"{s['n_cells']:,} cells in {s['n_clusters']:,} zones"],
    ]
    return _table(["Measure", "Value"], rows)


def sensitivity_table(access: Access) -> str:
    """Speed (rows) by minutes (columns); each cell is the gap, its CI and the distance D."""
    rows_in = access["sensitivity"]
    speeds = sorted({r["speed_kmh"] for r in rows_in})
    minutes = sorted({r["minutes"] for r in rows_in})
    by_key = {(r["speed_kmh"], r["minutes"]): r for r in rows_in}
    primary = access.get("config", {}).get("primary", {})
    primary_key = (primary.get("speed_kmh"), primary.get("minutes"))

    body = []
    for sp in speeds:
        cells = [f"{sp:g} km/h"]
        for mn in minutes:
            r = by_key.get((sp, mn))
            if r is None:
                cells.append("-")
                continue
            lo, hi = r["ci_cluster"]
            text = f"{_pp(r['gap_pp'])} {_bracket(lo, hi)}, {_metres(r['threshold_m'])}"
            cells.append(f"**{text}**" if (sp, mn) == primary_key else text)
        body.append(cells)
    return _table(["Speed \\ Minutes"] + [f"{mn:g} min" for mn in minutes], body)


def _zone_rows(zones: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            f"`{z['cluster']}`",
            _people(z["pop"]),
            _pct(z["share_buffer"]),
            _pct(z["share_network"]),
            _pp(z["gap_pp"]),
            _people(z["pop_affected"]),
        ]
        for z in zones
    ]


def _gap_key(z: dict[str, Any]) -> float:
    """Sort key for the gap: a zone without population has no gap and sorts last."""
    return -z["gap_pp"] if z["gap_pp"] is not None else math.inf


def zones_table(access: Access, top: int = 10) -> str:
    """Top zones by gap, then top zones by residents affected (two markdown tables)."""
    zones = access["zones"]
    header = ["Zone", "Residents", "Buffer", "Network", "Gap", "Residents affected"]
    by_gap = sorted(zones, key=lambda z: (_gap_key(z), str(z["cluster"])))[:top]
    by_pop = sorted(zones, key=lambda z: (-z["pop_affected"], str(z["cluster"])))[:top]
    return (
        f"**Top {len(by_gap)} zones by gap**\n\n{_table(header, _zone_rows(by_gap))}\n\n"
        f"**Top {len(by_pop)} zones by residents affected**\n\n{_table(header, _zone_rows(by_pop))}"
    )


# ---------------------------------------------------------------------- spot checks


def _lonlat_transformer(crs: str) -> Transformer:
    """Projected metres to lon/lat. The synthetic CRS is not on Earth, so it has none."""
    if crs == LOCAL_CRS:
        raise ValueError(
            f"crs {crs!r} is the synthetic grid and has no lon/lat; "
            "a spot-check sheet needs a real projected CRS"
        )
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def spot_check_sheet(
    access: Access, n: int = SPOT_CHECK_N, seed: int = SPOT_CHECK_SEED
) -> pd.DataFrame:
    """A seeded uniform sample of cells for the owner to compare with an outside router.

    Only cells with a network distance and a facility can be checked, so unreachable
    cells are not drawn. The draw runs over cells sorted by ``cell_id`` so the same
    result file and seed always give the same sheet.
    """
    to_lonlat = _lonlat_transformer(access["config"]["crs"])
    facilities = {f["fid"]: f for f in access["facilities"]}
    eligible = sorted(
        (
            c
            for c in access["cells"]
            if c.get("network_m") is not None and c.get("nearest_fid_network") in facilities
        ),
        key=lambda c: c["cell_id"],
    )
    k = min(n, len(eligible))
    picks = np.random.default_rng(seed).choice(len(eligible), size=k, replace=False)

    rows = []
    for i in sorted(int(p) for p in picks):
        c = eligible[i]
        f = facilities[c["nearest_fid_network"]]
        lon1, lat1 = to_lonlat.transform(c["x"], c["y"])
        lon2, lat2 = to_lonlat.transform(f["x"], f["y"])
        rows.append(
            {
                "cell_id": c["cell_id"],
                "origin_lat": round(lat1, 6),
                "origin_lon": round(lon1, 6),
                "facility_fid": f["fid"],
                "facility_lat": round(lat2, 6),
                "facility_lon": round(lon2, 6),
                "network_m": round(c["network_m"], 1),
                "euclid_m": round(c["euclid_m"], 1),
                "router_url": ROUTER_URL.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2),
                "router_m": None,
                "within_15pct": None,
            }
        )
    columns = [
        "cell_id", "origin_lat", "origin_lon", "facility_fid", "facility_lat", "facility_lon",
        "network_m", "euclid_m", "router_url", "router_m", "within_15pct",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)


def require_registered(access: Access) -> None:
    """Refuse anything but a registered access result: the sheet validates the verdict."""
    if access.get("summary", {}).get("registered") is not True:
        raise ValueError(
            "the spot-check applies to the registered analysis only; this access file is "
            "not the registered analysis (no verdict to validate)"
        )


def _required(n: int) -> int:
    """Rows that must pass: 16 of 20, and the same 80% rounded up for other sheet sizes."""
    return math.ceil(SPOT_CHECK_PASS_SHARE * n - 1e-9)


def _spot_check_markdown(sheet: pd.DataFrame, csv_name: str) -> str:
    n = len(sheet)
    need = _required(n)
    lines = [
        "# Spot-check sheet",
        "",
        f"{n} cells drawn at random (fixed seed) from the registered access result. For each row:",
        "",
        "1. Open the link. It asks the openstreetmap.org foot router for the walk from the "
        "cell centroid to the facility the network analysis chose.",
        "2. Read the walking distance the router shows, in metres, and type it into "
        f"`router_m` in `{csv_name}`. Leave `within_15pct` blank; scoring fills it in.",
        "3. Do not adjust the link's endpoints: the point of the check is that the router "
        "never sees the analysis network.",
        "",
        "Pass rule (pre-registered, Amendment 1): a row passes when our network distance and "
        f"the router's differ by at most {round(100 * SPOT_CHECK_TOLERANCE)}% **or** at most "
        f"{round(SPOT_CHECK_TOLERANCE_M)} m (the router joins the street differently, which "
        f"matters on short routes). The facility is the one the network analysis chose. "
        f"The sheet passes with at least {need} of {n} rows.",
        "",
        f"If fewer than {need} of {n} pass, the verdict is still reported but marked "
        f'"{NOT_VALIDATED}", and every miss is investigated and written up in `NOTES.md` '
        "before the README reports the result.",
        "",
    ]
    rows = [
        [
            f"`{r.cell_id}`",
            f"`{r.facility_fid}`",
            _metres(r.network_m),
            f"[route]({r.router_url})",
            "",
        ]
        for r in sheet.itertuples()
    ]
    lines.append(_table(["Cell", "Facility", "Network distance", "Router", "router_m"], rows))
    return "\n".join(lines) + "\n"


def write_spot_check(
    access: Access,
    path_csv: str | Path,
    path_md: str | Path,
    n: int = SPOT_CHECK_N,
    seed: int = SPOT_CHECK_SEED,
) -> pd.DataFrame:
    """Write the sheet as CSV (for the owner to fill) and as markdown (instructions + links).

    Only a registered access result gets a sheet.
    """
    require_registered(access)
    sheet = spot_check_sheet(access, n=n, seed=seed)
    path_csv, path_md = Path(path_csv), Path(path_md)
    for p in (path_csv, path_md):
        p.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(path_csv, index=False)
    path_md.write_text(_spot_check_markdown(sheet, path_csv.name), encoding="utf-8")
    return sheet


def score_spot_check(path_csv: str | Path, access: Access) -> dict[str, Any]:
    """Score the owner-filled sheet by the registered rule (Amendment 1).

    The seeded sheet is regenerated from ``access`` and the file must hold exactly those
    cells (20, or every eligible cell when fewer than 20 exist), so a hand-picked or stale
    sheet cannot be scored. A row passes when ours and the router's distances differ by at
    most 15% of the router's or at most 100 m; the sheet passes with at least 16 of 20 rows
    (80% rounded up for other sizes). A half-filled sheet is refused.
    """
    require_registered(access)
    expected = spot_check_sheet(access)
    df = pd.read_csv(path_csv, dtype={"cell_id": str, "facility_fid": str})
    for col in ("cell_id", "router_m"):
        if col not in df.columns:
            raise ValueError(f"spot-check sheet is missing column {col!r}")

    want = sorted(expected["cell_id"])
    got = sorted(df["cell_id"].astype(str))
    if got != want:
        extra, absent = sorted(set(got) - set(want)), sorted(set(want) - set(got))
        raise ValueError(
            f"the sheet is not the seeded draw from this access file: expected {len(want)} "
            f"cells, found {len(got)}; not expected {extra[:3]}, missing {absent[:3]}"
        )
    df = df.set_index(df["cell_id"].astype(str)).loc[list(expected["cell_id"])]

    router = pd.to_numeric(df["router_m"], errors="coerce")
    blank = int(router.isna().sum())
    if blank:
        raise ValueError(
            f"{blank} of {len(df)} rows have a blank or non-numeric router_m; "
            "fill every row before scoring"
        )
    if (router <= 0).any():
        raise ValueError("router_m must be positive in every row")

    ours = expected.set_index("cell_id")["network_m"].astype(float)
    ours = ours.loc[router.index]
    diff_m = (ours - router).abs()
    rel = diff_m / router
    within = (diff_m <= SPOT_CHECK_TOLERANCE_M + 1e-9) | (rel <= SPOT_CHECK_TOLERANCE + 1e-12)
    n = len(df)
    required = _required(n)
    n_within = int(within.sum())
    passed = n_within >= required
    miss_ids = [str(c) for c in router.index[~within.to_numpy()]]
    return {
        "n": n,
        "n_within": n_within,
        "required": required,
        "passed": passed,
        "status": "validated" if passed else NOT_VALIDATED,
        "tolerance": SPOT_CHECK_TOLERANCE,
        "tolerance_m": SPOT_CHECK_TOLERANCE_M,
        "median_rel_error": float(rel.median()),
        "max_rel_error": float(rel.max()),
        "misses": miss_ids,
        "miss_details": [
            {
                "cell_id": c,
                "network_m": float(ours[c]),
                "router_m": float(router[c]),
                "diff_m": float(diff_m[c]),
                "rel_error": float(rel[c]),
            }
            for c in miss_ids
        ],
        "rel_errors": [float(v) for v in rel],
    }


# -------------------------------------------------------------------------- figures


def _figure_class():
    """matplotlib's Figure, or a RuntimeError that names the extra to install.

    The object-oriented Figure saves through Agg by itself, so no global backend
    switch (and no pyplot state) is needed.
    """
    try:
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError(MAPS_HINT) from exc
    return Figure


def _is_real_crs(access: Access) -> bool:
    return access["config"]["crs"] != LOCAL_CRS


def _hex_outline_xy(cell_id: str, to_xy: Transformer) -> np.ndarray | None:
    """Projected corner points of an H3 cell, or None when the id is not an H3 cell."""
    try:
        if not h3.is_valid_cell(str(cell_id)):
            return None
        boundary = h3.cell_to_boundary(str(cell_id))
    except (ValueError, TypeError):
        return None
    lats, lons = zip(*boundary, strict=True)
    xs, ys = to_xy.transform(list(lons), list(lats))
    return np.column_stack([xs, ys])


def _cell_status(cell: dict[str, Any]) -> str:
    buf, net = bool(cell["buffer_ok"]), bool(cell["network_ok"])
    if buf and net:
        return "both reach"
    if buf:
        return "buffer only"
    return "network only" if net else "neither"


def _aoi_outline(ax, access: Access) -> None:
    from matplotlib.patches import Rectangle

    xmin, ymin, xmax, ymax = access["config"]["aoi"]
    ax.add_patch(
        Rectangle(
            (xmin, ymin),
            xmax - xmin,
            ymax - ymin,
            fill=False,
            edgecolor="#333333",
            linewidth=1.0,
            linestyle="--",
            zorder=5,
        )
    )
    pad = 0.03 * max(xmax - xmin, ymax - ymin)
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal")
    ax.set_xlabel(f"x (m, {access['config']['crs']})")
    ax.set_ylabel("y (m)")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=7)


def _facility_markers(ax, access: Access) -> None:
    facs = access["facilities"]
    if facs:
        ax.scatter(
            [f["x"] for f in facs],
            [f["y"] for f in facs],
            marker="*",
            s=90,
            c="#F0E442",
            edgecolors="black",
            linewidths=0.7,
            zorder=6,
        )


def _street_lines(ax, city_edges: pd.DataFrame | None) -> None:
    """Faint street geometry behind the data; skipped when no WKT is available."""
    if city_edges is None or "geometry" not in city_edges.columns:
        return
    import shapely
    from matplotlib.collections import LineCollection

    wkt = [g if isinstance(g, str) else None for g in city_edges["geometry"]]
    segments = []
    for geom in shapely.from_wkt(np.array(wkt, dtype=object), on_invalid="ignore"):
        if geom is None or geom.is_empty or geom.geom_type != "LineString":
            continue
        segments.append(np.asarray(geom.coords)[:, :2])
    if segments:
        ax.add_collection(LineCollection(segments, colors="#BBBBBB", linewidths=0.3, zorder=0))


def _draw_cells(ax, access: Access, colour_of) -> None:
    """Hexagons when the ids are H3 cells on a real CRS, otherwise plain points."""
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Polygon

    cells = access["cells"]
    to_xy = (
        Transformer.from_crs("EPSG:4326", access["config"]["crs"], always_xy=True)
        if (_is_real_crs(access))
        else None
    )
    patches, patch_colours, px, py, point_colours = [], [], [], [], []
    for c in cells:
        outline = _hex_outline_xy(c["cell_id"], to_xy) if to_xy is not None else None
        if outline is not None:
            patches.append(Polygon(outline, closed=True))
            patch_colours.append(colour_of(c))
        else:
            px.append(c["x"])
            py.append(c["y"])
            point_colours.append(colour_of(c))
    if patches:
        ax.add_collection(
            PatchCollection(
                patches, facecolors=patch_colours, edgecolors="none", alpha=0.9, zorder=2
            )
        )
    if px:
        ax.scatter(px, py, c=point_colours, s=14, linewidths=0, zorder=3)


def _title(access: Access, what: str) -> str:
    s = access["summary"]
    lo, hi = s["ci_cluster"]
    return f"{what}\ngap {s['gap_pp']:.2f} pp ({_confidence_label(access)} CI {lo:.2f} to {hi:.2f})"


def status_map(access: Access, path: str | Path, city_edges: pd.DataFrame | None = None) -> Path:
    """Cells coloured by status: both arms reach, buffer only, neither."""
    Figure = _figure_class()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    fig = Figure(figsize=(8, 8), dpi=150)
    ax = fig.add_subplot(1, 1, 1)
    _street_lines(ax, city_edges)
    _draw_cells(ax, access, lambda c: STATUS_COLOURS[_cell_status(c)])
    _facility_markers(ax, access)
    _aoi_outline(ax, access)

    present = [s for s in STATUS_COLOURS if any(_cell_status(c) == s for c in access["cells"])]
    handles = [Patch(facecolor=STATUS_COLOURS[s], label=s) for s in present]
    handles.append(
        Line2D(
            [],
            [],
            marker="*",
            linestyle="",
            markerfacecolor="#F0E442",
            markeredgecolor="black",
            markersize=11,
            label="facility",
        )
    )
    ax.legend(handles=handles, loc="lower left", fontsize=8, framealpha=0.9)
    ax.set_title(_title(access, "Access status by cell (walking threshold)"), fontsize=10)
    return _save(fig, path)


def zone_gap_map(access: Access, path: str | Path) -> Path:
    """Gap per zone (resolution-7 parent) on a sequential scale with a colour bar."""
    Figure = _figure_class()
    import matplotlib
    from matplotlib.cm import ScalarMappable
    from matplotlib.collections import PatchCollection
    from matplotlib.colors import Normalize
    from matplotlib.patches import Polygon

    zones = access["zones"]
    gaps = [z["gap_pp"] for z in zones if z["gap_pp"] is not None]
    norm = Normalize(vmin=0.0, vmax=max(max(gaps, default=0.0), 1.0))
    cmap = matplotlib.colormaps["viridis"]
    gap_of = {z["cluster"]: (z["gap_pp"] or 0.0) for z in zones}

    fig = Figure(figsize=(8.6, 8), dpi=150)
    ax = fig.add_subplot(1, 1, 1)
    to_xy = (
        Transformer.from_crs("EPSG:4326", access["config"]["crs"], always_xy=True)
        if (_is_real_crs(access))
        else None
    )

    patches, patch_colours = [], []
    drawn: set[Any] = set()
    for z in zones:
        outline = _hex_outline_xy(z["cluster"], to_xy) if to_xy is not None else None
        if outline is not None:
            patches.append(Polygon(outline, closed=True))
            patch_colours.append(cmap(norm(z["gap_pp"] or 0.0)))
            drawn.add(z["cluster"])
    if patches:
        ax.add_collection(
            PatchCollection(
                patches,
                facecolors=patch_colours,
                edgecolors="#444444",
                linewidths=0.4,
                alpha=0.9,
                zorder=2,
            )
        )
    # Zones that are not drawable hexagons fall back to the centroid of their cells.
    point_xy: dict[Any, list[tuple[float, float]]] = {}
    for c in access["cells"]:
        if c["cluster"] not in drawn:
            point_xy.setdefault(c["cluster"], []).append((c["x"], c["y"]))
    if point_xy:
        xs = [np.mean([p[0] for p in pts]) for pts in point_xy.values()]
        ys = [np.mean([p[1] for p in pts]) for pts in point_xy.values()]
        cols = [cmap(norm(gap_of.get(k, 0.0))) for k in point_xy]
        ax.scatter(xs, ys, c=cols, s=140, edgecolors="#444444", linewidths=0.5, zorder=3)

    _facility_markers(ax, access)
    _aoi_outline(ax, access)
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.75)
    bar.set_label("Gap, buffer minus network (percentage points)")
    ax.set_title(_title(access, "Overstatement of access by zone"), fontsize=10)
    return _save(fig, path)


def circuity_hist(access: Access, path: str | Path) -> Path:
    """Population-weighted histogram of network / straight-line distance."""
    Figure = _figure_class()
    ratios, weights = [], []
    for c in access["cells"]:
        net, euc = c.get("network_m"), c.get("euclid_m")
        if net is None or euc is None or not math.isfinite(net) or euc <= 0:
            continue
        ratios.append(net / euc)
        weights.append(c["pop"])
    if not ratios:
        raise ValueError("no cell has a finite network distance and a positive straight-line one")

    s = access["summary"]
    fig = Figure(figsize=(7, 4.2), dpi=150)
    ax = fig.add_subplot(1, 1, 1)
    ax.hist(ratios, bins=30, weights=weights, color="#0072B2", edgecolor="white", linewidth=0.4)
    if s.get("circuity_median") is not None:
        ax.axvline(
            s["circuity_median"],
            color="#D55E00",
            linewidth=1.5,
            label=f"median {s['circuity_median']:.2f}",
        )
    if s.get("circuity_p90") is not None:
        ax.axvline(
            s["circuity_p90"],
            color="#E69F00",
            linewidth=1.5,
            linestyle="--",
            label=f"90th percentile {s['circuity_p90']:.2f}",
        )
    ax.set_xlabel("walking distance / straight-line distance to the nearest facility")
    ax.set_ylabel("residents")
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8)
    ax.set_title("Circuity of the walking network (population-weighted)", fontsize=10)
    fig.tight_layout()
    return _save(fig, path)


def _save(fig, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    return path


# --------------------------------------------------------------------------- report

DEFAULT_TITLE = "Walkable access: straight-line buffer vs walking network"
FIGURES = (
    ("status_map.png", "Cells by access status"),
    ("zone_gap_map.png", "Gap by zone"),
    ("circuity_hist.png", "Circuity of the walking network"),
)


def write_report(
    access: Access,
    out_dir: str | Path,
    *,
    city_edges: pd.DataFrame | None = None,
    title: str = DEFAULT_TITLE,
) -> Path:
    """Write ``REPORT.md`` and the figures into ``out_dir``; return the REPORT.md path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = access["summary"]
    cfg = access["config"]
    lo, hi = s["ci_cluster"]

    figure_lines: list[str]
    try:
        fig_dir = out_dir / "figures"
        status_map(access, fig_dir / "status_map.png", city_edges=city_edges)
        zone_gap_map(access, fig_dir / "zone_gap_map.png")
        circuity_hist(access, fig_dir / "circuity_hist.png")
        figure_lines = [f"![{alt}](figures/{name})" for name, alt in FIGURES]
    except RuntimeError as exc:
        figure_lines = [f"Figures were skipped: {exc}"]

    verdict = s.get("verdict")
    if verdict is None:
        verdict_line = f"This run is **{verdict_words(None)}**. {NOT_REGISTERED_NOTE}"
    else:
        verdict_line = f"Verdict: **{verdict_words(verdict)}**."

    parts = [
        f"# {title}",
        "",
        f"Headline: the straight-line buffer overstates access by **{_pp(s['gap_pp'])}** "
        f"({_confidence_label(access)} cluster-bootstrap CI {_ci(lo, hi)}) at "
        f"{_metres(s['threshold_m'])}. {verdict_line}",
        "",
        f"Study area CRS {cfg['crs']}; {s['n_cells']:,} origin cells in {s['n_clusters']:,} zones; "
        f"{cfg['n_facilities']:,} facilities; {cfg['n_nodes']:,} network nodes and "
        f"{cfg['n_edges']:,} edges.",
        "",
        "## Summary",
        "",
        summary_table(access),
        "",
        "Values are shown to 2 decimals; the verdict uses the unrounded values.",
        "",
        "## Sensitivity to walking speed and time",
        "",
        "Gap in percentage points, cluster-bootstrap CI in brackets, then the distance "
        "threshold. The primary setting is in bold.",
        "",
        sensitivity_table(access),
        "",
        "## Where the gap falls",
        "",
        zones_table(access),
        "",
        "## Figures",
        "",
        *figure_lines,
        "",
    ]
    path = out_dir / "REPORT.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
