# Source-backed road staging feasibility v1

`eval/expanded_road_staging_v1.py` builds a private, bounded **planning
hypothesis**, not a ground-work authorization, calibrated safety assessment,
full source episode or score. It leaves the strict censored baseline, existing
scenario catalogs and all whole-cell access and walking masks unchanged. The
explicit opt-in assumes no unmapped water crossing **only on the exact listed,
individually screened road edges**. Flood, legality, current vehicle suitability,
road condition and 2021 status remain unknown.

The compiler reconstructs the pinned original twelve cases, their separate
expanded-fire drafts and same-OSM-node ground-post drafts. It reconstructs v4
mixed inputs and synthetic weather/fire warm-up under the existing nominal
factor and 0.9 mixed-crossing proxy. The initial planning fire is exactly the
source-air slice's **partial public handover observation**: the first half of
the sorted modeled burning cells after warm-up. It is not the entire true fire,
a historical report or a guarantee that a later ground host will have the same
observation. No future weather schedule or private full fire is used to select
routes. The 0.9 proxy and nominal fuel factors are uncalibrated exercise
assumptions.

For each case the search considers at most 12 retained source graph nodes,
ranked deterministically. A node must be within four 500 m grid cells of the
sector and three cells of at least one initially reported fire cell, outside
the existing one-cell and downwind synthetic fire buffer, on a v2 land or
v4 admitted mixed cell with source fuel and finite pinned terrain, and have
unambiguous source-node identity with incoming and outgoing engine/crew road
modes. These distances are bounded selection heuristics, not proof a crew can
reach a fire or perform work. Up to two initially available ground-unit/node
combinations are retained per case. A source node is a road staging point only;
a hold location, line-work reach and ground-work safety remain unknown.

Each proposed outbound and return is made only from complete, directed,
source-backed edges that permit both engine and crew exercise modes. No walking,
partial edge, snap or connector is added. Edges are screened against the
pinned OSM and DERA water layers, corroborated coastal land faces, tagged
bridge/ford/tunnel evidence, a 50 m mapped-water corridor and 10 m WorldCover
land-cover samples using the earlier segment exercise screen. The 50 m/10 m
rules are uncalibrated filters, not proof of clearance. Every touched road cell
must be in the supported v2-land or v4-mixed scope, have source fuel and finite
coarse terrain, and not be an additional v4 censor. This does not establish
road grade, vehicle clearance, whole-cell safety or land access.

The shortest permitted source-graph search uses the existing frozen router's
10,000-visit bound. Each unit/node pair has at most four reroutes after a
screened edge is rejected; at most 100 distinct edges are screened across the
run. If no route is found after a complete search within this scope, the result
is `blocked`; if a node, reroute, visit or screening limit prevents a complete
search, or handover or required evidence is unavailable, it is
`search_incomplete_unknown`. A found candidate remains a hypothesis even when
the audit notes an incomplete search. If an earlier case cached unavailable edge
evidence, later searches conservatively carry that uncertainty because excluding
the edge may hide a route; missing OSM or DERA water layers count as unavailable
evidence. Neither result proves another route or
work site does not exist. `revalidate_path` is a
separate testable road-only check for exact directed edges, the supplied
independently trusted screened-edge set, current fire, wind and dynamic
exclusions. A later host must bind the candidate and all implementation/source
pins, supply authoritative current state, and revalidate outbound, movement,
return and work separately. It must not treat the initial public observation as
true live fire or infer walking access from a road.

## Private results

The candidate and audit are kept in a dedicated mode-0700 directory with
mode-0600 files. They contain private case statuses, source-node and observed-fire
information and must not be committed, served or exposed to an actor. Catalog
summaries and per-case results stay private; compiler stdout reports completion
only. A blocked or unknown entry is retained rather than filled with a fabricated
connector. This is not an incident evaluation or a claim of successful dispatch
or useful work.

## Reproduction and validation

The source and graph audit uses the base Python/PROJ runtime; the validated v4
warm-up and raster samples use the local raster-capable Detectree interpreter.
The output must be a fresh dedicated direct child of `/tmp` with the
`expanded-road-staging-v1` prefix. The existing private-destination guard
checks ownership, mode, Git ancestors and symlinks; existing outputs are not
replaced. Do not serve `/tmp` or this directory: filesystem checks cannot
identify every same-user or privileged server configuration.

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_road_staging_v1.py \
  --opt-in-no-unmapped-crossings --output-dir /tmp/expanded-road-staging-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_road_staging_v1.py -q -p no:cacheprovider
```

The synthetic tests cover the independent public-observation/sector bounds, finite terrain
and source-fuel checks, an exact directed round trip, water-conflict blocking,
search budget, and changed fire, wind and exclusions. The private-input test
checks twelve case records and revalidates any emitted routes; it is not a full
host, operations, suppression or scoring test. OSM and DERA omissions,
non-atomic map observations, raster classification uncertainty and unmapped
water remain limitations. © OpenStreetMap contributors, ODbL; DERA © IECA,
CC BY 4.0; ESA WorldCover 2021 / Copernicus Sentinel data. See
[license status](license-status.md) for distribution limitations.
