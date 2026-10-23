"""The city extract: street network, facilities and the CRS they share.

Every coordinate is in a projected CRS in metres. Nothing in the analysis ever
measures a distance in degrees.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

NODE_COLUMNS = ("node_id", "x", "y")
EDGE_COLUMNS = ("u", "v", "length_m", "highway", "geometry")
FACILITY_COLUMNS = ("fid", "kind", "x", "y")
ORIGIN_COLUMNS = ("cell_id", "x", "y", "pop", "cluster")


def require_columns(df: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing columns {missing}")


@dataclass(frozen=True)
class City:
    """Street network and destinations of one study area.

    nodes:      node_id (int), x, y
    edges:      u, v, length_m, highway, geometry (WKT LineString or None)
    facilities: fid (str), kind (str), x, y
    crs:        the projected CRS of every x/y, e.g. "EPSG:32643"
    aoi:        (xmin, ymin, xmax, ymax) of the study area in that CRS; the
                network and facilities may extend beyond it, origins may not
    """

    nodes: pd.DataFrame
    edges: pd.DataFrame
    facilities: pd.DataFrame
    crs: str
    aoi: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        require_columns(self.nodes, NODE_COLUMNS, "nodes")
        require_columns(self.edges, EDGE_COLUMNS, "edges")
        require_columns(self.facilities, FACILITY_COLUMNS, "facilities")
        xmin, ymin, xmax, ymax = self.aoi
        if not (xmin < xmax and ymin < ymax):
            raise ValueError(f"aoi must be (xmin, ymin, xmax, ymax), got {self.aoi}")
