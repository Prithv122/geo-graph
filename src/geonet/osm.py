"""Reading and writing the committed city extract, and CRS conversion helpers.

The extract is plain parquet plus a ``meta.json`` so that CI and clean clones never need the
network: the one-off download script writes it, everything else only reads it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from geonet import registered
from geonet.city import EDGE_COLUMNS, FACILITY_COLUMNS, NODE_COLUMNS, City, require_columns
from geonet.synthetic import SYNTHETIC_CRS

NODES_FILE = "nodes.parquet"
EDGES_FILE = "edges.parquet"
FACILITIES_FILE = "facilities.parquet"
META_FILE = "meta.json"
FETCH_SCRIPT = "scripts/fetch_osm.py"
_WGS84 = "EPSG:4326"


@lru_cache(maxsize=16)
def _transformer(src: str, dst: str) -> Transformer:
    # always_xy keeps (lon, lat) / (x, y) order whatever the CRS axis definition says.
    return Transformer.from_crs(src, dst, always_xy=True)


def to_projected(lon, lat, crs: str) -> tuple[np.ndarray, np.ndarray]:
    """Longitude/latitude in degrees -> x/y in ``crs`` (scalars or arrays)."""
    x, y = _transformer(_WGS84, crs).transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def to_lonlat(x, y, crs: str) -> tuple[np.ndarray, np.ndarray]:
    """x/y in ``crs`` -> longitude/latitude in degrees (scalars or arrays)."""
    lon, lat = _transformer(crs, _WGS84).transform(np.asarray(x, float), np.asarray(y, float))
    return np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)


def _check_metric_crs(crs: str) -> None:
    """Refuse anything but a projected CRS in metres: degrees would silently break distances."""
    if crs == SYNTHETIC_CRS:
        return
    try:
        parsed = CRS(crs)
    except CRSError as exc:
        raise ValueError(f"extract CRS {crs!r} is not a valid CRS") from exc
    units = {axis.unit_name for axis in parsed.axis_info}
    if not parsed.is_projected or units != {"metre"}:
        raise ValueError(
            f"extract CRS {crs!r} must be projected in metres (units: {sorted(units)}); "
            "distances are never computed in degrees"
        )


def save_extract(city: City, directory, meta_extra: dict | None = None) -> None:
    """Write the extract deterministically (sorted rows, sorted JSON keys).

    ``crs`` and ``aoi`` always come from ``city`` and win over the same keys in ``meta_extra``.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    nodes = city.nodes[list(NODE_COLUMNS)].copy()
    nodes["node_id"] = nodes["node_id"].astype("int64")
    nodes[["x", "y"]] = nodes[["x", "y"]].astype("float64")
    nodes = nodes.sort_values("node_id", kind="stable").reset_index(drop=True)

    edges = city.edges[list(EDGE_COLUMNS)].copy()
    edges[["u", "v"]] = edges[["u", "v"]].astype("int64")
    edges["length_m"] = edges["length_m"].astype("float64")
    edges["highway"] = edges["highway"].astype(str)
    edges["geometry"] = edges["geometry"].astype(object).where(edges["geometry"].notna(), None)
    edges = edges.sort_values(["u", "v", "length_m"], kind="stable").reset_index(drop=True)

    fac = city.facilities[list(FACILITY_COLUMNS)].copy()
    fac[["fid", "kind"]] = fac[["fid", "kind"]].astype(str)
    fac[["x", "y"]] = fac[["x", "y"]].astype("float64")
    fac = fac.sort_values("fid", kind="stable").reset_index(drop=True)

    for frame, name in ((nodes, NODES_FILE), (edges, EDGES_FILE), (fac, FACILITIES_FILE)):
        frame.to_parquet(out / name, index=False, compression="zstd")

    meta = {**(meta_extra or {}), "crs": city.crs, "aoi": [float(v) for v in city.aoi]}
    text = json.dumps(meta, indent=2, sort_keys=True) + "\n"
    (out / META_FILE).write_text(text, encoding="utf-8", newline="\n")


