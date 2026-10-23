"""Extract round-trip, CRS guard, and the degrees-versus-metres bug the project exists to avoid."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from pyproj import Geod
from shapely.geometry import Point

from geonet import registered
from geonet.city import City
from geonet.osm import (
    check_registered_extract,
    load_extract,
    read_meta,
    save_extract,
    to_lonlat,
    to_projected,
)
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


# ------------------------------------------------------------ registered-extract check


def _registered_city(reach: float = 2_500.0, kinds=("hospital", "clinic", "doctors")) -> City:
    """A tiny extract in the registered CRS whose nodes reach ``reach`` m beyond the AOI."""
    aoi = registered.registered_aoi()
    xmin, ymin, xmax, ymax = aoi
    nodes = pd.DataFrame(
        {
            "node_id": [1, 2, 3, 4],
            "x": [xmin - reach, xmax + reach, xmin - reach, xmax + reach],
            "y": [ymin - reach, ymin - reach, ymax + reach, ymax + reach],
        }
    )
    edges = pd.DataFrame(
        {"u": [1], "v": [2], "length_m": [10.0], "highway": ["footway"], "geometry": [None]}
    )
    fac = pd.DataFrame(
        {
            "fid": [f"node/{i}" for i in range(len(kinds))],
            "kind": list(kinds),
            "x": [(xmin + xmax) / 2] * len(kinds),
            "y": [(ymin + ymax) / 2] * len(kinds),
        }
    )
    return City(nodes, edges, fac, registered.CRS, aoi)


GOOD_META = {"fetch_buffer_m": 2_500}


def test_registered_aoi_is_the_10_km_square_around_the_centre() -> None:
    xmin, ymin, xmax, ymax = registered.registered_aoi()
    assert (xmax - xmin, ymax - ymin) == (10_000.0, 10_000.0)
    cx, cy = to_projected(*registered.CENTRE_LONLAT, registered.CRS)
    assert (xmin + xmax) / 2 == pytest.approx(float(cx), abs=0.5)
    assert (ymin + ymax) / 2 == pytest.approx(float(cy), abs=0.5)
    assert xmin == int(xmin) and ymin == int(ymin)  # centre rounded to the metre


def test_a_good_registered_extract_has_no_problems() -> None:
    assert check_registered_extract(_registered_city(), GOOD_META) == []
    # an AOI within 1 m of the registered one is accepted
    city = _registered_city()
    moved = City(
        city.nodes,
        city.edges,
        city.facilities,
        city.crs,
        tuple(v + 0.9 for v in city.aoi),
    )
    assert check_registered_extract(moved, GOOD_META) == []


def test_aoi_more_than_one_metre_off_is_a_problem() -> None:
    city = _registered_city()
    xmin, ymin, xmax, ymax = city.aoi
    moved = City(city.nodes, city.edges, city.facilities, city.crs, (xmin + 1.5, ymin, xmax, ymax))
    problems = check_registered_extract(moved, GOOD_META)
    assert len(problems) == 1 and "AOI" in problems[0]


def test_crs_other_than_the_registered_one_is_a_problem() -> None:
    city = _registered_city()
    other = City(city.nodes, city.edges, city.facilities, "EPSG:32644", city.aoi)
    assert any("CRS" in p for p in check_registered_extract(other, GOOD_META))


@pytest.mark.parametrize("meta", [{}, {"fetch_buffer_m": 2_499}, {"fetch_buffer_m": 1_000}])
def test_missing_or_small_fetch_margin_is_a_problem(meta) -> None:
    problems = check_registered_extract(_registered_city(), meta)
    assert len(problems) == 1 and "fetch_buffer_m" in problems[0]


def test_a_larger_fetch_margin_is_fine() -> None:
    assert check_registered_extract(_registered_city(), {"fetch_buffer_m": 3_000}) == []


def test_network_must_reach_2000_m_beyond_the_aoi_on_every_side() -> None:
    assert check_registered_extract(_registered_city(reach=2_000.0), GOOD_META) == []
    problems = check_registered_extract(_registered_city(reach=1_999.0), GOOD_META)
    assert len(problems) == 1 and "2,000 m beyond the AOI" in problems[0]
    for side in ("west", "east", "south", "north"):
        assert side in problems[0]

    # only one side short: drop the nodes that reach the west
    city = _registered_city()
    nodes = city.nodes[city.nodes["x"] > city.aoi[0] - 100.0]
    nodes = pd.concat(
        [nodes, pd.DataFrame({"node_id": [9], "x": [city.aoi[0] - 100.0], "y": [city.aoi[1]]})]
    )
    short = City(nodes, city.edges, city.facilities, city.crs, city.aoi)
    problems = check_registered_extract(short, GOOD_META)
    assert len(problems) == 1 and "west" in problems[0] and "east" not in problems[0]


def test_facility_kind_outside_the_registered_set_is_a_problem() -> None:
    problems = check_registered_extract(_registered_city(kinds=("hospital", "pharmacy")), GOOD_META)
    assert len(problems) == 1 and "pharmacy" in problems[0]


def test_degree_like_coordinates_are_a_problem() -> None:
    city = _registered_city()
    nodes = city.nodes.copy()
    nodes.loc[0, "x"] = 77.59  # degrees, not metres
    fac = city.facilities.copy()
    fac.loc[0, "y"] = 12.97
    bad = City(nodes, city.edges, fac, city.crs, city.aoi)
    problems = check_registered_extract(bad, GOOD_META)
    assert any("node coordinates look like degrees" in p for p in problems)
    assert any("facility coordinates look like degrees" in p for p in problems)


def test_read_meta_round_trips_and_names_the_fetch_script(tmp_path) -> None:
    city, _ = synthetic_city()
    save_extract(city, tmp_path, meta_extra={"fetch_buffer_m": 2_500})
    meta = read_meta(tmp_path)
    assert meta["fetch_buffer_m"] == 2_500 and meta["crs"] == city.crs
    with pytest.raises(FileNotFoundError, match=r"scripts/fetch_osm\.py"):
        read_meta(tmp_path / "nowhere")


def test_check_works_on_a_saved_and_reloaded_extract(tmp_path) -> None:
    save_extract(_registered_city(), tmp_path, meta_extra=GOOD_META)
    assert check_registered_extract(load_extract(tmp_path), read_meta(tmp_path)) == []
