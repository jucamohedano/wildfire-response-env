# Source population Operations stage v1

`wildfire_source_population_tick_v1.PopulationOperationsStageV1` is a private,
opt-in **single-minute Operations staging component** for a future domain
incident owner. It is not a complete host, actor API, source-day runner or
scorer. It does not advance fire, deliver EventBus messages, or commit state.
The caller receives detached Operations state and pending private events, which
must be combined with the same minute's fire, aircraft, hold-cost, censor and
communication transaction or discarded. A callback that mutates the live owner
is detected and poisons the owner. No scenario, roster or mask catalog is
created.

The component accepts only the exact existing domain-ground host, independently
pinned population-anchor adapter and independently pinned population-segment
movement seam. It checks their graph/source/case lineage and requires that the
host already contain the exact adapted group homes, handover positions and
destinations. The current domain-ground host does **not** install that roster, so
this component cannot simply be attached to it and is not yet a composed host.
The existing anchor correction is a synthetic same-source-node exercise, not an
operational-access claim.

Within the detached Operations tick, shelter requests use the existing
Operations order lifecycle. A same-minute destination closure is applied before
a request, and aircraft availability/daylight is derived from the pinned
schedule. The future owner must still emit each corresponding generic public
event exactly once; this stage does not emit those scenario-event reports. An evacuation request can use only an exact pinned
segment option validated against the actual current modeled burning cells, wind
and exclusions. Every subsequent group movement delegates to the existing
segment seam, which rechecks the canonical full path, prior position and swept
minute. An engine access restriction disables those routes. Domain cells that
are estimated, unknown, out of domain, unsupported or without elevation are
excluded; allowing a pinned exact-edge exercise does not make an entire mixed
cell accessible. Missing, ambiguous, same-node and unsupported routes do not
create an evacuation order. These are synthetic no-unmapped-crossing
assumptions, not legal access, a water survey or physical safety evidence.

The caller must supply explicit callbacks for non-population movement, hazards
and suppression. Those callbacks must only stage work and must not mutate the
live owner. The stage must never be passed to an actor: it contains private
cohort data, paths and pending events. A future owner still needs a reviewed
public handover and destination disclosure, delayed report facade, same-tick
integration with the existing hold/fire lifecycle, private checkpoint and exact
replay. It must also separately pin the fixed source-supported scoring mask,
profile factors and complete scorer identity before any 80/20 verifier can be
used. The existing scorers deliberately reject this estimated-fire/hold
composition; this component provides no score.

`tests/test_source_population_tick_v1.py` uses synthetic test-only lineage and
routes to check staged shelter, exact-route evacuation, per-minute dynamic
blocking and recovery, missing routes, restrictions, estimated-cell exclusion,
lineage mismatch, same-minute closures/restrictions, and live-owner mutation detection. Passing these tests does not
qualify a source incident or prove a physical route.
