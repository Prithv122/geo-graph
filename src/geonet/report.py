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

Access = dict[str, Any]

LOCAL_CRS = "LOCAL:metres"
SPOT_CHECK_SEED = 20261023
SPOT_CHECK_N = 20
SPOT_CHECK_TOLERANCE = 0.15
# Registered rule: at least 16 of 20 within 15% (80%); other sample sizes scale it up.
SPOT_CHECK_PASS_SHARE = 0.8
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


def _pct(share: float) -> str:
    return f"{100 * share:.1f}%"


def _pp(value: float) -> str:
    return f"{value:.1f} pp"


def _ci(lo: float, hi: float) -> str:
    return f"[{lo:.1f}, {hi:.1f}] pp"


def _people(value: float) -> str:
    return f"{round(value):,}"


def _metres(value: float) -> str:
    return f"{round(value):,} m"


def verdict_words(verdict: str) -> str:
    """The registered verdict as a phrase; unknown codes are an error, not a guess."""
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
    affected_share = s["pop_affected"] / s["pop_total"] if s["pop_total"] else float("nan")
    rows = [
        ["Walking threshold", threshold],
        ["Residents with access, straight-line buffer", _pct(s["share_buffer"])],
        ["Residents with access, walking network", _pct(s["share_network"])],
        ["Gap (buffer minus network)", _pp(s["gap_pp"])],
        [f"{conf} CI, cluster bootstrap", _ci(*s["ci_cluster"])],
        [f"{conf} CI, cell bootstrap (sensitivity)", _ci(*s["ci_cell"])],
        ["Verdict", verdict_words(s["verdict"])],
        [
            "Residents the buffer counts as served but the network does not",
            f"{_people(s['pop_affected'])} of {_people(s['pop_total'])} ({_pct(affected_share)})",
        ],
        [
            "Circuity (network / straight line), median and 90th percentile",
            f"{s['circuity_median']:.2f} and {s['circuity_p90']:.2f}",
        ],
        ["Snapping distance, median", _metres(s["snap_median_m"])],
        ["Residents snapped more than 200 m", _pct(s["snap_over_200m_share"])],
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
            text = f"{r['gap_pp']:.1f} pp [{lo:.1f}, {hi:.1f}], {_metres(r['threshold_m'])}"
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


def zones_table(access: Access, top: int = 10) -> str:
    """Top zones by gap, then top zones by residents affected (two markdown tables)."""
    zones = access["zones"]
    header = ["Zone", "Residents", "Buffer", "Network", "Gap", "Residents affected"]
    by_gap = sorted(zones, key=lambda z: (-z["gap_pp"], str(z["cluster"])))[:top]
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


def _spot_check_markdown(sheet: pd.DataFrame, csv_name: str) -> str:
    n = len(sheet)
    need = math.ceil(SPOT_CHECK_PASS_SHARE * n - 1e-9)
    lines = [
        "# Spot-check sheet",
        "",
        f"{n} cells drawn at random (fixed seed) from the access result. For each row:",
        "",
        "1. Open the link. It asks the openstreetmap.org foot router for the walk from the "
        "cell centroid to the facility the network analysis chose.",
        "2. Read the walking distance the router shows, in metres, and type it into "
        f"`router_m` in `{csv_name}`. Leave `within_15pct` blank; scoring fills it in.",
        "3. Do not adjust the link's endpoints: the point of the check is that the router "
        "never sees the analysis network.",
        "",
        f"Pass rule (pre-registered): at least {need} of {n} rows have the network distance "
        f"within {round(100 * SPOT_CHECK_TOLERANCE)}% of the router's. "
        "A miss is investigated and written up before the result is reported.",
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
    """Write the sheet as CSV (for the owner to fill) and as markdown (instructions + links)."""
    sheet = spot_check_sheet(access, n=n, seed=seed)
    path_csv, path_md = Path(path_csv), Path(path_md)
    for p in (path_csv, path_md):
        p.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(path_csv, index=False)
    path_md.write_text(_spot_check_markdown(sheet, path_csv.name), encoding="utf-8")
    return sheet


def score_spot_check(path_csv: str | Path) -> dict[str, Any]:
    """Score the owner-filled sheet by the registered rule (16 of 20 within 15%).

    Refuses a sheet with any blank or non-positive ``router_m``: a half-filled sheet
    must not produce a verdict.
    """
    df = pd.read_csv(path_csv)
    for col in ("cell_id", "network_m", "router_m"):
        if col not in df.columns:
            raise ValueError(f"spot-check sheet is missing column {col!r}")
    router = pd.to_numeric(df["router_m"], errors="coerce")
    blank = int(router.isna().sum())
    if blank or df.empty:
        raise ValueError(
            f"{blank} of {len(df)} rows have a blank or non-numeric router_m; "
            "fill every row before scoring"
        )
    if (router <= 0).any():
        raise ValueError("router_m must be positive in every row")

    rel = (df["network_m"] - router).abs() / router
    within = rel <= SPOT_CHECK_TOLERANCE + 1e-12
    n = len(df)
    required = math.ceil(SPOT_CHECK_PASS_SHARE * n - 1e-9)
    n_within = int(within.sum())
    return {
        "n": n,
        "n_within": n_within,
        "required": required,
        "passed": n_within >= required,
        "tolerance": SPOT_CHECK_TOLERANCE,
        "median_rel_error": float(rel.median()),
        "max_rel_error": float(rel.max()),
        "misses": [str(c) for c in df.loc[~within, "cell_id"]],
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
    return f"{what}\ngap {s['gap_pp']:.1f} pp ({_confidence_label(access)} CI {lo:.1f} to {hi:.1f})"


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
    gaps = [z["gap_pp"] for z in zones]
    norm = Normalize(vmin=0.0, vmax=max(max(gaps, default=0.0), 1.0))
    cmap = matplotlib.colormaps["viridis"]
    gap_of = {z["cluster"]: z["gap_pp"] for z in zones}

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
            patch_colours.append(cmap(norm(z["gap_pp"])))
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
    ax.axvline(
        s["circuity_median"],
        color="#D55E00",
        linewidth=1.5,
        label=f"median {s['circuity_median']:.2f}",
    )
    ax.axvline(
        s["circuity_p90"],
        color="#E69F00",
        linewidth=1.5,
        linestyle="--",
        label=f"90th percentile {s['circuity_p90']:.2f}",
    )
    ax.set_xlabel("walking distance / straight-line distance to the nearest facility")
    ax.set_ylabel("residents")
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

    parts = [
        f"# {title}",
        "",
        f"Headline: the straight-line buffer overstates access by **{_pp(s['gap_pp'])}** "
        f"({_confidence_label(access)} cluster-bootstrap CI {_ci(lo, hi)}) at "
        f"{_metres(s['threshold_m'])}. Verdict: **{verdict_words(s['verdict'])}**.",
        "",
        f"Study area CRS {cfg['crs']}; {s['n_cells']:,} origin cells in {s['n_clusters']:,} zones; "
        f"{cfg['n_facilities']:,} facilities; {cfg['n_nodes']:,} network nodes and "
        f"{cfg['n_edges']:,} edges.",
        "",
        "## Summary",
        "",
        summary_table(access),
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
