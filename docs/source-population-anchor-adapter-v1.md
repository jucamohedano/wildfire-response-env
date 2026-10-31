# Source population anchor adapter v1

`wildfire_source_population_anchor_v1.PopulationAnchorAdapterV1` is a private,
opt-in input adapter for a future source host. It consumes the independently
pinned [population anchor candidate](expanded-population-anchors-v1.md) rather
than creating a competing scenario catalog. The candidate is pinned to
`5ea3fcb157146a61072dba4818c7b6d224a5242d50ed5efbd5a28fc822ce39d4`.
It binds that candidate to an exact `SourceCaseCatalogV1`, the reviewed complete
source/graph pair and original source snapshot, and separate source and water
resolution evidence. It leaves the original catalog and existing source-road
and source-air code unchanged.

For every group position, group home and destination position, the adapter
independently checks the unique original graph node, the same OSM node ID and
latitude/longitude in both source snapshots, exact 0.1 m rounding, a unique
reviewed graph **and complete-source** node within or exactly 0.15 m, and an unambiguous
origin against all source segments. It re-evaluates the exercise edge mode
policy from source tags and restrictions and checks incoming and outgoing
engine evidence against the candidate. It separately recomputes directed
engine-mode graph reachability: 47 group-to-destination pairs have a graph path
and one is already at the same node. Those are topology results under synthetic
exercise assumptions, not legal, physical or current-access findings.

The four original OSM node IDs are `321315386`, `6119295366`, `595570962` and
`4341595138`; the maximum coordinate correction is approximately 0.065577 m.
For every anchored case, `private_case()` returns copies of adapted groups and
destinations alongside the original case ID and digest. `private_host_input()`
bundles the unchanged private source drafts and the explicitly separate adapted
roster for a future host; it does not instantiate or advance a host. Only the current
position, home and destination coordinates are replaced with the exact position
of the **same** source node. The original status, counts, exposure declaration,
destination references, incident, fire, weather, reports, events and aircraft
are not changed. In the occupied-at-handover case, the group's home remains
separate from its current destination. The original source selector remains
unchanged. The returned roster is labeled
`synthetic_same_osm_node_coordinate_correction`,
`private-unqualified-not-runnable`, with route access unknown; it is not a
historical deployment, registered scenario or actor-facing observation.

This resolves the **coordinate-identity** ambiguity only. For all four nodes,
the older strict surface input says `land`, but the later water-resolution v2
labels the cell `unknown`, and the v4 mixed-water overlay is an exercise
assumption. Cell access remains `unknown`. The road-only router excludes those
mixed/unknown cells, and the source draft's edge whitelist remains empty. The
adapter neither creates a connector nor widens a whitelist, authorizes a hold,
or claims a safe route. A future joint source host must pin this adapter's
identity and explicitly apply its private roster while independently retaining
all surface, water, weather and route guards. No such joint host or 12-case
outcome is created here.

The constructor accepts the source selector, the candidate and its independent
expected digest, reviewed graph and complete source, original graph and
compressed source snapshot, strict source-only input and pinned water-v2/v4
exercise documents. All are checked against fixed source identities. The
adapter's private identity also hashes its code, source-policy dependencies,
adapted roster and derived evidence. Hashes detect changes; they are not an
authentication mechanism for an untrusted Python process. Python objects,
source-derived coordinates and `private_case()`/`private_evidence()` are
trusted-driver data, not actor interfaces.

`tests/test_source_population_anchor_adapter_v1.py` uses the actual pinned
candidate, source, graph, original catalog and water evidence; only the fire
input object supplied to the existing source selector is a test-only synthetic
stand-in. It checks the twelve roster adaptations, same-node provenance,
mode evidence, topology, unchanged original fields, the occupied cohort's
separate home, boundary ambiguity, and rehashed candidate and water-evidence tampering. It does not
simulate a source incident or establish operational access.
