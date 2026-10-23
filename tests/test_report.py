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
    assert "| Residents with access, straight-line buffer | 73.5% |" in table
    assert "| Residents with access, walking network | 36.6% |" in table
    assert "| Gap (buffer minus network) | 37.0 pp |" in table
    assert "| 95% CI, cluster bootstrap | [30.5, 42.5] pp |" in table
    assert "| 95% CI, cell bootstrap (sensitivity) | [32.9, 40.9] pp |" in table
    assert "| Verdict | H1 supported |" in table
    assert "3,165 of 8,560 (37.0%)" in table
    assert "1.38 and 1.52" in table
    assert "| Snapping distance, median | 30 m |" in table
    assert "| Residents snapped more than 200 m | 7.5% |" in table
    assert "1,250 m (5 km/h for 15 min)" in table


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


def test_sensitivity_table_is_speed_by_minutes(access):
    lines = report.sensitivity_table(access).splitlines()
    assert lines[0] == "| Speed \\ Minutes | 10 min | 15 min | 20 min |"
    assert len(lines) == 2 + 3  # header, rule, three speeds
    assert lines[2].startswith("| 4 km/h | 0.0 pp [0.0, 5.5], 667 m |")
    # the primary setting (5 km/h, 15 min) is the bold one in the middle row
    assert "**37.0 pp [30.5, 42.5], 1,250 m**" in lines[3]
    assert lines[4].endswith("| 0.0 pp [0.0, 5.5], 2,000 m |")


def test_zones_table_orders_by_gap_and_by_residents_affected(access):
    text = report.zones_table(access, top=2)
    by_gap, by_pop = text.split("**Top 2 zones by residents affected**")
    assert by_gap.index("87618925bffffff") < by_gap.index("8761892e9ffffff")
    assert by_gap.count("|") and "100.0 pp" in by_gap
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


def test_pass_rule_for_the_registered_sample_is_16_of_20(access, tmp_path):
    csv = tmp_path / "s.csv"
    # 20 rows of a stand-in sheet; the cell ids do not matter to the scoring
    df = pd.DataFrame({"cell_id": [f"c{i}" for i in range(20)], "network_m": 1000.0})
    df["router_m"] = 1000.0
    df.loc[16:, "router_m"] = 500.0  # 4 rows are off by 100%
    df.to_csv(csv, index=False)
    res = report.score_spot_check(csv)
    assert (res["n"], res["n_within"], res["required"], res["passed"]) == (20, 16, 16, True)
    assert res["misses"] == ["c16", "c17", "c18", "c19"]

    df.loc[15, "router_m"] = 500.0  # a fifth miss: 15 of 20
    df.to_csv(csv, index=False)
    res = report.score_spot_check(csv)
    assert (res["n_within"], res["passed"]) == (15, False)


def test_tolerance_boundary_is_inclusive(tmp_path):
    csv = tmp_path / "s.csv"
    pd.DataFrame(
        {"cell_id": ["a", "b"], "network_m": [115.0, 116.0], "router_m": [100.0, 100.0]}
    ).to_csv(csv, index=False)
    res = report.score_spot_check(csv)
    assert res["n_within"] == 1  # 15% exactly counts, 16% does not
    assert res["misses"] == ["b"]
    assert res["max_rel_error"] == pytest.approx(0.16)


def test_score_refuses_blank_router_m(access, tmp_path):
    csv = tmp_path / "sheet.csv"
    report.write_spot_check(access, csv, tmp_path / "sheet.md", n=3)
    with pytest.raises(ValueError, match="blank"):
        report.score_spot_check(csv)

    df = pd.read_csv(csv)
    df["router_m"] = df["network_m"] * 1.05
    df.loc[1, "router_m"] = np.nan
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="1 of 3"):
        report.score_spot_check(csv)

    df["router_m"] = df["network_m"] * 1.05
    df.to_csv(csv, index=False)
    assert report.score_spot_check(csv)["passed"] is True


def test_score_refuses_non_positive_router_m(tmp_path):
    csv = tmp_path / "s.csv"
    pd.DataFrame({"cell_id": ["a"], "network_m": [100.0], "router_m": [0.0]}).to_csv(
        csv, index=False
    )
    with pytest.raises(ValueError, match="positive"):
        report.score_spot_check(csv)


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
    assert "Figures were skipped" in text and "37.0 pp" in text
    assert not (tmp_path / "figures").exists() or not any((tmp_path / "figures").iterdir())


def test_write_report_mentions_gap_and_links_figures(access, tmp_path):
    pytest.importorskip("matplotlib")
    path = report.write_report(access, tmp_path / "out", title="Bengaluru test")
    assert path == tmp_path / "out" / "REPORT.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Bengaluru test")
    assert "37.0 pp" in text and "[30.5, 42.5] pp" in text
    assert "H1 supported" in text
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
    assert "12.3 pp" in text and "[8.0, 16.5] pp" in text and "H1 not supported" in text
    assert "37.0 pp [" not in text.split("## Sensitivity")[0]
