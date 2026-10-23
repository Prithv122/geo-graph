# Walkable Access — F4

**Tier:** 3 · **Category:** F - Querying & analysis · **Wave:** 4

The project's own engineering guide. Keep it short and current.

## What this is

A method comparison. Analysts often count "people within 15 minutes' walk of a hospital" with a
straight-line circle. This project measures how far that overstates access compared with walking
distance on the OpenStreetMap street network, for one city (Bengaluru), weighted by population.
The question, the decision rule and every fixed choice are in `PREREGISTRATION.md`.

## Stack

Python 3.12 · networkx (Dijkstra) · shapely + pyproj (projected geometry) · h3 (population cells) ·
scipy (KD-tree snapping) · pandas/pyarrow. Local-only extras: `fetch` (osmnx, rasterio, requests)
for the one-off data download, `maps` (matplotlib) for the figures.

## Acceptance criteria

- [x] Both arms share destinations, study area, population weights and threshold; a test asserts
      that only the distance method differs.
- [x] Network share equals the closed form on the synthetic grid; buffer overstates on the grid
      with a river.
- [x] Real run on the committed Bengaluru extract: gap in pp with a 95% cluster-bootstrap CI,
      the 3 x 3 sensitivity grid, the per-zone gap map, and the H1 verdict as registered.
- [ ] Runs from a clean clone using only the documented steps; tests, lint and CI green.

## Module contracts

All x/y are metres in the extract's projected CRS (Bengaluru: EPSG:32643, UTM 43N).

| Module | Owns |
|---|---|
| `city.py` | `City(nodes, edges, facilities, crs, aoi)` and the column schemas |
| `synthetic.py` | `synthetic_city(...) -> (City, origins)`, closed-form distances |
| `network.py` | `build_graph` (undirected, `length_m` >= chord, largest component), `EdgeSnapper` (nearest street), `Snapper` (nearest node, sensitivity) |
| `osm.py` | `load_extract(dir) -> City`, `save_extract(city, dir)`, `to_projected` / `to_lonlat` |
| `hexgrid.py` | `load_pop(csv)`, `cells_to_origins(pop_df, crs, parent_res=7) -> origins` |
| `registered.py` | the registered constants and the registered-run decision |
| `access.py` | `buffer_distance_m`, `network_distance_m` (street snapping), `run_access(...) -> dict` |
| `stats.py` | `weighted_share`, `gap_pp`, `cluster_bootstrap_ci` |
| `report.py` | markdown tables, maps (`maps` extra), the spot-check sheet |
| `cli.py` | `geonet build`, `geonet access`, `geonet report` |

Formats:
- `data/osm/nodes.parquet {node_id, x, y}`, `edges.parquet {u, v, length_m, highway, geometry}`,
  `facilities.parquet {fid, kind, x, y}`, `meta.json {crs, aoi, fetch_buffer_m, source, fetched_at}`.
- `data/pop_h3.csv {h3_id, pop}` (res 9), `data/pop_h3_r10.csv` (res 10, sensitivity).
- Origins (in memory): `{cell_id, x, y, pop, cluster}`.
- `results/access.json {config, cells[...], summary, sensitivity[...]}`; schema in `access.py`.

## Conventions

- `uv` for dependencies; `ruff check` + `ruff format`; `pytest`.
- Distances are only ever computed in projected metres. Edge lengths come from projected geometry,
  so walking distance can never be shorter than the straight line (a test checks it).
- No numbers in the README that a committed command does not reproduce.

## Project-specific notes

- Data downloads (`scripts/fetch_osm.py`, `scripts/fetch_pop.py`) run locally once; their outputs
  are committed (a few MB) so CI and clean clones never touch the network.
- OpenStreetMap data is ODbL; the committed extract stays ODbL (`data/ATTRIBUTION.md`).
- Local pytest needs `--basetemp` pointing at a writable folder on this machine.
