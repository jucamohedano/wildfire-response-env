# Expanded origin guard v1

`eval.expanded_origin_guard_v1.GuardedOriginRouter` is a separately versioned,
process-local wrapper around the existing frozen `HazardRouter`. Before every
`options` call, including emergency egress, it checks whether the origin is
within the router's 0.000001 m tolerance of source segments belonging to more
than one disconnected local source topology. If so, it returns `unmapped`
with no options and does not invoke the router. A small additional numerical
margin makes tolerance-boundary cases fail closed.

The guard indexes original source segments by way and segment identity. It
groups segments only when they share the same original source node at the
origin. A geometric crossing, an overlap, a shared distant endpoint, and two
nonadjacent segments of a self-crossing way do not establish a local junction.
Forward and reverse arcs of the same source segment count once. It inspects
all source-candidate segments, including disabled and omitted runtime edges:
otherwise a resource on an unknown road could be attached to a permitted road
at their crossing. This can reject an origin more conservatively than checking
only the selected mode or corridor. It does not create a connector or an
intersection node.

The wrapper deliberately does **not** accept a source-edge identity override.
Even when an identity is known, it refuses an ambiguous origin, because the
frozen router has no API to force its origin selection to that source edge.
For an unambiguous origin, it delegates to the existing router with the same
mode, hazard, exclusion, target and emergency-egress arguments. Missing road
coverage, existing walking behavior, hazard masks, mode permissions and all
other router limitations remain unchanged. An unambiguous origin is not proof
of operational access, legal permission, vehicle suitability or safe terrain.

Initialization requires separately trusted graph and source SHA-256 pins. It
copies and validates the graph through `RoadNetwork`, checks each graph node and
each directed edge against source identity and exact source geometry, and
builds an index from the pinned source. The caller must supply the real input,
height, walk and corridor masks. Inputs, the underlying Python object and the
process are trusted; the wrapper is not a sandbox. The 2026 source is
non-atomic and incomplete, so an unrecorded crossing can still be missed.

For the pinned private source, the canonical source digest used by the guard is
`e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1`;
the anchored exercise graph is
`97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525`.
The reported graph edges `1148795467:0:4:f` and `23359994:0:9:f` intersect at
two disconnected segment interiors. Both permit exercise engine and crew
modes; the unguarded router selects an edge there, while this guard returns no
route. The existing private graphs and drafts are not modified.

## Integration status

**This guard is not integrated into the expanded host.** Existing host routes,
returns, emergency egress, cached options and any other direct
`HazardRouter` calls remain unguarded. Consumers must explicitly construct and
use `GuardedOriginRouter` for every route request; retaining a separate direct
router reference bypasses the guard. Full host integration must cover every
entry point and revalidation path and be independently tested before calling
the host protected. The current source-map access cells remain unknown and
scenario drafts remain unqualified.

A trusted caller can construct the wrapper with the pinned graph, pinned source
candidate, and its existing router inputs and masks:

```python
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import load_candidate, strict_json
from pathlib import Path

graph = strict_json(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes())
source = load_candidate()  # checks the separately pinned compressed source
router = GuardedOriginRouter(
    graph, '97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525',
    source, 'e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1',
    inputs, heights, walk_mask=walk_mask, allowed_edge_ids=allowed_edge_ids,
)
# Use router.options(...) for all route requests, including emergency egress.
```

This example assumes the caller already has validated `inputs`, `heights`,
`walk_mask` and `allowed_edge_ids`; it does not provide an access mask or
authorize the scenario. Do not publish the private source-derived graph or
geometry; see [road review fixes](expanded-road-review-fixes-v3.md) for source
and rights limitations.

Run from the repository root with Shapely 2 and GEOS `dwithin` support:

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest tests/test_expanded_origin_guard_v1.py -q -p no:cacheprovider
```

The tests exercise the actual pinned crossing and synthetic disconnected,
nearby, disabled-road, shared-junction, self-crossing and ordinary-road cases,
plus source and graph pin checks. The all-passable rasters in those tests are
synthetic and do not change the source-map access status.
