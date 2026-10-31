# Guarded road-only route seam v2

`eval.expanded_guarded_routes_v1.GuardedRoadRoutes` is a separately versioned
routing component for an eventual source-scenario host. **It is not wired into
any existing host.** It owns a `GuardedOriginRouter` and provides explicit
`dispatch_options`, `return_options` and `evacuation_options` entry points.
It does not mutate the frozen host, router, source inputs, old graphs or
scenario drafts. Version v2 requires the reviewed graph-and-full-source pair
below; arbitrary caller-rehashed pairs are refused. Python inputs and pins are trusted process-local data; the
component is not a sandbox or an operational emergency-routing service.

Each entry point checks both the origin and destination for ambiguous source
segments. It requires each endpoint to equal exactly one retained source graph
node; a mid-edge return, a rounded position or an off-network connector remains
unknown. The underlying guard validates the caller-pinned graph against the
caller-pinned source arcs and refuses ambiguous crossings before calling the
frozen router. Its source index includes disabled and omitted candidate arcs,
so another road at a crossing cannot be silently attached. No source-edge
identity override is implemented.

An explicit whitelist is required. Every returned leg must be a complete,
directed source-backed road edge in that whitelist, with `engine` permission
and the requested ground mode permission. The whole edge geometry, endpoints
and successive leg continuity are checked. Empty, walking, partial-edge,
connector, unlisted, mode-incompatible or malformed journeys are discarded.
Crew and synthetic vehicle evacuation requests use the frozen router's engine
vehicle search to avoid its crew walking fallback; crew permission is checked
separately. This deliberately excludes crew-only edges and may miss a valid
route. A route is an unverified synthetic exercise candidate, not a claim of
legal access, vehicle clearance, historical use or tactical safety.

The component never treats a road as evidence of full-cell access. Callers
provide a separately hashed `expanded-route-cell-evidence-v1` document with
surface and access rasters. Cells without both `land` and `accessible` are
excluded from every swept road segment; the walking mask is always zero. Two
roles are supported:

- `source_unknown` requires every access cell to remain `unknown` and cannot
  authorize a ground route.
- `synthetic_fixture_only` accepts explicit fixture cells for isolated tests;
  these are synthetic assumptions, not source evidence.

There is currently no role that claims independently validated real access.
Component, guard and router source hashes, height input and the whitelist are
recorded in the component identity; height
is not inferred from roads and is not used to authorize off-road movement.
Callers must supply appropriate observed fire, wind and restrictions; dynamic
excluded cells are passed through and every returned edge is rechecked against
them. The component does not establish that those observations are complete
or current.

## Independently pinned source/graph pair

The component recognizes one reviewed pair, embedded in the routing module as
`REVIEWED_PAIR_MANIFEST`. Its independently fixed manifest digest is
`bbfcf2f257bca8e793f9fb8d864fb37de9bc3609c998dd94ed744e7734a8c548`.
It binds the v4 anchored graph digest
`97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525`
to the **complete** expanded candidate canonical digest
`e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1`,
its compressed source digest
`976875d58d2d134ba1f5d2aa62030f23b567f56d0bf98dd9820f88a2cb3ae343`,
and source-manifest identity
`d331a7fd511ba9ebbd767a85d78dccb7c7e68eaef6a4d4294ab183949e6586e9`.
The complete candidate includes all 168,948 directed arcs, including roads
that a particular runtime graph, mode or whitelist may omit or disable. The
manifest also pins source node, way, restriction and boundary-evidence counts.

The constructor checks the fixed manifest digest, both supplied hashes, both
actual object digests, source and graph provenance and complete-source counts
before constructing the origin guard. Passing a newly computed digest for a
reduced source cannot change the independently anchored pair. The canonical
source digest binds all candidate fields, not just these counts. The compressed
hash records the previously audited source and is cross-checked against graph
provenance; it is not recomputed from an in-memory source object. The separate
raw-acquisition audit and graph reconstruction remain necessary evidence for
how this pair was reviewed. The caller cannot supply a replacement pair
manifest or pair pin to the public constructor. A different real graph or
candidate needs a separately reviewed code and manifest version.

The lower-level `GuardedOriginRouter` alone does **not** impose this pair
registry. Use the v2 road component when relying on this binding. The pair
manifest digest is included in `GuardedRoadRoutes.identity`, along with the
component and dependency source hashes, graph and source pins, cell evidence,
heights and whitelist. The host must pin this identity and the reviewed
manifest independently across replay and checkpoint restore; recomputing a
manifest or accepting caller-chosen pair pins defeats the protection. Source
hashing is not a Python sandbox: the host must also protect its loaded modules
and trust anchors from mutation.