def read_meta(directory) -> dict:
    """Read ``meta.json`` of an extract (the fetch settings the registered check needs)."""
    path = Path(directory) / META_FILE
    if not path.is_file():
        raise FileNotFoundError(
            f"extract in {Path(directory)} has no {META_FILE}; run `python {FETCH_SCRIPT}` "
            "once (needs the `fetch` extra and network access) to create it"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_extract(directory) -> City:
    """Read an extract written by :func:`save_extract` (or by the fetch script)."""
    src = Path(directory)
    names = (NODES_FILE, EDGES_FILE, FACILITIES_FILE, META_FILE)
    missing = [n for n in names if not (src / n).is_file()]
    if missing:
        raise FileNotFoundError(
            f"extract in {src} is missing {missing}; run `python {FETCH_SCRIPT}` "
            "once (needs the `fetch` extra and network access) to create it"
        )

    meta = read_meta(src)
    crs = meta.get("crs")
    aoi = meta.get("aoi")
    if not isinstance(crs, str) or not isinstance(aoi, list) or len(aoi) != 4:
        raise ValueError(f"{META_FILE} needs a string `crs` and a 4-number `aoi`")
    _check_metric_crs(crs)

    nodes = pd.read_parquet(src / NODES_FILE)
    edges = pd.read_parquet(src / EDGES_FILE)
    facilities = pd.read_parquet(src / FACILITIES_FILE)
    require_columns(nodes, NODE_COLUMNS, "nodes")
    require_columns(edges, EDGE_COLUMNS, "edges")
    require_columns(facilities, FACILITY_COLUMNS, "facilities")

    nodes = nodes[list(NODE_COLUMNS)].astype({"node_id": "int64"}).reset_index(drop=True)
    edges = edges[list(EDGE_COLUMNS)].astype({"u": "int64", "v": "int64"}).reset_index(drop=True)
    facilities = facilities[list(FACILITY_COLUMNS)].reset_index(drop=True)
    return City(nodes, edges, facilities, crs, tuple(float(v) for v in aoi))


# Coordinates smaller than this in absolute value look like degrees, not UTM metres.
_DEGREE_LIMIT = 1000.0
# The network must reach beyond the largest threshold (2,000 m) on every side of the AOI.
_MIN_REACH_M = 2_000.0


def check_registered_extract(city: City, meta: dict) -> list[str]:
    """Why this extract cannot carry the registered analysis; an empty list means it can.

    Checked: the CRS and AOI (to 1 m), the fetch margin recorded at download time, that the
    nodes really reach 2,000 m beyond the AOI on every side, the facility kinds, and that no
    coordinate looks like degrees. Each problem is one readable sentence.
    """
    problems: list[str] = []

    if city.crs != registered.CRS:
        problems.append(f"CRS is {city.crs}, the registered CRS is {registered.CRS}")

    aoi = registered.registered_aoi()
    if any(abs(a - b) > 1.0 for a, b in zip(city.aoi, aoi, strict=True)):
        problems.append(
            f"AOI {tuple(round(v, 1) for v in city.aoi)} differs by more than 1 m from the "
            f"registered AOI {tuple(round(v, 1) for v in aoi)}"
        )

    margin = meta.get("fetch_buffer_m")
    if not isinstance(margin, int | float) or isinstance(margin, bool):
        problems.append("fetch_buffer_m is missing from meta.json")
    elif margin < registered.FETCH_MARGIN_M:
        problems.append(
            f"fetch_buffer_m is {margin:g} m, below the registered {registered.FETCH_MARGIN_M} m"
        )

    nodes, fac = city.nodes, city.facilities
    if len(nodes):
        xmin, ymin, xmax, ymax = city.aoi
        reach = {
            "west": xmin - float(nodes["x"].min()),
            "east": float(nodes["x"].max()) - xmax,
            "south": ymin - float(nodes["y"].min()),
            "north": float(nodes["y"].max()) - ymax,
        }
        short = [f"{side} {v:,.0f} m" for side, v in reach.items() if v < _MIN_REACH_M]
        if short:
            problems.append(
                f"network nodes reach less than {_MIN_REACH_M:,.0f} m beyond the AOI on: "
                + ", ".join(short)
            )
    else:
        problems.append("the extract has no network nodes")

    kinds = sorted(set(fac["kind"].astype(str)) - registered.ALLOWED_KINDS)
    if kinds:
        problems.append(f"facility kinds outside the registered set: {kinds}")

    for name, frame in (("node", nodes), ("facility", fac)):
        small = (frame["x"].abs() < _DEGREE_LIMIT) | (frame["y"].abs() < _DEGREE_LIMIT)
        if bool(small.any()):
            problems.append(
                f"{int(small.sum())} {name} coordinates look like degrees, not metres "
                f"(|x| or |y| < {_DEGREE_LIMIT:g})"
            )
    return problems
