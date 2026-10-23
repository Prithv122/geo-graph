"""Extract round-trip, CRS guard, and the degrees-versus-metres bug the project exists to avoid."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from pyproj import Geod
from shapely.geometry import Point

from geonet.city import City
from geonet.osm import load_extract, save_extract, to_lonlat, to_projected
from geonet.synthetic import synthetic_city

BENGALURU_LON, BENGALURU_LAT = 77.5946, 12.9716
UTM43N = "EPSG:32643"


def test_round_trip_on_synthetic_city(tmp_path) -> None:
    city, _ = synthetic_city(river_row=5, bridge_col=3)
    save_extract(city, tmp_path, meta_extra={"source": "synthetic", "fetch_buffer_m": 0})
    back = load_extract(tmp_path)

    pd.testing.assert_frame_equal(back.nodes, city.nodes)
    pd.testing.assert_frame_equal(back.edges, city.edges)
    pd.testing.assert_frame_equal(back.facilities, city.facilities)
    assert back.crs == city.crs
    assert back.aoi == city.aoi

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "synthetic"
    assert meta["aoi"] == list(city.aoi)


def test_output_is_deterministic_and_sorted(tmp_path) -> None:
    city, _ = synthetic_city()
    shuffled = City(
        city.nodes.sample(frac=1, random_state=1),
        city.edges.sample(frac=1, random_state=2),
        city.facilities.iloc[::-1],
        city.crs,
        city.aoi,
    )
    save_extract(city, tmp_path / "a", meta_extra={"b": 1, "a": 2})
    save_extract(shuffled, tmp_path / "b", meta_extra={"a": 2, "b": 1})
    for name in ("nodes.parquet", "edges.parquet", "facilities.parquet", "meta.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name

    text = (tmp_path / "a" / "meta.json").read_text(encoding="utf-8")
    assert text.endswith("}\n")
    keys = list(json.loads(text))
    assert keys == sorted(keys)


def test_meta_extra_cannot_override_crs_or_aoi(tmp_path) -> None:
    city, _ = synthetic_city()
    save_extract(city, tmp_path, meta_extra={"crs": "EPSG:4326", "aoi": [0, 0, 1, 1]})
    back = load_extract(tmp_path)
    assert back.crs == city.crs
    assert back.aoi == city.aoi


def test_loader_reads_the_fetch_script_format(tmp_path) -> None:
    """Real-extract shape: big OSM ids, WKT or null geometry, `node/123` fids, UTM CRS."""
    big = 9_876_543_210
    nodes = pd.DataFrame({"node_id": [big, 5, big + 1], "x": [1.0, 2.0, 3.0], "y": [4.0, 5.0, 6.0]})
    edges = pd.DataFrame(
        {
            "u": [5, big],
            "v": [big, big + 1],
            "length_m": [10.0, 20.5],
            "highway": ["footway", "residential"],
            "geometry": ["LINESTRING (2 5, 1 4)", None],
        }
    )
    fac = pd.DataFrame(
        {
            "fid": ["way/2", "node/123"],
            "kind": ["clinic", "hospital"],
            "x": [1.0, 2.0],
            "y": [3.0, 4.0],
        }
    )
    city = City(nodes, edges, fac, UTM43N, (0.0, 0.0, 10.0, 10.0))
    save_extract(city, tmp_path)

    back = load_extract(tmp_path)
    assert back.nodes["node_id"].tolist() == [5, big, big + 1]
    assert back.nodes["node_id"].dtype == np.int64
    assert back.edges["u"].dtype == np.int64
    assert back.edges["geometry"].isna().tolist() == [False, True]
    assert back.facilities["fid"].tolist() == ["node/123", "way/2"]
    assert back.crs == UTM43N


def test_degree_crs_is_refused(tmp_path) -> None:
    city, _ = synthetic_city()
    save_extract(city, tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    meta["crs"] = "EPSG:4326"
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="projected in metres"):
        load_extract(tmp_path)


def test_garbage_crs_is_refused(tmp_path) -> None:
    city, _ = synthetic_city()
    save_extract(city, tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    meta["crs"] = "not-a-crs"
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid CRS"):
        load_extract(tmp_path)


def test_missing_files_name_the_fetch_script(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match=r"scripts/fetch_osm\.py"):
        load_extract(tmp_path / "nowhere")

    city, _ = synthetic_city()
    save_extract(city, tmp_path)
    (tmp_path / "edges.parquet").unlink()
    with pytest.raises(FileNotFoundError, match=r"edges\.parquet"):
        load_extract(tmp_path)


def test_projection_round_trip_scalars_and_arrays() -> None:
    x, y = to_projected(BENGALURU_LON, BENGALURU_LAT, UTM43N)
    lon, lat = to_lonlat(x, y, UTM43N)
    assert float(lon) == pytest.approx(BENGALURU_LON, abs=1e-9)
    assert float(lat) == pytest.approx(BENGALURU_LAT, abs=1e-9)

    xs, ys = to_projected([77.5, 77.6], [12.9, 13.0], UTM43N)
    assert xs.shape == ys.shape == (2,)
    # (lon, lat) order: easting is ~ 7.7e5 m in zone 43, northing ~ 1.4e6 m
    assert 700_000 < xs[0] < 800_000
    assert 1_400_000 < ys[0] < 1_500_000


def test_distances_in_metres_not_degrees() -> None:
    """A point 1 km east / north is 1,000 m in UTM, and ~0.009 'units' in raw degrees."""
    geod = Geod(ellps="WGS84")
    cx, cy = to_projected(BENGALURU_LON, BENGALURU_LAT, UTM43N)
    cx, cy = float(cx), float(cy)

    for azimuth in (90.0, 0.0):  # east, north
        lon, lat, _ = geod.fwd(BENGALURU_LON, BENGALURU_LAT, azimuth, 1000.0)
        px, py = to_projected(lon, lat, UTM43N)
        assert np.hypot(float(px) - cx, float(py) - cy) == pytest.approx(1000.0, abs=1.0)
        # the bug this guards against: measuring the same pair in degrees
        naive = np.hypot(lon - BENGALURU_LON, lat - BENGALURU_LAT)
        assert naive < 0.02
        assert abs(naive - 1000.0) > 900.0

    # a 1 km projected buffer contains a 990 m geodesic point and excludes a 1,010 m one
    disc = Point(cx, cy).buffer(1000.0)
    for azimuth in (0.0, 90.0, 180.0, 270.0, 45.0):
        for metres, inside in ((990.0, True), (1010.0, False)):
            lon, lat, _ = geod.fwd(BENGALURU_LON, BENGALURU_LAT, azimuth, metres)
            px, py = to_projected(lon, lat, UTM43N)
            assert disc.contains(Point(float(px), float(py))) is inside, (azimuth, metres)