The host author should bind and check the loaded `_verify_reviewed_pair`
function, its fixed anchor and `REVIEWED_PAIR_MANIFEST` digest as well as the
routing-module file hash; a file hash alone does not detect in-memory function
replacement. Capture `reviewed_pair_sha256` in the host identity and reject
checkpoint restore if it differs.

Synthetic unit tests use a test-local fixture seam or a separately fixed
fixture manifest; that seam is not a production registry. Previously generated
arbitrary synthetic graph/source fixtures are no longer accepted by the
production constructor. Host test authors must use a reviewed fixture pair or
an explicitly test-only fixed trust seam, with a distinct graph identity for
distinct source candidates. They must not turn test-only rehashing into a
production fallback. Existing v1 checkpoints are not v2 identities.

## Source posts and current outcome

`check_anchored_draft` verifies the separately pinned v4 draft catalog, graph,
case digests, original OSM node identities, resource bases and exact post
coordinates. It does not register or run the draft. The original two post IDs
are retained as source-node endpoints in the private v4 graph. Its draft
whitelist is empty and the source access converter marks all cells unknown.
Consequently the component returns **unmapped** for actual source post
requests, returns and evacuations. It will not widen the draft whitelist or
promote source-road cells to accessible land.

Tests exercise the actual pinned graph and its reported disconnected crossing.
They also use an explicitly synthetic all-access fixture to exercise the
original post-to-post graph route for both ground modes. That fixture is not a
source-map access assessment. The road-only restriction is particularly
conservative for interrupted travel: a resource between graph nodes cannot be
returned until a future implementation retains and safely validates its actual
source-edge identity and position.

## Exact host integration blockers

A safe source host cannot be assembled solely by wrapping the current public
methods:

1. `SourceGroundedDiagnosticV1.route_options` and `dispatch` explicitly raise
   `NotImplementedError`. The existing `ExpandedIncidentV1` constructor
   creates its own `HazardRouter` and accepts only a
   `synthetic_fixture_only` catalog with a different case schema. Its ground
   resource constructor rejects unknown access cells. The v4 twelve-case draft
   is not such a catalog and is not runtime-qualified.
2. The expanded movement and hold hosts call the internal router for dispatch
   revalidation and advance `Operations` without a source-aware ground return
   planner. A real integration must route all initial and repeated dispatch,
   duty and emergency returns through the guard, revalidate the full road-only
   journey at execution time, and preserve the guard and evidence pins through
   checkpoints and restore. Replacing only public `route_options` would miss
   those lifecycle paths.
3. `ExpandedPopulationV1.evacuation_options` calls the host's internal router
   directly, and its constructor requires the exact `WeatherMixedHoldV1` type.
   It must accept a reviewed guarded routing seam and revalidate evacuation
   journeys and any replanning, without creating walking access from roads.
4. The original incident-day environment independently invokes its router for
   ground dispatch, evacuation, returns, emergency egress and access-change
   counterfactuals, and binds `Operations.return_planner` on construction and
   restore. A source-capable version must route all of those calls through one
   guarded component, preserve source-edge identity for mid-edge positions,
   validate movement after hazards change, and pin the integration in replay and
   checkpoint identities. Direct `RoadNetwork.path` also ignores mode flags and
   must not be used to authorize a journey.

Those host/API and scenario-contract changes, independently supported cell
access evidence, and full lifecycle/replay checks remain future work. A caller
must not treat this module as if those changes have occurred.

## Validation

With the private pinned source candidate and v4 graph/drafts available:

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest tests/test_expanded_guarded_routes_v1.py \
  tests/test_expanded_origin_guard_v1.py -q -p no:cacheprovider
```

Tests cover a rehashed incomplete-source attack, manifest tampering, guarded
dispatch, return and evacuation entries, actual and
synthetic disconnected crossings, exact-node anchors, one-way direction,
unknown and synthetic cell roles, empty and restricted whitelists, missing
vehicle modes, and rejection of walking and invented connectors. The real
graph's unknown-access test returns no route. Synthetic route results do not
establish operational suitability. Source-derived geometry remains private
under `/tmp`; see [origin guard](expanded-origin-guard-v1.md) and the linked
source attribution and rights documentation.
