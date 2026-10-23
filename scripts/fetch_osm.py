"""One-off download of the Bengaluru walking network and health facilities from OpenStreetMap.

Writes data/osm/{nodes,edges,facilities}.parquet and data/osm/meta.json. The study area and the
fetch margin are registered in PREREGISTRATION.md; this script only reads them from aoi.py.

    uv run python scripts/fetch_osm.py --dry-run
    uv run python scripts/fetch_osm.py --out data/osm
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import aoi as study
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString

NETWORK_TYPE = "walk"
AMENITIES = ["hospital", "clinic", "doctors"]
SIMPLIFY_OVER_BYTES = 10 * 1024 * 1024
SIMPLIFY_TOLERANCE_M = 1.0
RETRY_WAITS_S = (60, 180, 420)  # polite backoff after a 429/504 or a timeout


def plan_text(aoi_bounds, bbox) -> str:
    left, bottom, right, top = bbox
    return "\n".join(
        [
            f"CRS            : {study.CRS}",
            f"Centre         : {study.CENTRE_LAT} N, {study.CENTRE_LON} E",
            f"AOI (m)        : xmin={aoi_bounds[0]:.0f} ymin={aoi_bounds[1]:.0f} "
            f"xmax={aoi_bounds[2]:.0f} ymax={aoi_bounds[3]:.0f}  ({study.AOI_SIZE_M} m square)",
            f"Fetch margin   : {study.FETCH_BUFFER_M} m on every side",
            f"Fetch bbox     : left={left} bottom={bottom} right={right} top={top} (lon/lat)",
            "Request plan   : 1) ox.graph_from_bbox(bbox, network_type='walk', simplify=True,",
            "                    retain_all=True, truncate_by_edge=True)  -> one Overpass query",
            f"                 2) ox.features_from_bbox(bbox, tags={{'amenity': {AMENITIES}}})",
            "                    -> one Overpass query (nodes, ways, relations)",
            "                 The data timestamp is read from the Overpass responses themselves.",
            "                 Retries (429/504/timeouts): waits of "
            f"{', '.join(str(w) for w in RETRY_WAITS_S)} s, then gives up.",
        ]
    )


def with_retries(label: str, fn):
    for attempt in range(len(RETRY_WAITS_S) + 1):
        try:
            return fn()
        except Exception as exc:  # osmnx/requests raise several types for 429/504/timeouts
            if attempt == len(RETRY_WAITS_S):
                raise
            wait = RETRY_WAITS_S[attempt]
            print(f"{label} failed ({type(exc).__name__}: {exc}); retrying in {wait} s", flush=True)
            time.sleep(wait)


def response_timestamps(cache: Path, since: float) -> list[str]:
    """Data timestamps (osm_base) that Overpass stamped on the responses cached in this run."""
    stamps = []
    for f in cache.glob("*.json"):
        if f.stat().st_mtime < since:
            continue
        with f.open(encoding="utf-8") as fh:
            m = re.search(r'"timestamp_osm_base":\s*"([^"]+)"', fh.read(2000))
        if m:
            stamps.append(m.group(1))
    return sorted(stamps)


def project_geoms(geoms: list[shapely.Geometry], transformer) -> list[shapely.Geometry]:
    out = []
    for g in geoms:
        xy = shapely.get_coordinates(g)
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        out.append(shapely.set_coordinates(g, np.column_stack([x, y])))
    return out


def build_nodes(graph, transformer) -> pd.DataFrame:
    ids = np.fromiter(graph.nodes, dtype=np.int64)
    lon = np.array([graph.nodes[n]["x"] for n in ids], dtype=float)
    lat = np.array([graph.nodes[n]["y"] for n in ids], dtype=float)
    x, y = transformer.transform(lon, lat)
    df = pd.DataFrame({"node_id": ids, "x": np.round(x, 2), "y": np.round(y, 2)})
    return df.sort_values("node_id").reset_index(drop=True)


def build_edges(graph, transformer) -> pd.DataFrame:
    rows, geoms = [], []
    for u, v, _key, data in graph.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is None:
            nu, nv = graph.nodes[u], graph.nodes[v]
            geom = LineString([(nu["x"], nu["y"]), (nv["x"], nv["y"])])
        hw = data.get("highway", "")
        hw = "|".join(str(h) for h in hw) if isinstance(hw, list) else str(hw)
        rows.append((int(u), int(v), hw))
        geoms.append(geom)
    projected = project_geoms(geoms, transformer)
    df = pd.DataFrame(rows, columns=["u", "v", "highway"])
    df["length_m"] = np.round(shapely.length(projected), 2)
    df["_geom"] = projected
    return df.sort_values(["u", "v", "length_m"], kind="stable").reset_index(drop=True)


def edges_with_wkt(df: pd.DataFrame, tolerance: float) -> pd.DataFrame:
    geoms = df["_geom"].to_numpy()
    if tolerance > 0:
        geoms = shapely.simplify(geoms, tolerance)  # endpoints of a LineString are always kept
    out = df.drop(columns="_geom").copy()
    out["geometry"] = shapely.to_wkt(geoms, rounding_precision=2)
    return out[["u", "v", "length_m", "highway", "geometry"]]


def build_facilities(gdf, transformer) -> pd.DataFrame:
    gdf = gdf.reset_index()
    pts = [g.representative_point() for g in gdf.geometry]
    xy = shapely.get_coordinates(project_geoms(pts, transformer))
    df = pd.DataFrame(
        {
            "fid": gdf["element"].astype(str) + "/" + gdf["id"].astype(str),
            "kind": gdf["amenity"].astype(str),
            "x": np.round(xy[:, 0], 2),
            "y": np.round(xy[:, 1], 2),
        }
    )
    return df.sort_values("fid").reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default="data/osm", help="output folder (default data/osm)")
    p.add_argument("--dry-run", action="store_true", help="print the plan, no network call")
    args = p.parse_args(argv)

    aoi_bounds = study.aoi_bounds()
    bbox = study.fetch_bbox_lonlat(aoi_bounds)
    print(plan_text(aoi_bounds, bbox))
    if args.dry_run:
        print("Dry run: nothing fetched.")
        return 0

    import osmnx as ox

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = Path("data/raw/osmnx_cache")
    cache.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache)
    ox.settings.requests_timeout = 240
    ox.settings.log_console = False

    run_start = time.time()
    transformer = study.to_utm()
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    t0 = time.time()
    graph = with_retries(
        "walking network",
        lambda: ox.graph_from_bbox(
            bbox, network_type=NETWORK_TYPE, simplify=True, retain_all=True, truncate_by_edge=True
        ),
    )
    t_graph = time.time() - t0
    print(f"network downloaded in {t_graph:.0f} s", flush=True)

    t0 = time.time()
    feats = with_retries(
        "facilities", lambda: ox.features_from_bbox(bbox, tags={"amenity": AMENITIES})
    )
    t_feat = time.time() - t0
    print(f"facilities downloaded in {t_feat:.0f} s", flush=True)

    nodes = build_nodes(graph, transformer)
    edges_raw = build_edges(graph, transformer)
    facilities = build_facilities(feats, transformer)
    stamps = response_timestamps(cache, run_start)

    nodes.to_parquet(out / "nodes.parquet", index=False, compression="zstd")
    facilities.to_parquet(out / "facilities.parquet", index=False, compression="zstd")
    simplified = 0
    edges = edges_with_wkt(edges_raw, 0)
    edges_path = out / "edges.parquet"
    edges.to_parquet(edges_path, index=False, compression="zstd")
    if edges_path.stat().st_size > SIMPLIFY_OVER_BYTES:
        edges = edges_with_wkt(edges_raw, SIMPLIFY_TOLERANCE_M)
        edges.to_parquet(edges_path, index=False, compression="zstd")
        simplified = int(SIMPLIFY_TOLERANCE_M)

    kinds = {k: int(v) for k, v in facilities["kind"].value_counts().sort_index().items()}
    meta = {
        "aoi": [float(v) for v in aoi_bounds],
        "aoi_size_m": study.AOI_SIZE_M,
        "centre_lonlat": [study.CENTRE_LON, study.CENTRE_LAT],
        "counts": {"edges": len(edges), "facilities": kinds, "nodes": len(nodes)},
        "crs": study.CRS,
        "fetch_bbox_lonlat": list(bbox),
        "fetch_buffer_m": study.FETCH_BUFFER_M,
        "fetched_at": fetched_at,
        "geometry_simplified_m": simplified,
        "source": (
            f"OpenStreetMap via the Overpass API, osmnx {ox.__version__}, "
            f"network_type={NETWORK_TYPE}"
        ),
    }
    if stamps:
        meta["osm_timestamp"] = stamps[0]  # oldest of the responses
        meta["osm_timestamps_by_response"] = stamps
    (out / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"nodes      : {len(nodes)}")
    print(f"edges      : {len(edges)} (directed rows; geometry simplified: {simplified} m)")
    print(f"facilities : {len(facilities)} {kinds}")
    for name in ("nodes", "edges", "facilities"):
        print(f"{name}.parquet: {(out / f'{name}.parquet').stat().st_size / 1e6:.2f} MB")
    print(f"network {t_graph:.0f} s, facilities {t_feat:.0f} s, osm_timestamps={stamps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
