# Source-road-only and event timeline slices

This change does **not** provide a complete source-scenario incident host or a
12-case run. It adds two separately versioned, unscored components that can be
reviewed before such a host is constructed.

`wildfire_source_road_v1.SourceRoadOnlyV1` accepts verified v4 mixed inputs,
the complete independently reviewed source/graph pair, and an explicit,
independently hashed edge whitelist. Each whitelisted edge must explicitly
permit engine travel and have a reviewed exercise access basis of assumed
public road, explicit public-road access, or explicit service-road access.
Crew vehicle routes additionally require crew permission. This is a synthetic
exercise assumption about **mapped road geometry only**, not legal access or a
claim of safety. The router never changes the v4 cell access raster, which
remains unknown, and exposes no general safe-position or walking API.

The router uses the existing source-origin guard and road router, with a zero
walking mask. Each query requires exact unique source graph-node endpoints,
rejects ambiguous origins and destinations, and admits only complete,
continuous, permitted directed source edges. Unknown, sea, mixed-water,
outside-domain, unknown-fuel and unknown-elevation cells are excluded from
swept road segments; current fire, wind and caller-provided restrictions are
also checked. `movement_safe` is for a trusted host's previously authorized
full-edge journey: it checks exact source geometry, modes, canonical edge
length and duration, the actual prior position, and the current swept segment. It does not create a route
from a mid-edge position. A road crossing a cell never makes the rest of the
cell walkable or accessible. Results contain private source geometry and must
not be sent directly to an actor.

`wildfire_source_road_v1.timeline.EventTimelineV1` is a separate event and
communications slice, **not a fire or operations host**. Given an independently
pinned synthetic scenario, it emits weather and incoming reports through the
existing delayed `EventBus`; faithfully applies central-sector access
restrictions, center outages and destination closures; and explicitly stops
before any unsupported scheduled event. An aircraft in the initial roster
produces `unsupported` at handover. It never moves a resource, cohort,
destination or incident, and it produces no reward. The timeline requires a separately pinned original-grid geometry and returns
its hash with private restriction cells. It does not silently transform those cells to the expanded grid: a
future host must verify and version the exact grid conversion before using them.
Its private checkpoint and action transcript support integrity checks and fresh
replay. `restore()` requires an independently trusted SHA-256 of the entire
checkpoint, including its embedded digest; a digest read from the same
untrusted checkpoint is not an independent pin. A caller that trusts an altered
checkpoint and supplies its altered digest has crossed the trusted-driver
boundary; hashes do not authenticate such input. The component validates a single case hash, not the complete 12-case
source-catalog lineage or post anchors; callers must validate those separately.

`wildfire_source_road_v1.catalog.SourceCaseCatalogV1` is a private 12-case
lineage selector, not an incident runner. It independently checks the reviewed
source/graph pair and the original catalog and parent input pins, rebuilds the
expanded-fire drafts, validates the original coastal source snapshot, and
reconstructs the anchored drafts from the original OSM post identities and
rounding. `private_case()` returns the original case, expanded-fire draft and
verified anchored **ground resource** positions separately. It does not return
the anchored draft as an operational population roster: original cohort and
destination positions stay unchanged and no connectors are invented.

The private v4 post-anchor catalog contains twelve synthetic exercises, but all
twelve include aircraft. This event-only timeline explicitly stops at minute zero for them; it does not
discard aircraft or pretend they are runnable full scenarios. The separate
[aircraft-only slice](source-aircraft-v1.md) implements their aircraft lifecycle
while continuing to label ground and population operations unsupported. The
expanded-fire drafts share parent scenario hashes with the anchored drafts but
use remapped grid cells, seed offsets and different initial-wind assumptions.
Those choices must be composed and independently pinned before an incident-day
host can safely combine them. The source post anchors preserve original OSM
node identity with centimeter-scale coordinate correction. Population and
other destination positions do not generally have independently verified
exact graph nodes: this change does not snap or move them, and unsupported
connectors remain unavailable. A separately labeled, evidence-backed adaptation
is required for any future relocation.

`tests/test_source_case_catalog_v1.py` checks lineage and non-relocation for
two cases against the real pinned original catalog, old source snapshot and
reviewed graph without executing a source day. Its fire input object is a
test-only synthetic stand-in. `tests/test_source_road_only_v1.py` uses the **real reviewed graph and complete
source pair with the production pair verifier unpatched**, combined with a
synthetic all-land/fuel/elevation fixture whose access raster stays unknown.
It exercises exact-edge routing, dynamic fire and restriction guards, rejection
of partial/walking/altered-speed journeys and unknown surface cells. This is not
evidence that the real surface or roads are operationally traversable.
`tests/test_source_event_timeline_v1.py` exercises two source-like synthetic
sequences with delayed observations, restrictions, destination and center
changes, unsupported-event stopping, checkpoint and replay. When the private
catalog is available it also checks that two actual source draft cases stop
explicitly at handover for their aircraft, without simulating a source day.
Neither test is a full-day policy matrix or score.
