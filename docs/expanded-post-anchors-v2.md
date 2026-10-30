# Synthetic post anchors and width screen v2

> **Superseded:** An independent review identified crossing-origin and split-validation gaps in these earlier artifacts. Use the separately pinned replacements and reproduction steps in [road review fixes](expanded-road-review-fixes-v3.md). The figures below document the earlier run; its reproduction commands no longer describe the current scripts.

This is a private, opt-in diagnostic based on the [expanded exercise road
policy](expanded-road-exercise-v1.md). It creates a separately pinned exercise
graph, twelve new synthetic scenario **drafts**, and a width-only screen. It
does not modify the original scenarios, the source-only graph, the earlier
exercise graph, or any source access mask. The drafts are marked
`private-unqualified-not-runnable` and have an empty road-edge whitelist. They
are not a registered scenario catalog, a historical reconstruction, or an
operational authorization.

## Original post identities

Every original case identifies `center-0` as `osm:n321315386` and `center-1`
as `osm:n6119295366`. The pinned original coastal snapshot and expanded road
candidate contain each same-ID node at identical longitude and latitude. The
original coastal graph rounds projected coordinates to 0.1 m; the candidate
retains unrounded projection coordinates. Rounding the candidate coordinates to
0.1 m exactly reproduces each original post position. The coordinate changes
are approximately 0.065577 m and 0.032754 m, respectively, within the maximum
0.070711 m diagonal error from rounding both coordinates. No different node is
selected and no 102.7 m relocation is made.

The first post was an interior source vertex hidden by chain compaction; the
second was already a graph node. The new graph splits the existing directed
edges at the first post's original source node, adding one node and two net
edges. The split uses the original geometry and does not add a connector,
crossing, route outside the domain, or new source arc. An independent expansion
check compares every directed source arc, endpoint, speed, class, permission
and assumption basis before and after splitting. The graph has 15,326 nodes
and 27,286 edges, with SHA-256
`ce8086850cb9a875048ecb34f1da70f98d29051f2ed33c0fd18ea2bba6136625`.

Each private draft has a new case ID, a parent case digest and an explicit
same-node/rounding mapping. Resource positions and bases, matching facility
and destination positions, and a matching already-arrived group's position
are consistently updated. The old road whitelist is cleared: it referred to
another network and no new operational whitelist is established. The draft
catalog SHA-256 is
`f039057e1bd0f43c800366083fc2b0cce74b523e77460dbbfe94a163e00feedb`.

Mode-aware searches find a 149.464 m directed source-road path from either
original post node to the other, for both exercise crew and engine modes.
The path follows one edge whose basis is the assumed normal public-road use;
it does not use the 5 m way discussed below. A synthetic all-passable raster
was used only to exercise the router at these exact nodes. Real cell access,
vehicle height and weight clearance, bridge capacity, current conditions and
complete legal restrictions remain unknown. Thus this is a topology result,
not a deployable or verified mission. The expanded source's access cells remain
unknown, and its existing host still rejects resources in them.

## Tagged 5 m way

The `synthetic-width-screen-v1` profile declares a representative vehicle width
of 2.5 m and 0.5 m of clearance on each side, requiring 3.5 m in total. The OSM
way tag says `width=5`, so it passes that arithmetic screen with 1.5 m nominal
remaining width. The tag is not an on-site measurement and the scenario does
not specify actual vehicle dimensions; this calculation alone does not
establish an operationally usable corridor.

The retained way has no explicit motor-access, maximum-height or maximum-weight
tag. None of the retrieved turn-restriction relations references the way, and
none of its nodes is a retrieved restriction via node. Those observations do
not prove absence of restrictions: the acquisition is non-atomic and three
retrieved relations have missing members. In particular, missing height and
weight tags are unknown, not evidence of compliance. The 5 m way keeps empty
exercise modes. The two post-to-post topology paths above do not need it.

The synthetic width profile, its inputs and the decision are recorded in the
private diagnostic, including the original catalog, original source snapshot,
source-manifest and graph pins. No vehicle height, vehicle weight, bridge
capacity or unrestricted legal status is assumed.

## Reproduction

These earlier artifacts are preserved. Use the current write-once commands and
fresh output paths in [road re-review fixes](expanded-road-review-fixes-v3.md).
