"""H3 population cells -> origins. Cells are built from real H3 ids; nothing touches the network."""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd
import pytest

from geonet.city import ORIGIN_COLUMNS
from geonet.hexgrid import cells_to_origins, load_pop
from geonet.osm import to_projected

CRS = "EPSG:32643"
CENTRE = h3.latlng_to_cell(12.97, 77.59, 9)


def _csv(tmp_path, rows: list[tuple[str, object]], name: str = "pop.csv"):
    path = tmp_path / name
    pd.DataFrame(rows, columns=["h3_id", "pop"]).to_csv(path, index=False)
    return path


def _ring(cell: str, k: int = 1) -> list[str]:
    return sorted(h3.grid_disk(cell, k))


def test_load_pop_round_trip(tmp_path) -> None:
    cells = _ring(CENTRE)
    path = _csv(tmp_path, [(c, 10.0 + i) for i, c in enumerate(cells)])
    df = load_pop(path)
    assert list(df.columns) == ["h3_id", "pop"]
    assert df["h3_id"].tolist() == cells
    assert df["pop"].dtype == float
    assert df["pop"].iloc[0] == 10.0


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ([("not-a-cell", 1.0)], "invalid H3"),
        ([("", 1.0)], "empty values|invalid H3"),
        ([(CENTRE, 1.0), (h3.cell_to_parent(CENTRE, 8), 1.0)], "one resolution"),
        ([(CENTRE, 1.0), (CENTRE, 2.0)], "duplicate"),
        ([(CENTRE, -1.0)], ">= 0"),
        ([(CENTRE, float("nan"))], "finite"),
        ([(CENTRE, float("inf"))], "finite"),
        ([(CENTRE, "many")], "finite"),
    ],
)
def test_load_pop_validation_errors(tmp_path, rows, match) -> None:
    with pytest.raises(ValueError, match=match):
        load_pop(_csv(tmp_path, rows))


def test_load_pop_rejects_missing_columns_and_empty(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("cell,people\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs columns"):
        load_pop(path)
    empty = tmp_path / "empty.csv"
    empty.write_text("h3_id,pop\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no rows"):
        load_pop(empty)


def test_zero_population_cells_are_dropped(tmp_path) -> None:
    cells = _ring(CENTRE)
    pops = [0.0, 5.0, 0.0, 7.0, 1.0, 2.0, 3.0]
    df = load_pop(_csv(tmp_path, list(zip(cells, pops, strict=True))))
    origins = cells_to_origins(df, CRS)
    assert len(origins) == 5
    assert (origins["pop"] > 0).all()
    assert set(origins["cell_id"]) == {c for c, p in zip(cells, pops, strict=True) if p > 0}


def test_origins_columns_cluster_and_order(tmp_path) -> None:
    cells = _ring(CENTRE, 2)
    df = load_pop(_csv(tmp_path, [(c, 1.0 + i) for i, c in enumerate(cells)]))
    origins = cells_to_origins(df, CRS)
    assert tuple(origins.columns) == ORIGIN_COLUMNS
    assert origins["cell_id"].is_monotonic_increasing
    assert origins["cell_id"].tolist() == cells
    for cell, cluster in zip(origins["cell_id"], origins["cluster"], strict=True):
        assert cluster == h3.cell_to_parent(cell, 7)
        assert h3.get_resolution(cluster) == 7
    other = cells_to_origins(df, CRS, parent_res=6)
    assert {h3.get_resolution(c) for c in other["cluster"]} == {6}


def test_centroid_projects_to_cell_centre(tmp_path) -> None:
    cells = _ring(CENTRE)
    df = load_pop(_csv(tmp_path, [(c, 4.0) for c in cells]))
    origins = cells_to_origins(df, CRS)
    for row in origins.itertuples():
        lat, lng = h3.cell_to_latlng(row.cell_id)  # (lat, lng): easy to swap by mistake
        ex, ey = to_projected(lng, lat, CRS)
        assert np.hypot(row.x - float(ex), row.y - float(ey)) < 5.0
    # centre cell lands near the registered Bengaluru centre, in metres
    centre = origins.set_index("cell_id").loc[CENTRE]
    cx, cy = to_projected(77.5946, 12.9716, CRS)
    assert np.hypot(centre.x - float(cx), centre.y - float(cy)) < 1000.0


def test_neighbouring_cells_are_a_cell_width_apart(tmp_path) -> None:
    """Res 9 cells are ~350 m across: a swapped lat/lng would give nonsense spacing."""
    ring = _ring(CENTRE)
    df = load_pop(_csv(tmp_path, [(c, 1.0) for c in ring]))
    origins = cells_to_origins(df, CRS).set_index("cell_id")
    ctr = origins.loc[CENTRE]
    for cid, row in origins.iterrows():
        if cid == CENTRE:
            continue
        d = np.hypot(row.x - ctr.x, row.y - ctr.y)
        assert 250.0 < d < 450.0


def test_parent_res_must_be_coarser(tmp_path) -> None:
    df = load_pop(_csv(tmp_path, [(c, 1.0) for c in _ring(CENTRE)]))
    with pytest.raises(ValueError, match="coarser"):
        cells_to_origins(df, CRS, parent_res=9)
    with pytest.raises(ValueError, match="coarser"):
        cells_to_origins(df, CRS, parent_res=10)


def test_all_zero_population_gives_empty_origins() -> None:
    df = pd.DataFrame({"h3_id": [CENTRE], "pop": [0.0]})
    origins = cells_to_origins(df, CRS)
    assert origins.empty
    assert tuple(origins.columns) == ORIGIN_COLUMNS
