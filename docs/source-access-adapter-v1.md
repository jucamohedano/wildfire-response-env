# Source staging to road-journey adapter v1

`wildfire_source_access_adapter_v1.SourceAccessAdapterV1` is a private,
trusted-driver adapter, not another incident simulator or a public facade. It
connects the existing twelve-case `SourceCaseCatalogV1`, the existing
`SourceRoadOnlyV1` route/movement checks, and an independently pinned private
wider-staging candidate. It does not change the shared host, scenario roster,
fire, access raster, road graph, work-site permissions, scoring or prior
versions. The actor must not receive its private source geometries, candidate
hashes or internal failure details.

The adapter accepts only exact reviewed catalog and road-component objects,
requires explicit opt-in and a separately supplied candidate digest, verifies
all twelve case/ground-resource lineages and exact original source-node bases,
and confirms that the road whitelist is a subset of individually screened
source edges in the candidate. Every accepted edge's direction, original way,
segment range, endpoints, mode permissions and geometry hash must match the
reviewed graph. At construction the adapter also verifies the original graph
document against its independent pin, then binds each allowed live edge, its
assumed speed, endpoints and allowed-edge adjacency to that document. It checks
those bindings before every planning and movement call; mutation fails closed. Edges with missing, ambiguous or rejected water evidence cannot
enter this whitelist. The underlying road component also excludes v4 mixed,
unknown, sea, unsupported-fuel and unsupported-elevation cells; it never turns
a road into whole-cell or walking access. The synthetic mapped-road exercise
assumption does not establish actual road conditions, legal access, water
clearance or safety.

A trusted caller invokes `candidates(case_id, resource_id, current_position=...,`
`burning=..., wind=..., excluded_cells=...)` with current, independently
verified state. The adapter requires the resource's exact original base and
initial availability, and considers only its retained node proposals. It
returns an option only when the existing road component returns a directed,
full-edge **outbound and return** at that snapshot. `outbound` is a private
`Operations.dispatch`-compatible journey; `return_at_snapshot` is a planning
check, not a guarantee of later return. `movement_safe(...)` delegates to the
existing exact-edge swept-segment guard. A host must bind its own identity and
current position/fire/wind/restrictions, enforce finite resources and delayed
observations, revalidate outbound/return each minute and at return time, and
separately authorize any supported work site. No candidate here authorizes
work. Initial predeployment proposals are treated as possible destinations, not
proof that a prehandover journey occurred. Later resources are unavailable in
this handover-only adapter; it does not invent delayed staging or connectors.

If staging equals the base, no journey is invented and the reason is
`staging_is_base_no_journey_or_work_site`. If a direction cannot be found,
`no_supported_outbound_route` or `no_supported_return_route` describes only the
bounded retained evidence under the current snapshot, not physical
impassability. `search_incomplete` preserves the parent node/variant budget.
Unavailable results include a `needs` description of the missing evidence or host capability.
A resource not represented by a retained candidate or not initially available
is unavailable; this is not a claim about all possible resources or roads.
Both the candidate digest and the road whitelist digest must be supplied from
independent trusted review, not obtained from the same untrusted candidate.

A production-input private check reconstructed the pinned v4 inputs and
reviewed graph/source pair, used only the eight individually screened directed
edges, and queried all ground roster entries against the saved partial public
handover. It found no source-road outbound/return candidate for any of the 12
cases. Five retained proposals were already at their base; the other 19 could
not produce supported outbound routes. The parent search is incomplete in all
cases. These are snapshot results, not full-fire, full-day or operational
validation. The existing domain ground bridge still only supports its separately
approved single-case predeployment and does not automatically call this
adapter. A new reviewed integration is required before a shared host can
consume these journeys; the synthetic test demonstrates the existing
`Operations` journey contract, not integration or real access.

```sh
PYTHONPATH=envs:envs/wildfire_extended_inputs_v1:envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_source_access_adapter_v1.py -q -p no:cacheprovider
```
