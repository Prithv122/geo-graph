# Build Notes — Walkable Access

Working notes: what broke, what was tried, why X over Y.

---

## Log

### 2026-10-23 — scaffold, pre-registration, contracts
- Option A (geo) from the approved brief: straight-line buffer vs walking network, one city.
- Pre-registration committed with the core modules, before any data was fetched.
- **Chose a cluster bootstrap (H3 res-7 parents) as the primary CI** over the brief's per-cell
  bootstrap. Neighbouring cells share streets and facilities, so treating them as independent
  draws would give an interval that is too narrow. The per-cell CI is kept as a sensitivity.
- **Facility snap leg counted in the network arm.** Origin -> nearest node (straight) + path +
  facility's nearest node -> facility (straight). Leaving the facility leg out would let the network
  arm be shorter than the straight line for a facility far from any street, and the two arms would
  no longer differ only in the distance method.
- **Edge lengths from projected geometry**, not the lengths OSM tools report (geodesic). Mixing the
  two metrics would let a network distance come out a few centimetres under the straight line and
  break the invariant `network >= straight line` that the tests check.
- **Fetch margin of 2.5 km** around the AOI for network and facilities: a resident at the AOI edge
  may walk to a hospital just outside it. Population is still counted inside the AOI only.
- **No Dijkstra cutoff**: one labelled multi-source Dijkstra from all facilities gives every
  origin's network distance once, so the nine sensitivity thresholds are just comparisons, and the
  circuity ratio is defined for cells beyond the threshold too.
- Gap is non-negative by construction (the network can only be longer). The registered question is
  its size and where it falls, not its sign.

### 2026-10-23 — review, amendments, data, registered run
- **Pre-run review (fresh context) found a one-directional bias:** nearest-node snapping can put a
  resident behind their own route (190 m along a street with nodes at 0/400/1,000 m: 1,190 m instead
  of 810 m). It only lengthens walks, so it inflates the gap towards H1. Amendment 1 (committed
  before any real number) moved to nearest-street snapping and kept nearest-node as a sensitivity.
  Result: 2.89 vs 2.91 pp, so it barely mattered here (short edges in a dense centre), but the
  direction of the bias was known and the size was not.
- Same review: the run did not enforce the registered settings (now `registered` gates the verdict),
  `network >= straight line` was reported but not enforced (now raises), the AOI and fetch margin
  were never checked (now checked against `registered.py`), and the spot-check had no consequence
  for failing (now: verdict marked "not validated").
- **Amendment 2:** the first registered run stopped at input validation: 52 boundary cells hold
  population from pixels inside the AOI but have centroids just outside it. Allowed origins up to
  250 m outside the AOI. Nothing had been computed.
- Overpass returned 502 "at capacity" twice; the fetch script backs off 60/180/420 s. WorldPop's
  server resets the default python-requests user agent and ignores Range requests, so the 778 MB
  raster is downloaded whole with a descriptive user agent naming the project.
- osmnx's `length` is geodesic; lengths are recomputed from the projected geometry so the
  `length >= chord` rule holds on all 218k edge rows.
- **Result:** buffer 100.00%, network 97.09%, gap 2.91 pp [1.28, 4.98] -> H1 not supported (and not
  the null). The buffer saturates: the farthest cell centroid is 1,221 m from a facility, under the
  1,250 m threshold, with 689 facilities in 100 km^2. At 833 m the gap is 9.89 pp.
- Rerun is byte-identical; `tests/test_results.py` rebuilds the headline from the committed data.

---

## Rejected approaches

| Approach | Why rejected |
|---|---|
| Option B (betweenness vs closure impact) | Owner chose A at brief approval: an analyst-facing result with a map, and the mistake it measures is common |
| Cutoff Dijkstra per threshold | Nine runs instead of one, and no circuity beyond the threshold |
| Per-cell bootstrap as the primary CI | Spatially correlated cells make it too narrow; kept as a sensitivity |
| Nearest-node snapping | One-directional bias on long edges (Amendment 1); kept as a sensitivity |
| Moving the primary threshold to 10 min after seeing 9.9 pp | That is choosing the answer; 15 min was registered |

## Open questions

- [ ] Owner spot-checks (20 cells against the openstreetmap.org foot router): `results/spot_check.md`.
- [ ] What is in the top-gap zones (closed grounds, rail, campuses)? Not checked on the ground.
- [ ] A sparser area (a suburb) is where the 5 pp bar could be crossed; one AOI only here.
