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

---

## Rejected approaches

| Approach | Why rejected |
|---|---|
| Option B (betweenness vs closure impact) | Owner chose A at brief approval: an analyst-facing result with a map, and the mistake it measures is common |
| Cutoff Dijkstra per threshold | Nine runs instead of one, and no circuity beyond the threshold |
| Per-cell bootstrap as the primary CI | Spatially correlated cells make it too narrow; kept as a sensitivity |

## Open questions

- [ ] Owner spot-checks (20 cells against the openstreetmap.org foot router).
