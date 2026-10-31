# Experimental guarded expanded incident host v1

`wildfire_expanded_guarded_v1.GuardedIncidentV1` is an opt-in, unscored,
single-driver exercise integrating the existing mixed-fire/weather diagnostic,
`Operations`, `EventBus`, and `GuardedRoadRoutes`. Its independently pinned
roster must have role `synthetic_source_adapted_not_historical`: positions,
resources, population and declarations are synthetic exercise inputs, not claims
about historical deployments or observed exposure. It is not a qualified
12-case source incident-day runner or an operational routing service.

The constructor takes the verified v4 mixed inputs and draft case, source and
runtime graph documents, independently expected hashes, cell evidence and hash,
a road-edge whitelist, a separately pinned roster, and explicit containment
settings. It reconstructs the guarded routing component and pins its identity,
implementation sources, weather/fire identity and roster. It also independently
checks and records the fixed `reviewed_pair_sha256`, including the loaded
reviewed-pair verifier and its implementation, source paths, fixed default and
manifest digest. This check occurs before route construction and at later
identity, restore and replay boundaries; the constructor has no alternate-pair
parameter or production test mode. Roster resources,
cohorts and destinations must start at exact unique graph-node coordinates.
The actor-facing route options contain only option IDs and travel times; road
geometry, guard identity and source records remain trusted-driver data.

The roster uses version `wildfire-expanded-guarded-synthetic-catalog-v1` and
exactly `version`, `role`, `case_id`, `inputs_sha256`, `graph_sha256`,
`resources`, `groups` and `destinations`. A resource has exactly `id`, `kind`
(`crew` or `engine`), node `position`, positive integer `work_remaining` and
`water_litres` (zero for crews, positive for engines). A cohort has exactly
`id`, positive integer `count`, node `position`, private boolean
`initial_exposed`, and `handover_exposure` (`null` for unknown, otherwise a
boolean matching the private declaration). A destination has exactly `id`,
node `position`, and nonnegative integer `capacity`. A cohort on a burning
handover cell must be declared privately exposed; no historical exposure is
inferred. The explicit handover disclosure and subsequently delivered reports
are the only actor-facing exposure knowledge. Exposure follows the existing
synthetic dose proxy and is not mortality.

Every accessible cell in the separately pinned routing evidence must also be
explicitly accessible confirmed land in the fire input contract. Neither a
road edge nor a synthetic roster changes a whole cell's access. The component's
`source_unknown` role permits no accessible cells; consequently actual v4
inputs, whose cell access is unknown, cannot authorize a route. The other
supported evidence role is only an explicit synthetic fixture. There is no
independently validated operational-access role here. The host does not promote
estimated fuel, infer walking access, or assert the real candidate graph is
legally traversable.

`route_options`, `return_options` and `evacuation_options` call the guarded seam
with last delivered positions and stale handover fire/wind. Dispatch rechecks
the selected complete directed edge sequence against the actual current fire,
wind and guard before acceptance. Resource movement and synthetic vehicle
evacuation recheck each swept travel segment each minute, after validating the
original journey's complete directed edges, whitelist and mode permissions.
Ordinary progress between nodes occurs only along those validated full edges;
a new route cannot start or end mid-edge. A blocked mid-edge release cannot
invent a connector or partial-edge return and leaves the unit needing assistance.
Both requested releases and Operations' automatic returns use the guarded return
planner, and ambiguous or non-node origins and destinations are unmapped.
There is no emergency egress exception in this host.

Commands take one minute. Population protection uses the existing Operations
order delivery, acknowledgment and execution delays, capacity reservations and
movement checks. Acceptance, movement and resident snapshots go through EventBus; the public
situation is assembled from delivered information only. An order-result
acknowledgment does not attach the cohort’s private exposure state; a delivered
resident update is needed to reveal a later observation.
Weather observations use their scheduled delivery times. The complete fire is
censored before operations and before/after every existing physical fire step;
a censor suppresses population and reports. Unexpected partial mutations poison
the host until restoration of a trusted checkpoint.

This version deliberately does **not** authorize suppression, hold assignments,
aircraft, fixed-mask scores, arbitrary incoming reports, non-weather events, or
full-day qualification. A source road alone does not authorize off-road work at
a hold line. Ground units that reach work receive the existing unavailable-work
result. Further integration needs separately supported work-location/access
evidence, the complete incident lifecycle, and a scored replay verifier.

Private checkpoints bind the guarded identity, weather/fire state and RNG,
Operations, EventBus, delivered knowledge and action transcript. Restore checks
identities, clocks, roster, full-edge journeys and exact reproduction. Digests
are integrity checks, not authentication. `transcript()` and `replay()` are
trusted evaluator APIs; replay requires a fresh host and independent transcript
and identity hashes, and compares each public action result. The object,
roster, source inputs, transcript, private identities and checkpoints are not
an actor sandbox and require a reviewed allowlist.

## Scorer handoff

`private_handover_exposure()` returns the pinned initial whole-cohort counts for
a trusted scorer; `initial_exposed` must never be inferred from the actor's
`handover_exposure`. A new scorer must pin the guarded host and routing source
identity, independently verify replay and fixed-mask accounting, and reject
censored/unfinished or unqualified cases. Existing scored v1/v2 exact-class
contracts do not automatically accept this new host. This module makes no
score claim and does not change those scorer implementations or their pins.

The synthetic test `tests/test_expanded_guarded_host_v1.py` exercises real
Operations dispatch, mid-edge failed return, exact-node return, evacuation,
fire-invalidated dispatch and movement, unknown access, ambiguous origins,
delayed reports, checkpoint restoration, replay and fire censoring. Its mixed
input trust anchor and accessible cells are test-only assumptions. The guarded
routing component's own tests include the available private source graph with
unknown access; passing these tests does not qualify an actual source-day host.

`tests/test_expanded_guarded_reviewed_pair_v1.py` uses the available real
reviewed graph and complete source with the production pair verifier unpatched.
Its fire and roster are synthetic and access remains unknown. It checks
rehashing and verifier/anchor tampering across host and hold checkpoints and
replay, and rejects a caller-rehashed source subset. If the private pair is
unavailable, that test is explicitly skipped. The separate small-graph
lifecycle tests use isolated test-process monkeypatches of the pair verifier
and host binding only; no such override is exposed by production constructors.
