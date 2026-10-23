"""The command line end to end on the synthetic grid, and the registered-run decision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from geonet import cli, registered

REGISTERED = {
    "synthetic": False,
    "speed_kmh": 5,
    "minutes": 15,
    "n_boot": 2000,
    "seed": 20261023,
    "extract_problems": [],
    "pop_resolution": 9,
}


def test_package_imports() -> None:
    import geonet

    assert geonet.__version__


# ------------------------------------------------------------------ end to end (synthetic)


@pytest.fixture
def access_path(tmp_path, capsys) -> Path:
    out = tmp_path / "access.json"
    code = cli.main(["access", "--synthetic", "--n-boot", "50", "--out", str(out)])
    assert code == 0
    capsys.readouterr()
    return out


def test_build_prints_graph_counts(capsys) -> None:
    assert cli.main(["build", "--synthetic"]) == 0
    text = capsys.readouterr().out
    assert "nodes" in text and "facilities" in text


def test_access_on_synthetic_is_not_the_registered_analysis(tmp_path, capsys) -> None:
    out = tmp_path / "sub" / "access.json"
    code = cli.main(["access", "--synthetic", "--n-boot", "50", "--out", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "not the registered analysis (no verdict)" in text
    assert f"wrote {out}" in text

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["summary"]["registered"] is False
    assert result["summary"]["verdict"] is None
    assert result["config"]["bootstrap"]["n"] == 50


def test_report_says_it_is_not_registered(access_path, tmp_path, capsys) -> None:
    out_dir = tmp_path / "report"
    assert cli.main(["report", "--access", str(access_path), "--out", str(out_dir)]) == 0
    text = (out_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "not the registered analysis (no verdict)" in text
    assert "H1 supported" not in text and "registered null" not in text
    assert not (out_dir / "spot_check.csv").exists()


def test_report_spot_check_fails_cleanly_on_an_unregistered_access_file(
    access_path, tmp_path, capsys
) -> None:
    out_dir = tmp_path / "report"
    code = cli.main(["report", "--access", str(access_path), "--out", str(out_dir), "--spot-check"])
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "registered analysis" in err
    assert not out_dir.exists()  # nothing is written before the refusal


def test_score_spot_check_refuses_an_unregistered_access_file(
    access_path, tmp_path, capsys
) -> None:
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("cell_id,router_m\n", encoding="utf-8")
    code = cli.main(["score-spot-check", str(sheet), "--access", str(access_path)])
    assert code == 2
    assert "registered analysis" in capsys.readouterr().err


def test_missing_access_file_is_a_clean_error(tmp_path, capsys) -> None:
    code = cli.main(["report", "--access", str(tmp_path / "nope.json")])
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")


# --------------------------------------------------------------------- output file naming


def _args(**kw) -> argparse.Namespace:
    base = {"synthetic": False, "pop": cli.DEFAULT_POP, "out": None}
    return argparse.Namespace(**{**base, **kw})


def test_primary_run_writes_results_access_json() -> None:
    assert cli._access_out(_args()) == Path("results/access.json")


def test_resolution_10_run_has_its_own_default_file() -> None:
    args = _args(pop=Path("data/pop_h3_r10.csv"))
    assert cli._access_out(args) == Path("results/access_r10.json")


def test_explicit_out_wins_but_r10_cannot_overwrite_the_primary() -> None:
    other = Path("somewhere/else.json")
    assert cli._access_out(_args(pop=Path("data/pop_h3_r10.csv"), out=other)) == other
    with pytest.raises(ValueError, match="must not overwrite"):
        cli._access_out(_args(pop=Path("data/pop_h3_r10.csv"), out=Path("results/access.json")))


def test_synthetic_default_is_under_results_synthetic() -> None:
    assert cli._access_out(_args(synthetic=True)) == Path("results/synthetic/access.json")


def test_report_folder_for_a_non_primary_access_file(tmp_path, access_path, capsys) -> None:
    r10 = tmp_path / "access_r10.json"
    r10.write_bytes(access_path.read_bytes())
    assert cli.main(["report", "--access", str(r10)]) == 0
    assert (tmp_path / "access_r10" / "REPORT.md").exists()
    assert not (tmp_path / "REPORT.md").exists()  # the primary report is never overwritten


# --------------------------------------------------------------------- the registered decision


def test_the_registered_settings_are_the_registered_run() -> None:
    assert registered.is_registered_run(**REGISTERED) is True


@pytest.mark.parametrize(
    "change",
    [
        {"synthetic": True},
        {"speed_kmh": 4},
        {"speed_kmh": 6},
        {"minutes": 10},
        {"minutes": 20},
        {"n_boot": 1999},
        {"n_boot": 50},
        {"seed": 1},
        {"extract_problems": ["AOI differs"]},
        {"pop_resolution": 10},
        {"pop_resolution": None},
    ],
)
def test_each_condition_flips_the_decision(change) -> None:
    assert registered.is_registered_run(**{**REGISTERED, **change}) is False


def test_float_speed_and_minutes_still_match() -> None:
    assert registered.is_registered_run(**{**REGISTERED, "speed_kmh": 5.0, "minutes": 15.0})


def test_cli_defaults_are_the_registered_settings() -> None:
    args = cli.build_parser().parse_args(["access"])
    assert (args.speed, args.minutes, args.n_boot, args.seed) == (
        registered.SPEED_KMH,
        registered.MINUTES,
        registered.N_BOOT,
        registered.SEED,
    )


# ----------------------------------------------- a registered-shaped run on a tiny extract


def _write_registered_extract(directory: Path, fetch_buffer_m: float = 2_500) -> None:
    """A 250 m street grid over the registered AOI plus a 2.5 km margin, three clinics."""
    import numpy as np
    import pandas as pd

    from geonet.city import City
    from geonet.osm import save_extract

    xmin, ymin, xmax, ymax = registered.registered_aoi()
    m = registered.FETCH_MARGIN_M
    xs = np.arange(xmin - m, xmax + m + 1, 250.0)
    ys = np.arange(ymin - m, ymax + m + 1, 250.0)
    nid = {(i, j): i * len(xs) + j for i in range(len(ys)) for j in range(len(xs))}
    nodes = pd.DataFrame([(nid[i, j], xs[j], ys[i]) for i, j in nid], columns=["node_id", "x", "y"])
    rows = []
    for i, j in nid:
        for di, dj in ((0, 1), (1, 0)):
            if (i + di, j + dj) in nid:
                a, b = (xs[j], ys[i]), (xs[j + dj], ys[i + di])
                rows.append(
                    (nid[i, j], nid[i + di, j + dj], 250.0, "residential",
                     f"LINESTRING ({a[0]} {a[1]}, {b[0]} {b[1]})")
                )  # fmt: skip
    edges = pd.DataFrame(rows, columns=["u", "v", "length_m", "highway", "geometry"])
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    fac = pd.DataFrame(
        {
            "fid": ["node/1", "node/2", "way/3"],
            "kind": ["hospital", "clinic", "doctors"],
            "x": [cx, cx + 1_500.0, cx - 1_250.0],
            "y": [cy, cy - 750.0, cy + 1_000.0],
        }
    )
    city = City(nodes, edges, fac, registered.CRS, registered.registered_aoi())
    save_extract(city, directory, meta_extra={"fetch_buffer_m": fetch_buffer_m})


def _write_pop(path: Path, resolution: int, k: int) -> None:
    import h3
    import pandas as pd

    lon, lat = registered.CENTRE_LONLAT
    centre = h3.latlng_to_cell(lat, lon, resolution)
    cells = sorted(h3.grid_disk(centre, k))
    pd.DataFrame({"h3_id": cells, "pop": [100.0 + i for i in range(len(cells))]}).to_csv(
        path, index=False
    )


@pytest.fixture(scope="module")
def tiny_registered(tmp_path_factory):
    root = tmp_path_factory.mktemp("registered")
    _write_registered_extract(root / "osm")
    _write_pop(root / "pop_h3.csv", resolution=9, k=6)
    out = root / "access.json"
    code = cli.main(
        ["access", "--data", str(root / "osm"), "--pop", str(root / "pop_h3.csv"),
         "--out", str(out)]
    )  # fmt: skip
    assert code == 0
    return root, out


def test_registered_run_has_a_verdict_and_the_snap_fields(tiny_registered) -> None:
    _, out = tiny_registered
    s = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert s["registered"] is True
    assert s["verdict"] in {"supported", "null", "not_supported"}
    assert s["snap_method"] == "edge"
    assert set(s["node_snap_sensitivity"]) >= {"share_network", "gap_pp", "ci_cluster"}


def test_registered_spot_check_round_trip(tiny_registered, tmp_path, capsys) -> None:
    import pandas as pd

    _, out = tiny_registered
    report_dir = tmp_path / "report"
    assert cli.main(["report", "--access", str(out), "--out", str(report_dir), "--spot-check"]) == 0
    text = (report_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "not the registered analysis" not in text.split("## Summary")[0]

    sheet = report_dir / "spot_check.csv"
    df = pd.read_csv(sheet)
    assert len(df) == 20
    df["router_m"] = df["network_m"] * 1.05
    df.to_csv(sheet, index=False)
    capsys.readouterr()
    assert cli.main(["score-spot-check", str(sheet), "--access", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    df.loc[:5, "router_m"] = df.loc[:5, "network_m"] * 3  # six rows far off: 14 of 20 pass
    df.to_csv(sheet, index=False)
    assert cli.main(["score-spot-check", str(sheet), "--access", str(out)]) == 1
    assert "not validated by the spot-check" in capsys.readouterr().err


def test_resolution_10_run_is_not_registered(tmp_path, capsys) -> None:
    _write_registered_extract(tmp_path / "osm")
    _write_pop(tmp_path / "pop_h3_r10.csv", resolution=10, k=8)
    out = tmp_path / "access_r10.json"
    args = ["access", "--data", str(tmp_path / "osm"), "--pop", str(tmp_path / "pop_h3_r10.csv")]
    assert cli.main([*args, "--out", str(out), "--n-boot", "2000"]) == 0
    s = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert s["registered"] is False and s["verdict"] is None
    assert "not the registered analysis" in capsys.readouterr().out


def test_short_fetch_margin_warns_and_is_not_registered(tmp_path, capsys) -> None:
    _write_registered_extract(tmp_path / "osm", fetch_buffer_m=1_000)
    _write_pop(tmp_path / "pop_h3.csv", resolution=9, k=3)
    out = tmp_path / "access.json"
    code = cli.main(
        ["access", "--data", str(tmp_path / "osm"), "--pop", str(tmp_path / "pop_h3.csv"),
         "--out", str(out)]
    )  # fmt: skip
    assert code == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err and "fetch_buffer_m" in captured.err
    assert "not the registered analysis" in captured.out
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["registered"] is False
