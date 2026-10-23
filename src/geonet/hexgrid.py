"""Population on H3 cells -> weighted origins in projected metres."""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd

from geonet.city import ORIGIN_COLUMNS
from geonet.osm import to_projected


def _resolution(cell_ids: pd.Series) -> int:
    """Common resolution of valid cells; raises if the ids mix resolutions."""
    resolutions = {h3.get_resolution(c) for c in cell_ids}
    if len(resolutions) != 1:
        raise ValueError(f"cells must share one resolution, found {sorted(resolutions)}")
    return resolutions.pop()


def load_pop(path) -> pd.DataFrame:
    """Read ``h3_id,pop`` and validate it, so a bad cell never reaches the analysis."""
    df = pd.read_csv(path, dtype={"h3_id": str})
    if "h3_id" not in df.columns or "pop" not in df.columns:
        raise ValueError(f"{path} needs columns h3_id and pop, found {list(df.columns)}")
    df = df[["h3_id", "pop"]]
    if df.empty:
        raise ValueError(f"{path} has no rows")
    if df["h3_id"].isna().any():
        raise ValueError("h3_id has empty values")

    bad = [c for c in df["h3_id"] if not h3.is_valid_cell(c)]
    if bad:
        raise ValueError(f"{len(bad)} invalid H3 cell ids, e.g. {bad[0]!r}")
    _resolution(df["h3_id"])
    if df["h3_id"].duplicated().any():
        dup = df.loc[df["h3_id"].duplicated(), "h3_id"].iloc[0]
        raise ValueError(f"duplicate H3 cell id {dup!r}")

    pop = pd.to_numeric(df["pop"], errors="coerce").astype(float)
    if not np.isfinite(pop).all():
        raise ValueError("pop must be finite numbers")
    if (pop < 0).any():
        raise ValueError("pop must be >= 0")
    return pd.DataFrame({"h3_id": df["h3_id"].astype(str), "pop": pop}).reset_index(drop=True)


def cells_to_origins(pop_df: pd.DataFrame, crs: str, parent_res: int = 7) -> pd.DataFrame:
    """One origin per populated cell: projected centroid, population, res-``parent_res`` parent.

    The parent id is the cluster for the bootstrap: neighbouring cells share streets and
    facilities, so they have to be resampled together.
    """
    cells = pop_df.loc[pop_df["pop"] > 0, ["h3_id", "pop"]]
    if cells.empty:
        return pd.DataFrame({c: [] for c in ORIGIN_COLUMNS})
    res = _resolution(cells["h3_id"])
    if parent_res >= res:
        raise ValueError(f"parent_res {parent_res} must be coarser than cell resolution {res}")

    ids = cells["h3_id"].tolist()
    latlng = np.array([h3.cell_to_latlng(c) for c in ids])  # rows are (lat, lng)
    x, y = to_projected(latlng[:, 1], latlng[:, 0], crs)
    origins = pd.DataFrame(
        {
            "cell_id": ids,
            "x": x,
            "y": y,
            "pop": cells["pop"].to_numpy(dtype=float),
            "cluster": [h3.cell_to_parent(c, parent_res) for c in ids],
        }
    )
    return origins.sort_values("cell_id", kind="stable").reset_index(drop=True)
