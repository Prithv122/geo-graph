"""One-off WorldPop download, summed to H3 cells inside the study area.

Downloads the WorldPop R2025A constrained 100 m population raster for India (2025), reads only the
window around the study area, assigns every pixel whose centre lies inside the AOI to an H3 cell by
that centre's latitude/longitude, and writes data/pop_h3.csv (res 9) and data/pop_h3_r10.csv
(res 10). The study area is registered in PREREGISTRATION.md; aoi.py holds the numbers.

    uv run python scripts/fetch_pop.py --dry-run
    uv run python scripts/fetch_pop.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import aoi as study
import numpy as np
import pandas as pd

PRODUCT = "WorldPop Global2 R2025A v1, individual countries 2015-2030, constrained, 100 m"
YEAR = 2025
URL = (
    "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/IND/v1/100m/"
    "constrained/ind_pop_2025_CN_100m_R2025A_v1.tif"
)
LISTING = "https://hub.worldpop.org/geodata/listing?id=135"
LICENCE = "CC BY 4.0"
CITATION = (
    "Bondarenko M., Priyatikanto R., Tejedor-Garavito N., Zhang W., McKeen T., Cunningham A., "
    "Woods T., Hilton J., Cihan D., Nosatiuk B., Brinkhoff T., Tatem A., Sorichetta A. "
    "Constrained estimates of 2015-2030 total number of people per grid square at a resolution "
    "of 3 arc (approximately 100m at the equator) R2025A version v1. Global Demographic Data "
    "Project - Funded by The Bill and Melinda Gates Foundation (INV-045237). WorldPop - School "
    "of Geography and Environmental Science, University of Southampton. "
    "DOI:10.5258/SOTON/WP00839"
)
LARGE_FILE_BYTES = 3 * 1024**3
EXPECTED_BYTES = 778_106_191  # Content-Length reported by the server on 2026-10-23
CHUNK = 1024 * 1024
# data.worldpop.org resets connections that carry the default library user agent; a
# descriptive one that names the project is accepted.
HEADERS = {"User-Agent": "geo-graph-data-fetch/0.1 (+https://github.com/Prithv122/geo-graph)"}
RESOLUTIONS = (9, 10)


def download(url: str, dest: Path) -> None:
    """Streaming download into dest (via dest.part). Resumes if the server honours Range."""
    import requests

    if dest.exists() and dest.stat().st_size == EXPECTED_BYTES:
        print(f"already downloaded: {dest}", flush=True)
        return
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = dict(HEADERS)
    if have:
        headers["Range"] = f"bytes={have}-"
        print(f"trying to resume at {have / 1e6:.1f} MB", flush=True)
    # no separate HEAD request: this server resets python HEAD requests
    with requests.get(url, headers=headers, stream=True, timeout=(30, 120)) as r:
        r.raise_for_status()
        if have and r.status_code != 206:
            have = 0  # the server ignored the range request: start over
        length = int(r.headers["Content-Length"])
        total = have + length
        if total > LARGE_FILE_BYTES:
            raise SystemExit("raster is over 3 GB: switch to a /vsicurl/ windowed read")
        print(f"remote size: {total / 1e6:.1f} MB", flush=True)
        with part.open("ab" if have else "wb") as f:
            done, last = have, time.time()
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 15:
                    print(f"  {done / 1e6:.0f} / {total / 1e6:.0f} MB", flush=True)
                    last = time.time()
    if part.stat().st_size != total:
        raise RuntimeError(f"incomplete download: {part.stat().st_size} of {total} bytes")
    part.replace(dest)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def read_aoi_pixels(raster: Path, aoi: tuple[float, float, float, float]):
    """Pixel centres (lon, lat) and population for pixels whose centre is inside the AOI."""
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window, from_bounds

    xmin, ymin, xmax, ymax = aoi
    with rasterio.open(raster) as src:
        info = {
            "crs": str(src.crs),
            "res": tuple(float(v) for v in src.res),
            "nodata": src.nodata,
            "shape": (src.height, src.width),
        }
        # AOI corners and edge midpoints in the raster CRS, padded by 3 pixels
        xm, ym = (xmin + xmax) / 2, (ymin + ymax) / 2
        pts = [
            (xmin, ymin), (xm, ymin), (xmax, ymin), (xmax, ym),
            (xmax, ymax), (xm, ymax), (xmin, ymax), (xmin, ym),
        ]  # fmt: skip
        to_raster = Transformer.from_crs(study.CRS, src.crs, always_xy=True)
        rx, ry = zip(*(to_raster.transform(x, y) for x, y in pts), strict=True)
        win = from_bounds(min(rx), min(ry), max(rx), max(ry), src.transform)
        col0 = int(np.floor(win.col_off)) - 3
        row0 = int(np.floor(win.row_off)) - 3
        col1 = int(np.ceil(win.col_off + win.width)) + 3
        row1 = int(np.ceil(win.row_off + win.height)) + 3
        window = Window(col0, row0, col1 - col0, row1 - row0)
        data = src.read(1, window=window, masked=False).astype("float64")
        transform = src.window_transform(window)
        nodata = src.nodata

    bad = ~np.isfinite(data)
    if nodata is not None:
        bad |= data == nodata
    n_nodata = int(bad.sum())
    data[bad] = 0.0
    data[data < 0] = 0.0

    rows, cols = np.indices(data.shape)
    lon, lat = rasterio.transform.xy(transform, rows.ravel(), cols.ravel(), offset="center")
    lon, lat = np.asarray(lon), np.asarray(lat)
    x, y = Transformer.from_crs(info["crs"], study.CRS, always_xy=True).transform(lon, lat)
    inside = (x >= xmin) & (x < xmax) & (y >= ymin) & (y < ymax)
    info["window"] = (col0, row0, col1 - col0, row1 - row0)
    info["window_nodata_pixels"] = n_nodata
    return lon[inside], lat[inside], data.ravel()[inside], info


def to_h3(lon, lat, pop, res: int) -> pd.DataFrame:
    import h3

    cells = [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon, strict=True)]
    df = pd.DataFrame({"h3_id": cells, "pop": pop}).groupby("h3_id", as_index=False)["pop"].sum()
    df["pop"] = df["pop"].round(4)
    df = df[df["pop"] > 0].sort_values("h3_id").reset_index(drop=True)
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw", default="data/raw", help="folder for the raw raster (git-ignored)")
    p.add_argument("--out", default="data", help="folder for pop_h3.csv and pop_h3_r10.csv")
    p.add_argument("--dry-run", action="store_true", help="print the plan, no download")
    args = p.parse_args(argv)

    aoi = study.aoi_bounds()
    raster = Path(args.raw) / Path(URL).name
    print(f"CRS      : {study.CRS}")
    print(f"AOI (m)  : xmin={aoi[0]:.0f} ymin={aoi[1]:.0f} xmax={aoi[2]:.0f} ymax={aoi[3]:.0f}")
    print(f"Product  : {PRODUCT}, year {YEAR}, licence {LICENCE}")
    print(f"URL      : {URL}")
    print(
        f"Plan     : download (about 742 MB, below the 3 GB limit for a windowed read) to {raster};"
    )
    print("           SHA-256; read only the AOI window; pixel centre in AOI -> H3 res 9 and 10;")
    print(
        f"           write {Path(args.out) / 'pop_h3.csv'} and {Path(args.out) / 'pop_h3_r10.csv'}"
    )
    if args.dry_run:
        print("Dry run: nothing downloaded.")
        return 0

    Path(args.raw).mkdir(parents=True, exist_ok=True)
    Path(args.out).mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    download(URL, raster)
    t_dl = time.time() - t0
    digest = sha256(raster)
    print(f"downloaded in {t_dl:.0f} s; SHA-256 {digest}", flush=True)

    lon, lat, pop, info = read_aoi_pixels(raster, aoi)
    print(f"raster CRS {info['crs']}, resolution {info['res']}, nodata {info['nodata']}")
    print(
        f"window (col, row, w, h): {info['window']}, nodata pixels in it: "
        f"{info['window_nodata_pixels']}"
    )
    print(f"pixels with centre inside the AOI: {len(pop)}; with pop > 0: {int((pop > 0).sum())}")
    print(f"total population in the AOI (raw pixels): {pop.sum():.4f}")

    totals = {}
    for res in RESOLUTIONS:
        df = to_h3(lon[pop > 0], lat[pop > 0], pop[pop > 0], res)
        name = "pop_h3.csv" if res == 9 else "pop_h3_r10.csv"
        df.to_csv(Path(args.out) / name, index=False, float_format="%.4f")
        totals[res] = float(df["pop"].sum())
        print(f"H3 res {res}: {len(df)} cells, total {totals[res]:.4f} -> {name}")
    print(f"res 9 vs res 10 total difference: {abs(totals[9] - totals[10]):.4f}")

    record = {
        "aoi": list(aoi),
        "citation": CITATION,
        "downloaded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "licence": LICENCE,
        "listing": LISTING,
        "mode": "full download",
        "pixels_in_aoi": len(pop),
        "product": PRODUCT,
        "raster_crs": info["crs"],
        "sha256": digest,
        "size_bytes": raster.stat().st_size,
        "total_population": float(pop.sum()),
        "url": URL,
        "year": YEAR,
    }
    (Path(args.raw) / "pop_source.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
