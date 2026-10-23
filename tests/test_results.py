"""The committed results must come out of the committed data and code, number for number."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geonet.cli import main

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "results" / "access.json"
DATA = ROOT / "data" / "osm"
POP = ROOT / "data" / "pop_h3.csv"

pytestmark = pytest.mark.skipif(
    not (COMMITTED.exists() and DATA.exists() and POP.exists()),
    reason="committed data or results not present",
)


def test_registered_run_reproduces_committed_results(tmp_path: Path) -> None:
    out = tmp_path / "access.json"
    code = main(["access", "--data", str(DATA), "--pop", str(POP), "--out", str(out)])
    assert code == 0

    fresh = json.loads(out.read_text(encoding="utf-8"))
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert fresh["summary"]["registered"] is True
    assert fresh["summary"]["verdict"] == committed["summary"]["verdict"]
    for key in ("share_buffer", "share_network", "gap_pp", "pop_affected", "circuity_median"):
        assert fresh["summary"][key] == pytest.approx(committed["summary"][key], abs=1e-9)
    assert fresh["summary"]["ci_cluster"] == pytest.approx(
        committed["summary"]["ci_cluster"], abs=1e-9
    )
    assert [r["gap_pp"] for r in fresh["sensitivity"]] == pytest.approx(
        [r["gap_pp"] for r in committed["sensitivity"]], abs=1e-9
    )
