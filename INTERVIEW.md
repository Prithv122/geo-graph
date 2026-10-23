# Interview Prep — Walkable Access

**Five questions, five answers.** Draft answers: read them, check you can say each one in your own
words, and change anything you would not defend.

---

### Q1. Walk me through the architecture in 90 seconds.

_A:_ Two one-off scripts fetch the inputs: the OpenStreetMap walking network and health facilities
for central Bengaluru plus a 2.5 km margin, and WorldPop's 100 m population raster summed to about
a thousand H3 hexagons. Everything is projected to UTM metres once and committed, so the analysis
never touches the network. `geonet access` builds an undirected graph, keeps the largest connected
part, and computes two distances per hexagon: straight line to the nearest facility with a KD-tree,
and walking distance by snapping each hexagon centre and each facility to the nearest street and
running one multi-source Dijkstra from all facilities at once. Both distances are compared with
nine thresholds, the population-weighted shares and their difference are computed, and a cluster
bootstrap gives the interval. The report step writes tables, maps and a spot-check sheet.

### Q2. Why did you snap to the nearest street instead of the nearest node, when the plan said node?

_A:_ A review before the real run found that the nearest node can be behind you. On a long street
with intersections at 0 and 1,000 m, a resident at 190 m walking to a clinic at 1,000 m is snapped
to the node at 0 and walks 1,190 m instead of 810 m. That error only ever lengthens walks, so it
would push the gap towards my own hypothesis. I changed the method in a dated amendment before any
real number existed and kept nearest-node as a sensitivity. In the end it barely mattered here
(2.89 vs 2.91 pp) because central Bengaluru's street segments are short, but I couldn't have known
that in advance, and the bias had a known direction.

### Q3. What's the weakest part, and what would break first?

_A:_ The inputs, not the code. OSM decides what counts as a clinic and which paths exist: a private
campus path mapped as public makes the network look better, and a missing footbridge makes it look
worse. The population sits at hexagon centres, not homes. The interval comes from 27 zones, which
is few for a bootstrap. At scale, the pure-Python Dijkstra and the Overpass download break first;
I'd move to a compiled router or a sparse-matrix shortest path, and regional OSM extracts.

### Q4. How do you know it works? What did you measure, against what?

_A:_ The baseline is the buffer method itself, since there's no ground truth for "access". On a
synthetic grid with a river and one bridge the walking distances match a closed form exactly, and
the buffer overstates by a known amount. The edge-snapping method matches a brute-force reference
that inserts every point into the graph, on random curved networks. A swap test feeds the
straight-line distance through the network slot and gets a gap of exactly zero, which shows that only
the distance method differs. An invariant check refuses any walk shorter than the straight line.
The committed result is rebuilt by a test and matches to 1e-9. Still open: 20 cells checked by hand
against the openstreetmap.org foot router, with a pass rule fixed in advance.

### Q5. Your hypothesis failed. Why should anyone care about a 2.9-point gap?

_A:_ Because the threshold, which analysts usually pick casually, decides the answer. At 15 minutes
the buffer says 100% and the street network 97%: about 31,000 people miscounted, concentrated in a
handful of neighbourhoods where the gap reaches 18–20 points. At 10 minutes the gap is about 10
points. The buffer saturates because the centre has 689 facilities in 100 km^2, so the honest
result is "the shortcut is fine for the city average at 15 minutes, wrong for specific
neighbourhoods, and wrong overall at tighter thresholds". I fixed 5 pp as the bar before looking,
so I report "not supported" rather than moving the threshold to 10 minutes after seeing 9.9.

---

## 30-second pitch

Straight-line circles are the standard shortcut for "who can walk to a hospital". I measured the
error against walking on OpenStreetMap streets for 1.06 million residents of central Bengaluru, with
the method pre-registered. At 15 minutes the circle says 100% and the street network 97.1%, a
2.9-point gap (CI 1.3–5.0), below my 5-point bar. But the error is concentrated in a few
neighbourhoods where it reaches 20 points, and at 10 minutes it is about 10 points citywide.
