"""Report tables, the spot-check sheet and the figures, on a small hand-made result."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from geonet import report

FIXTURE = Path(__file__).parent / "fixtures" / "access_small.json"


@pytest.fixture
def access() -> dict:
    return report.load_access(FIXTURE)


# ------------------------------------------------------------------------- tables


def test_load_access_has_expected_sections(access):
    assert set(access) == {"config", "facilities", "cells", "summary", "sensitivity", "zones"}
    assert len(access["cells"]) == 8


def test_summary_table_formats_fixture_numbers(access):
    table = report.summary_table(access)
    assert "| Residents with access, straight-line buffer | 73.54% |" in table
    assert "| Residents with access, walking network | 36.57% |" in table
    assert "| Gap (buffer minus network) | 36.97 pp |" in table
    assert "| 95% CI, cluster bootstrap | [30.47, 42.47] pp |" in table
    assert "| 95% CI, cell bootstrap (sensitivity) | [32.87, 40.87] pp |" in table
    assert "| Bootstrap | 2,000 resamples, seed 20261023 |" in table
    assert "| Snapping method | edge |" in table
    assert "| Verdict | H1 supported |" in table
    assert "3,165 of 8,560 (36.97%)" in table
    assert "1.38 and 1.52" in table
    assert "| Snapping distance, median | 30 m |" in table
    assert "| Residents snapped more than 200 m | 7.48% |" in table
    assert "1,250 m (5 km/h for 15 min)" in table


def test_summary_table_shows_the_node_snap_sensitivity(access):
    table = report.summary_table(access)
    assert (
        "| Nearest-node snapping, superseded by Amendment 1 (sensitivity) | "
        "gap 43.15 pp [37.10, 49.20], network share 30.39% |"
    ) in table


def test_unregistered_summary_has_no_verdict(access):
    access["summary"]["registered"] = False
    access["summary"]["verdict"] = None
    table = report.summary_table(access)
    assert "| Verdict | not the registered analysis (no verdict) |" in table
    assert "H1 supported" not in table


def test_summary_table_guards_missing_values(access):
    s = access["summary"]
    s["circuity_median"] = s["circuity_p90"] = None
    del s["node_snap_sensitivity"]
    del s["snap_method"]
    table = report.summary_table(access)
    assert "median and 90th percentile | n/a and n/a |" in table
    assert "| Nearest-node snapping, superseded by Amendment 1 (sensitivity) | n/a |" in table
    assert "| Snapping method | n/a |" in table


def test_zone_without_population_shows_na_and_sorts_last(access):
    access["zones"][0].update(share_buffer=None, share_network=None, gap_pp=None)
    text = report.zones_table(access, top=10)
    assert "n/a" in text
    by_gap = text.split("**Top 4 zones by residents affected**")[0]
    assert by_gap.rindex("8760145a6ffffff") > by_gap.index("87618925bffffff")


@pytest.mark.parametrize(
    ("verdict", "words"),
    [
        ("supported", "H1 supported"),
        ("null", "registered null"),
        ("not_supported", "H1 not supported"),
    ],
)
def test_verdict_wording(access, verdict, words):
    access["summary"]["verdict"] = verdict
    assert f"| Verdict | {words} |" in report.summary_table(access)


def test_unknown_verdict_is_an_error(access):
    access["summary"]["verdict"] = "maybe"
    with pytest.raises(ValueError, match="unknown verdict"):
        report.summary_table(access)


def test_verdict_words_for_none():
    assert report.verdict_words(None) == "not the registered analysis (no verdict)"


def test_sensitivity_table_is_speed_by_minutes(access):
    lines = report.sensitivity_table(access).splitlines()
    assert lines[0] == "| Speed \\ Minutes | 10 min | 15 min | 20 min |"
    assert len(lines) == 2 + 3  # header, rule, three speeds
    assert lines[2].startswith("| 4 km/h | 0.00 pp [0.00, 5.50], 667 m |")
    # the primary setting (5 km/h, 15 min) is the bold one in the middle row
    assert "**36.97 pp [30.47, 42.47], 1,250 m**" in lines[3]
    assert lines[4].endswith("| 0.00 pp [0.00, 5.50], 2,000 m |")


def test_zones_table_orders_by_gap_and_by_residents_affected(access):
    text = report.zones_table(access, top=2)
    by_gap, by_pop = text.split("**Top 2 zones by residents affected**")
    assert by_gap.index("87618925bffffff") < by_gap.index("8761892e9ffffff")
    assert by_gap.count("|") and "100.00 pp" in by_gap
    assert by_pop.index("8761892e9ffffff") < by_pop.index("87618925bffffff")
    assert "1,925" in by_pop
    assert "8760145a6ffffff" not in text  # third and fourth zones are cut off by top=2


def test_zones_table_top_larger_than_zone_count(access):
    assert "Top 4 zones by gap" in report.zones_table(access, top=10)


# ---------------------------------------------------------------------- spot check


def test_spot_check_is_deterministic_under_the_seed(access):
    a = report.spot_check_sheet(access, n=5)
    b = report.spot_check_sheet(access, n=5)
    pd.testing.assert_frame_equal(a, b)
    expected_idx = np.random.default_rng(report.SPOT_CHECK_SEED).choice(8, size=5, replace=False)
    ids = sorted(c["cell_id"] for c in access["cells"])
    assert list(a["cell_id"]) == sorted(ids[i] for i in expected_idx)
    other = report.spot_check_sheet(access, n=5, seed=1)
    assert list(other["cell_id"]) != list(a["cell_id"])


def test_spot_check_n_is_capped_at_cell_count(access):
    sheet = report.spot_check_sheet(access, n=20)
    assert len(sheet) == 8
    assert sheet["cell_id"].is_unique


def test_spot_check_skips_unreachable_cells(access):
    access["cells"][0]["network_m"] = None
    access["cells"][0]["network_ok"] = False
    sheet = report.spot_check_sheet(access)
    assert len(sheet) == 7
    assert access["cells"][0]["cell_id"] not in set(sheet["cell_id"])


def test_spot_check_columns_url_and_blanks(access):
    sheet = report.spot_check_sheet(access, n=3)
    assert list(sheet.columns) == [
        "cell_id", "origin_lat", "origin_lon", "facility_fid", "facility_lat", "facility_lon",
        "network_m", "euclid_m", "router_url", "router_m", "within_15pct",
    ]  # fmt: skip
    assert sheet["router_m"].isna().all()
    assert sheet["within_15pct"].isna().all()
    row = sheet.iloc[0]
    expected = (
        "https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route="
        f"{row.origin_lat:.6f}%2C{row.origin_lon:.6f}%3B{row.facility_lat:.6f}%2C{row.facility_lon:.6f}"
    )
    assert row.router_url == expected
    # Bengaluru sanity: the coordinates really are lon/lat there, not swapped or in metres
    assert 12.9 < row.origin_lat < 13.1 and 77.5 < row.origin_lon < 77.7


def test_spot_check_uses_the_network_facility(access):
    cell = next(c for c in access["cells"] if c["cell_id"] == "89618925a27ffff")
    cell["nearest_fid_network"] = "w3377"  # deliberately not the straight-line pick
    sheet = report.spot_check_sheet(access, n=8)
    assert sheet.set_index("cell_id").loc["89618925a27ffff", "facility_fid"] == "w3377"


def test_spot_check_refuses_synthetic_crs(access):
    access["config"]["crs"] = report.LOCAL_CRS
    with pytest.raises(ValueError, match="synthetic"):
        report.spot_check_sheet(access)


def test_write_spot_check_writes_csv_and_markdown(access, tmp_path):
    csv, md = tmp_path / "sub" / "sheet.csv", tmp_path / "sub" / "sheet.md"
    sheet = report.write_spot_check(access, csv, md, n=4)
    back = pd.read_csv(csv)
    assert list(back["cell_id"]) == list(sheet["cell_id"])
    assert back["router_m"].isna().all()
    text = md.read_text(encoding="utf-8")
    assert "router_m" in text and "sheet.csv" in text
    assert "at least 4 of 4" in text  # the 80% rule scaled to a 4-row sheet
    assert text.count("openstreetmap.org/directions") == 4


def test_spot_check_instructions_state_amendment_1(access, tmp_path):
    md = tmp_path / "sheet.md"
    report.write_spot_check(access, tmp_path / "sheet.csv", md, n=20)
    text = md.read_text(encoding="utf-8")
    assert "Amendment 1" in text
    assert "at most 15% **or** at most 100 m" in text
    assert "The facility is the one the network analysis chose" in text
    assert "not validated by the spot-check" in text
    assert "NOTES.md" in text


def test_write_spot_check_refuses_an_unregistered_result(access, tmp_path):
    access["summary"]["registered"] = False
    access["summary"]["verdict"] = None
    with pytest.raises(ValueError, match="registered analysis only"):
        report.write_spot_check(access, tmp_path / "s.csv", tmp_path / "s.md")
    assert not (tmp_path / "s.csv").exists()


def _with_cells(access: dict, n_cells: int, network_m=None) -> dict:
    """The fixture with ``n_cells`` cells, so the sheet is the registered 20-cell draw."""
    base = access["cells"][0]
    access["cells"] = [
        {
            **base,
            "cell_id": f"c{i:03d}",
            "network_m": 500.0 + 40 * i if network_m is None else network_m,
            "euclid_m": 400.0 + 30 * i,
        }
        for i in range(n_cells)
    ]
    return access


def _filled_sheet(access: dict, tmp_path: Path, router=None) -> Path:
    """Write the real sheet, then fill ``router_m`` (default: equal to our distance).

    ``router`` maps the sheet's ``network_m`` column to the router distances.
    """
    csv = tmp_path / "sheet.csv"
    report.write_spot_check(access, csv, tmp_path / "sheet.md")
    df = pd.read_csv(csv)
    df["router_m"] = df["network_m"] if router is None else router(df["network_m"])
    df.to_csv(csv, index=False)
    return csv


def test_registered_sheet_has_twenty_rows(access):
    sheet = report.spot_check_sheet(_with_cells(access, 30))
    assert len(sheet) == 20 and sheet["cell_id"].is_unique


def _router_off_by(k: int):
    """Router distances that are double ours (far off, by % and by metres) in the first k rows."""

    def router(net: pd.Series) -> pd.Series:
        r = net.copy()
        r.iloc[:k] = r.iloc[:k] * 2.0
        return r

    return router


def test_pass_rule_for_the_registered_sample_is_16_of_20(access, tmp_path):
    _with_cells(access, 30)
    csv = _filled_sheet(access, tmp_path, _router_off_by(4))
    res = report.score_spot_check(csv, access)
    assert (res["n"], res["n_within"], res["required"], res["passed"]) == (20, 16, 16, True)
    assert res["status"] == "validated"
    assert len(res["misses"]) == 4 and len(res["miss_details"]) == 4

    csv = _filled_sheet(access, tmp_path, _router_off_by(5))  # a fifth miss: 15 of 20
    res = report.score_spot_check(csv, access)
    assert (res["n_within"], res["passed"]) == (15, False)
    assert res["status"] == "not validated by the spot-check"
    assert res["miss_details"][0]["diff_m"] > 100


def test_long_route_passes_on_15_percent_and_boundary_is_inclusive(access, tmp_path):
    # 1,000 m routes: 100 m cannot rescue a row, only 15% can.
    _with_cells(access, 20, network_m=1000.0)
    inside = 1000.0 / 1.15  # ours is exactly 15% above the router's distance
    csv = _filled_sheet(access, tmp_path, lambda net: net * 0 + inside)
    assert report.score_spot_check(csv, access)["n_within"] == 20

    csv = _filled_sheet(access, tmp_path, lambda net: net * 0 + 800.0)  # 200 m and 25% off
    res = report.score_spot_check(csv, access)
    assert res["n_within"] == 0 and res["passed"] is False


def test_short_route_passes_within_100_metres_only(access, tmp_path):
    # 300 m routes: a router answer of 200 m is 50% off but exactly 100 m off.
    _with_cells(access, 20, network_m=300.0)
    csv = _filled_sheet(access, tmp_path, lambda net: net * 0 + 200.0)
    res = report.score_spot_check(csv, access)
    assert res["n_within"] == 20 and res["max_rel_error"] == pytest.approx(0.5)

    csv = _filled_sheet(access, tmp_path, lambda net: net * 0 + 199.0)  # 101 m and 51% off
    assert report.score_spot_check(csv, access)["n_within"] == 0


def test_score_requires_the_seeded_cells(access, tmp_path):
    _with_cells(access, 30)
    csv = _filled_sheet(access, tmp_path)
    df = pd.read_csv(csv)

    swapped = df.copy()
    swapped.loc[0, "cell_id"] = "c999"  # a cell the seeded draw did not pick
    swapped.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="not the seeded draw"):
        report.score_spot_check(csv, access)

    df.iloc[:19].to_csv(csv, index=False)  # a short sheet
    with pytest.raises(ValueError, match="expected 20 cells, found 19"):
        report.score_spot_check(csv, access)


def test_score_with_fewer_than_twenty_eligible_cells_uses_all_of_them(access, tmp_path):
    csv = _filled_sheet(access, tmp_path)  # the fixture has 8 cells
    res = report.score_spot_check(csv, access)
    assert (res["n"], res["required"], res["passed"]) == (8, 7, True)


def test_score_refuses_an_unregistered_access_file(access, tmp_path):
    csv = _filled_sheet(access, tmp_path)
    access["summary"]["registered"] = False
    with pytest.raises(ValueError, match="registered analysis only"):
        report.score_spot_check(csv, access)


def test_score_refuses_blank_router_m(access, tmp_path):
    csv = tmp_path / "sheet.csv"
    report.write_spot_check(access, csv, tmp_path / "sheet.md")
    with pytest.raises(ValueError, match="blank"):
        report.score_spot_check(csv, access)

    df = pd.read_csv(csv)
    df["router_m"] = df["network_m"] * 1.05
    df.loc[1, "router_m"] = np.nan
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="1 of 8"):
        report.score_spot_check(csv, access)

    df["router_m"] = df["network_m"] * 1.05
    df.to_csv(csv, index=False)
    assert report.score_spot_check(csv, access)["passed"] is True


def test_score_refuses_non_positive_router_m(access, tmp_path):
    csv = _filled_sheet(access, tmp_path)
    df = pd.read_csv(csv)
    df.loc[0, "router_m"] = 0.0
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="positive"):
        report.score_spot_check(csv, access)


# --------------------------------------------------------------------------- figures


def _is_png(path: Path) -> bool:
    return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _edges_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "geometry": [
                "LINESTRING (779000 1432000, 785000 1438000)",
                "LINESTRING (781000 1434000, 781000 1436000)",
                None,
            ]
        }
    )


def test_figures_are_written_for_a_real_crs(access, tmp_path):
    pytest.importorskip("matplotlib")
    p1 = report.status_map(access, tmp_path / "a" / "status.png", city_edges=_edges_frame())
    p2 = report.zone_gap_map(access, tmp_path / "zones.png")
    p3 = report.circuity_hist(access, tmp_path / "circ.png")
    for p in (p1, p2, p3):
        assert p.exists() and _is_png(p)


def test_figures_fall_back_to_points_for_non_h3_ids(access, tmp_path):
    pytest.importorskip("matplotlib")
    for i, c in enumerate(access["cells"]):
        c["cell_id"] = f"c{i}"
        c["cluster"] = f"b{i // 4}"
    for z, name in zip(access["zones"], ["b0", "b1", "b2", "b3"], strict=True):
        z["cluster"] = name
    access["config"]["crs"] = report.LOCAL_CRS
    assert _is_png(report.status_map(access, tmp_path / "s.png"))
    assert _is_png(report.zone_gap_map(access, tmp_path / "z.png"))


def test_circuity_hist_needs_usable_cells(access, tmp_path):
    pytest.importorskip("matplotlib")
    for c in access["cells"]:
        c["network_m"] = None
    with pytest.raises(ValueError, match="no cell"):
        report.circuity_hist(access, tmp_path / "c.png")


def test_missing_matplotlib_gives_a_clear_error(access, tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("no matplotlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="maps"):
        report.status_map(access, tmp_path / "x.png")


def test_write_report_without_matplotlib_still_writes_markdown(access, tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise RuntimeError(report.MAPS_HINT)

    monkeypatch.setattr(report, "status_map", refuse)
    path = report.write_report(access, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Figures were skipped" in text and "36.97 pp" in text
    assert not (tmp_path / "figures").exists() or not any((tmp_path / "figures").iterdir())


def test_write_report_mentions_gap_and_links_figures(access, tmp_path):
    pytest.importorskip("matplotlib")
    path = report.write_report(access, tmp_path / "out", title="Bengaluru test")
    assert path == tmp_path / "out" / "REPORT.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Bengaluru test")
    assert "36.97 pp" in text and "[30.47, 42.47] pp" in text
    assert "H1 supported" in text
    assert "the verdict uses the unrounded values" in text
    assert "## Sensitivity" in text and "## Where the gap falls" in text
    for name in ("status_map.png", "zone_gap_map.png", "circuity_hist.png"):
        assert f"figures/{name}" in text
        assert (tmp_path / "out" / "figures" / name).exists()


def test_report_numbers_come_from_the_access_dict(access, tmp_path):
    changed = copy.deepcopy(access)
    changed["summary"]["gap_pp"] = 12.3
    changed["summary"]["ci_cluster"] = [8.0, 16.5]
    changed["summary"]["verdict"] = "not_supported"
    text = report.write_report(changed, tmp_path).read_text(encoding="utf-8")
    assert "12.30 pp" in text and "[8.00, 16.50] pp" in text and "H1 not supported" in text
    assert "36.97 pp [" not in text.split("## Sensitivity")[0]


def test_unregistered_report_says_so_and_has_no_verdict(access, tmp_path):
    access["summary"]["registered"] = False
    access["summary"]["verdict"] = None
    text = report.write_report(access, tmp_path).read_text(encoding="utf-8")
    head = text.split("## Summary")[0]
    assert "not the registered analysis (no verdict)" in head
    assert "H1 supported" not in text
    assert "| Verdict | not the registered analysis (no verdict) |" in text


def test_report_guards_missing_circuity_and_zone_gaps(access, tmp_path):
    pytest.importorskip("matplotlib")
    access["summary"]["circuity_median"] = access["summary"]["circuity_p90"] = None
    access["zones"][0].update(share_buffer=None, share_network=None, gap_pp=None)
    text = report.write_report(access, tmp_path).read_text(encoding="utf-8")
    assert "n/a" in text
    assert (tmp_path / "figures" / "circuity_hist.png").exists()
