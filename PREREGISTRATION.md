# Pre-registration — straight-line buffer vs walking network

Written and committed before any real-city access number was computed. Amendments go at the end,
dated, with the reason, and must be committed before the run they affect.

## Question

By how many percentage points does a straight-line walking buffer overstate the share of residents
with walking access to a hospital or clinic, compared with walking distance on the OpenStreetMap
street network, and where is the gap largest?

## Fixed for both arms

Only the distance method differs between the arms. Everything below is shared, and a test asserts
that swapping the network distance for the straight-line distance gives a gap of exactly zero.

| Item | Value |
|---|---|
| Study area (AOI) | Bengaluru, a 10 km x 10 km square centred on 12.9716 N, 77.5946 E, defined in EPSG:32643 (UTM 43N) metres |
| Fetch margin | Network and facilities are fetched 2.5 km beyond the AOI (more than the largest threshold, 2 km), so a resident near the edge can reach a facility outside it. Population is counted inside the AOI only |
| Destinations | OSM `amenity=hospital`, `amenity=clinic`, `amenity=doctors` (nodes, ways and relations; areas become their representative point). The snapshot date is recorded in `data/osm/meta.json`. Duplicates (a hospital mapped as a point and an area) are kept: they cannot change a nearest-facility distance |
| Population | WorldPop 100 m population raster for India (product and year recorded at fetch time, CC BY 4.0), summed to H3 resolution 9 cells by pixel centre. Cells with zero population are dropped |
| Origins | One per H3 cell: the cell centroid, projected to metres, weighted by the cell's population |
| Threshold | A distance `D = speed x minutes`. Primary: 5 km/h, 15 min, `D = 1,250 m` |

## The two arms

- **Buffer:** straight-line distance from the origin to the nearest facility `<= D`.
- **Network:** straight leg from the origin to its nearest network node, plus the shortest path on
  the walking network, plus the straight leg from the facility's nearest network node to the
  facility, minimised over facilities, `<= D`.

Network: the OSM walking network (the `osmnx` "walk" filter: no motorways, no private or
foot-forbidden ways), undirected, cut to its largest connected component. Edge lengths are measured
on the projected geometry, so a walking distance can never be shorter than the straight line. That
makes the gap non-negative by construction; the question is its size and where it falls.

## Outcome and decision rule

`gap = 100 x (share_buffer - share_network)`, in percentage points, where each share is the
population-weighted fraction of residents classified as having access.

95% CI: cluster bootstrap over H3 resolution-7 parent cells (about 5 km^2 each), 2,000 resamples,
seed 20261023, percentile interval. Neighbouring cells share streets and facilities, so resampling
single cells would understate the uncertainty. The cell-level bootstrap is reported as a
sensitivity. The CI expresses neighbourhood-to-neighbourhood variation, not sampling error: the AOI
is a full count, not a sample.

- **H1 supported:** point estimate `>= 5 pp` and CI lower bound `>= 2 pp`.
- **Registered null:** CI upper bound `< 2 pp` (a grid-like, permeable centre). Published as is,
  with the cells that still differ.
- **Otherwise:** not supported, reported as measured with its CI.

## Secondary, descriptive (no hypothesis)

1. Sensitivity grid: speed {4, 5, 6} km/h x time {10, 15, 20} min (D from 667 m to 2,000 m). 4 km/h
   for 15 min and 6 km/h for 10 min give the same D, so the same result; a test checks it.
2. Resolution: the same primary analysis on H3 resolution 10 cells.
3. Where: the gap per resolution-7 zone, the top 10 zones by gap and by residents affected, and a map
   of cells by status (both arms reach / buffer only / neither).
4. Circuity: the ratio of network to straight-line distance to the nearest facility, its median and
   90th percentile, population-weighted.
5. Snapping: the distribution of origin-to-node distances and the population share snapped more
   than 200 m.

## Validation

- Synthetic grid: network distances equal the closed form exactly; on a grid cut by a river with one
  bridge, the buffer overstates by a known amount.
- 20 spot-checks by the owner: a seeded random sample of 20 cells, each compared with an independent
  router (the openstreetmap.org foot router) for the same origin and nearest facility. Pass if at
  least 16 of 20 are within 15% of the router's distance. A miss is investigated and written up
  before the result is reported, not silently fixed.

## Threats to validity (stated in the README)

OSM completeness (missing facilities or footpaths, private campus paths mapped as public), snapping
error from cell centroids, one uniform walking speed, the 100 m raster and the H3 cell size, one
city and one AOI, and "access" defined as distance only (no opening hours, capacity or cost).

---

## Amendment 1 — 2026-10-23, before any real-city access number

Reason: an independent review of the code before the run found that snapping to the nearest
network **node** biases the network arm upward. On a long street between intersections, the nearest
node can be behind the resident relative to the route (origin at 190 m on a street with nodes at 0,
400 and 1,000 m, facility at 1,000 m: 1,190 m by nearest node against a true walk of 810 m). The bias
only lengthens network distances, so it would inflate the gap and lean the result towards H1. No
real-city distance, share or gap had been computed when this was written; the OSM extract was not
yet on disk.

Changes (everything else above stands):

1. **Snap to the nearest street, not the nearest node.** Each origin and each facility joins the
   network at the closest point on the nearest edge of the largest connected component (a straight
   leg), then walks along that edge to either end node. Network distance = origin leg + along-edge
   distance to an end node + shortest path + along-edge distance from the facility's edge to its
   end node + facility leg, minimised over both ends on each side; when an origin and a facility
   join the same edge, walking directly along it is also a candidate. Every term is still measured
   in projected metres, so the network distance can never be shorter than the straight line.
2. **The nearest-node method is kept as a reported sensitivity**, so the size of the snapping bias
   is visible, but it does not decide anything.
3. **H1, in words:** the straight-line buffer overstates the population share with walking access
   by at least 5 percentage points. "Hospital or clinic" means the three registered tags, including
   `amenity=doctors`.
4. **Circuity** is network distance to the network-nearest facility divided by straight-line
   distance to the straight-line-nearest facility (each arm's own answer to "how far is care"),
   population-weighted, cells with a zero straight-line distance excluded.
5. **Registered run:** speed 5 km/h, 15 min, 2,000 resamples, seed 20261023, the registered AOI
   (checked to within 1 m) and a fetch margin of at least 2,500 m. Any other setting still produces
   numbers but is labelled "not the registered analysis" and carries no verdict.
6. **Spot-check, made concrete.** The facility is the one the network arm chose. A row passes if the
   router's walking distance and ours differ by at most 15% **or** at most 100 m (the router joins
   the street differently, which matters on short routes). The sheet must be the seeded 20-cell draw
   from the registered `access.json`. If fewer than 16 of 20 pass, the verdict is still reported but
   marked "not validated by the spot-check", and every miss is investigated and written up in
   `NOTES.md` before the README reports the result.
7. **Display:** shares, the gap and its CI are shown with 2 decimals, and the verdict always uses
   the unrounded values.
8. **Stated limits:** the AOI holds about 27 resolution-7 zones, and a percentile bootstrap over so
   few clusters can still be too narrow; the resolution-10 sensitivity mostly measures how 100 m
   raster pixels fall into smaller cells, not finer population.
