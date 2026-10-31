# Expanded road runtime v1: private source topology

> **Superseded:** An independent review identified crossing-origin and split-validation gaps in these earlier artifacts. Use the separately pinned replacements and reproduction steps in [road review fixes](expanded-road-review-fixes-v3.md). The figures below document the earlier run; its reproduction commands no longer describe the current scripts.

This converts the pinned expanded-road candidate into a `RoadNetwork`-compatible
**topology diagnostic** across the entire corrected 84 × 59 map. It does not
qualify the map or enable ground operations. The graph and source-derived
geometry are private under `/tmp`; do not commit, publish, or serve them.
© OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright);
see [license status](license-status.md) and [extended access](extended-access-v1.md)
for attribution, source limitations and rights review.

The input is `/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz`,
compressed SHA-256 `976875d58d2d134ba1f5d2aa62030f23b567f56d0bf98dd9820f88a2cb3ae343`.
The source manifest identity is
`d331a7fd511ba9ebbd767a85d78dccb7c7e68eaef6a4d4294ab183949e6586e9`.
The complete request footprint is not a complete mapped network. The 2026
multi-tile OSM source is non-atomic, not a historical 2021 observation, and
may omit crossing ways. Its direction and access tags are not proof of legal,
current, or fire-truck-suitable travel.

The converter joins contiguous directed segments only on the same source way,
with matching direction and class, through nodes incident to exactly two
segments on that way. All source junctions, crossings with distinct source
nodes, separate components, original coordinate vertices and source directions
are preserved. It leaves closed source ways uncontracted. No line is simplified,
no boundary segment is added, and no external path, fabricated connector or
geometric crossing is created. Length is the sum of the original projected
segments. Each compact edge stores its source way, segment range and direction;
its speed is the assumed class speed in `eval/prepare_coastal_roads.py`, not a
measurement. Source arcs cannot include outside endpoints: the rectangular
domain is convex, so the straight source segments remain inside it.

The candidate's exclusions remain intact: all ways referenced by retrieved
restriction relations and all arcs at restriction via nodes are omitted,
along with barriers and source-policy exclusions. Three restriction relations
have missing source members; these remain unresolved and referenced identifiers
are excluded as far as known. Exclusion can block real roads and does not prove
all relevant restrictions have been retrieved. Unsupported or conditional
access and unsuitable classes are excluded by the source policy. A lack of an
OSM access tag is not affirmative vehicle evidence.

Every edge has `allowed_modes: []` because the candidate supplies no affirmative
crew or engine access evidence. The expanded host's explicit road-mode filter
therefore admits no road edge. `RoadNetwork.path` itself does **not** enforce
`allowed_modes`: its paths are topology diagnostics only and must not be used
as mission routes. Nor does a road crossing a cell establish that the cell is
accessible. The current expanded source inputs retain all access cells as
unknown; starting or routing a ground resource remains blocked. The scenario
catalog's old road-edge whitelist also references the old graph and cannot be
reused for this graph. Do not copy or widen it without independent evidence.
No scenario inputs, whitelists or access masks are changed here.

For this pinned source, the private network has 15,325 nodes, 27,284 directed
edges, 196,232 coordinate vertices and a 15,474,449-byte compact JSON file.
The graph digest is
`75e413de6d45a43a385ccd74cc4f08d701e7bad9dbee2c9a82dab990537e9ee1`.
It retains all 168,948 candidate arcs and all 511 weak components. The largest
runtime strongly connected component contains 5,523 retained nodes. Contracted
intermediate source nodes are still geometric vertices but are no longer graph
endpoints; this changes available snapping and staging points, even though
source-path reachability and travel cost between retained junctions are
preserved. The router's bounded searches and safety checks may impose further
limits; full-map operational performance and complete mission availability are
not established by this conversion.

An independent raw-source audit checked request manifests, tile hashes,
restriction records, tags, directed source segments, boundary exclusions and
projected geometry with GEOS. Separately, the new validator expands every
runtime edge into original directed arcs, checks exact geometry, cost, class,
empty modes and intermediate-node topology, and compares the complete arc set.
Tests compare directed shortest-path costs against an independently traversed
uncontracted source graph, exercise a one-way branch and an unconnected
geometric crossing, and reject graph mutations. These checks validate faithful
conversion of the retrieved candidate, not correctness or completeness of OSM.

## Original resource posts

The private original twelve-case catalog was checked against its catalog SHA
`2b264d9668f0585a560d82af716f794ed386d6a7cdc713b24987269d647b33fc`
and individual case digests. Its 133 ground-resource occurrences use two
unique base positions. Their nearest retained runtime nodes are approximately
0.03 m and 102.70 m away, respectively; both are within 150 m. The two nearest
nodes have a source-topology path in both directions, each approximately
373.4 m, but neither lies in the largest runtime strongly connected component.
These distances and paths neither establish a connector from a post nor show
that either post, route, cell or resource is safe or accessible. No aircraft
post is included in this ground-road distance assessment.

## Reproduction

The original reproduction command targeted `/tmp/expanded-road-runtime-v1`;
that version is retained for history and must not be overwritten. The current
write-once commands and corrected output paths are in [road re-review fixes](expanded-road-review-fixes-v3.md).
