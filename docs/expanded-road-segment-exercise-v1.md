# Opt-in road-segment water exercise v1

This is a separately versioned, **low-confidence synthetic exercise rule** for
four exact directed source-road edges used by the twelve original population
cases. It assumes that an unmapped water crossing is absent **on each listed
edge only**. It does not classify any 500 m cell as accessible, authorize
walking or off-network connectors, or modify the strict censored baseline. It
is not a finding of legal, flood, vehicle-clearance or actual 2021 conditions.

The user-authorized assumption is broader than the mapped observations: the
available OSM acquisition can omit ways crossing request boxes, and the DERA
river layer contains identified named courses. The 50 m vector screen, 10 m
WorldCover pixels and mapped coastline faces are not proof of physical
clearance or calibrated position-error bounds. OSM and DERA are separate mapped
sources; their errors are not asserted to be statistically independent.

## Rule and provenance

The compiler requires `--opt-in-no-unmapped-crossings` and fixed source,
reviewed graph/source pair, original catalog, population-anchor, water-v2 and
v4 overlay pins. It reruns the independent raw OSM road acquisition audit,
reconstructs the source and exercise graph and exact-node population anchors,
checks every original case hash, and reconstructs both water and coastline
layers from their pinned raw inputs. Its private edge inventory includes the
source way and segment range, direction, source endpoints, exact geometry hash,
engine-mode flag, road structure/access tags, source-node structure counts and
screen evidence. It does not embed road coordinates; the pinned graph is the
geometry source.

An edge is eligible **only as an exercise assumption** when all of these hold:

- It is a directed `engine`-mode edge on a preserved source-graph path between
  original same-node anchors. Both endpoints are checked by the origin guard.
- Its complete geometry is valid and simple, and its 50 m corridor remains
  inside both DERA and OSM land-side coastline faces and the map bounds. The
  road stays more than 250 m plus the corridor width from either coastline.
- Neither the retrieved OSM waterway/open-area/area geometries nor the pinned
  DERA river/area geometries intersects or comes within 50 m of the road.
  Boundary contact is rejected. A missing layer or invalid reconstruction
  fails closed.
- Every 10 m WorldCover 2021 pixel touched by the 50 m corridor is classified
  as one of the supported land-cover classes. Water, wetlands, NoData and
  unsupported classes are rejected. An extra 0.01 m raster corridor margin
  conservatively accommodates the separate graph and raster PROJ runtimes;
  this is not a validated positional-error bound.
- Every 500 m cell touched by the road is in the pinned v4 mixed exercise
  overlay and is not an additional DERA censor. This scopes the edge hypothesis;
  it does not promote those cells. A tagged bridge, tunnel or ford is left
  unknown in this version, even if there is no mapped conflict. In particular,
  no structure tag overrides a mapped or ambiguous crossing.

A rejected edge remains `unknown`; a path containing one is not enabled. The
assumption applies only to the graph/source/anchor/water pair and edge inventory
pinned in the candidate. It does not authorize other edges, off-network
movement, return from an untracked mid-edge position, or a synthetic water
crossing through a mapped line or polygon. A fixed candidate digest prevents a
caller from substituting a rehashed candidate. The self-hash and tests are not
an external review or publication approval; any changed source, graph, policy
or edge inventory needs a new version and independent review.

## Result for the pinned cases

The private [r4 candidate](/tmp/expanded-road-segment-exercise-v1-r4/candidate.json)
has digest
`82bed516b5bd42ec4cd709035eacbd40c7f9cbf3a4fa6aa385ec5e6abc50c615`.
All four listed directed edges pass the rule under the explicit synthetic
assumption. They support 47 original directed group-to-destination graph paths;
the 48th combination already has the group at the destination node and needs
no movement. These are not 47 independent roads or surveys: the edges belong to
one mapped source way. Their origin cells remain mixed and source cell access
remains unknown. Flood, legality, vehicle suitability, historical water and
road status remain unknown for every result.

`SegmentExerciseRoutes` is a private opt-in route adapter for this exact pinned
candidate. It returns only the recorded full-edge engine paths, checks the
caller's **current** origin against the exact original source node, guards each
path endpoint against ambiguous source topology, and checks every road segment
against caller-supplied burning cells, wind and dynamic exclusions. It emits no
walk or connector legs. The one same-node case returns `no_movement`; a moved,
mid-edge or ambiguous origin returns no option. Its returned coordinates are
private source-derived driver data and must not be exposed to an actor.

The caller must supply current, independently supported fire and restriction
information. An empty hazard or exclusion set in a test is a synthetic fixture,
not evidence that the source map has no fire, flood or closures. The adapter
is **not wired into the existing host**. The host currently applies a whole-cell
unknown/mixed access guard, so it will continue to reject these routes until a
separately reviewed integration carries the exact-edge exception through route
selection, movement revalidation, evacuation lifecycle and replay/checkpoints.
Such integration must keep the original cell and walking masks unchanged and
must not generalize this assumption to any other edge or any other part of a
mixed cell.

## Reproduce and test

The graph and geometry audits run with the base Python/PROJ environment. The
pinned WorldCover check runs in the local raster-capable Detectree interpreter
because using its different PROJ version for the exact graph audit fails the
existing road-source equality check. The observed audit runtime was pyproj
3.8.0 / PROJ 9.8.1 and Shapely 2.1.2 / GEOS 3.13.1; the raster worker used
pyproj 3.7.0 / PROJ 9.5.0, Shapely 2.0.6 / GEOS 3.13.0 and rasterio 1.4.2.
These are local observations, not a dependency lock. The raster worker receives
its private corridor through stdin, validates the raster hash and contract,
and writes no source geometry. Missing runtimes or data fail closed.

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_road_segment_exercise_v1.py \
  --opt-in-no-unmapped-crossings --output-dir /tmp/expanded-road-segment-exercise-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_road_segment_exercise_v1.py -q -p no:cacheprovider
```

The output directory must be a fresh dedicated direct child of `/tmp`; it is
mode 0700 and its candidate is mode 0600. Existing artifacts are not replaced.
Do not serve `/tmp` or this directory, and do not commit or publish the
source-derived candidate. © OpenStreetMap contributors, ODbL; DERA © IECA,
CC BY 4.0; ESA WorldCover 2021 / Copernicus Sentinel data. See
[license status](license-status.md) and [extended access](extended-access-v1.md)
for the complete rights and source limitations.

Tests reject mapped line and polygon conflicts, near-water buffers, ambiguous
land/coast/raster evidence, tagged structures, missing mode and mixed-cell
support, and invented geometry. The private-source test brute-checks all four
edge geometries against both full water layers and independently reconstructs
all 47 original paths. Route-adapter tests check all 47 exact paths, the
same-node case, dynamic exclusion, ambiguous origin, altered pins and no-walk
geometry. These tests validate the synthetic rule's implementation; they do
not establish physical or legal safety.
