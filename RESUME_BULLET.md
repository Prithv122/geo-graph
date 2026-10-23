# Resume Bullets — Walkable Access

Form: **action → technical specifics → measured outcome.** Draft; the owner picks the wording.

---

## Bullets

- Measured how much straight-line buffers overstate walking access to healthcare for 1.06M residents
  of central Bengaluru, using OpenStreetMap streets (78k nodes), WorldPop population on H3 cells and a
  pre-registered decision rule: a 2.9 pp gap at 15 minutes (95% cluster-bootstrap CI 1.3–5.0, below
  the 5 pp bar), concentrated in neighbourhoods where it reaches 20 pp, and 9.9 pp at 10 minutes.
- Caught and fixed a one-directional bias before the real run (nearest-node snapping lengthens walks,
  which inflates the gap) by snapping to the nearest street segment; validated against a closed-form
  grid, a brute-force graph reference, and a test that reproduces the committed results to 1e-9.

## Which roles this supports

- [x] Data Scientist / ML
- [ ] AI Engineer (LLM/NLP/CV)
- [x] Data Engineer
- [x] Data Analyst / Python Developer

## Keywords this project earns

Geospatial analysis · OpenStreetMap · network analysis (Dijkstra, multi-source shortest paths) ·
H3 · coordinate reference systems (UTM) · shapely / pyproj · population weighting · cluster
bootstrap · pre-registration · sensitivity analysis
