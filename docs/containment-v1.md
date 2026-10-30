# Experimental containment v1

This package contains an opt-in `ContainmentIncidentDayEnv` adapter, a
`ContainmentDayFire` derivative and a reusable bookkeeping kernel. The frozen
`IncidentDayEnv` and its pins are unchanged. This is a narrow synthetic exercise
implementation, not an operationally calibrated or benchmark-accepted
incident-day environment.

Install `envs/wildfire_containment_v1` and `wildfire-response-core`, or add
both packages' `src` directories to `PYTHONPATH`. The adapter is pinned to core
implementation SHA-256
`af4adc0b2fb92282246411dbac83c1eeac23eb3b4730200c0b34dc288b59323a` and
refuses a different core. Its private checkpoint also pins this package's Python
sources, core, inputs, network, configuration and clocks.

## Opt-in incident-day adapter

Construct `wildfire_containment_v1.incident_day.ContainmentIncidentDayEnv` with
the usual incident-day constructor arguments, an explicit `containment_config`
for `ContainmentKernel`, and a positive `pressure_scale`. The adapter requires
positive `wind_factor` and `wear_factor`; it rejects artifact directories and
inputs with unresolved-land censoring. Reset normally. Submit a normal resource
request using a publicly returned route option, wait for the delivered offer,
then call `propose_hold(offer_id)`. It returns line segments adjoining that
public approach cell, with two distinct proposals per edge: one for each
`protected_cell` endpoint. Proposal IDs include the selected endpoint and the
public offer and edge; neither alternative depends on hidden line or fire
state. An agent can select either orientation using public information. These
are grid-cell abstractions, not field assessments of what is at risk. Select
one by calling `authorize_hold(offer_id, proposal_id)`, which submits the
ordinary one-minute authorization. Aircraft,
invented segments, stale offers and a segment already reserved by an actor's
hold command are rejected. A reservation reflects the actor's own authorization,
not private dispatch success. Reservations are retired only when public evidence
arrives: a matching declined dispatch removes that attempted hold, an accepted
replacement removes older reservations, and an accepted release or resource
status showing return, refill, rest, availability, off-duty or assistance removes
older reservations. This includes automatic returns after water or duty is
exhausted. Delayed observations cannot retire a later authorization. A declined
replacement leaves the earlier active hold reserved until appropriate evidence
arrives. Segment proposals use public grid bounds and approach geometry, not the
hidden fire state. A proposal is an exercise abstraction, not a field survey or
a guarantee that a route, line, or return is safe.

An accepted dispatch binds that offer and segment to the resource. A declined
replacement dispatch or release preserves the existing mission. Existing
operations handle mobilization, route safety, travel, setup, work, duty, water,
return and release. Only an on-scene working crew or engine within one
Manhattan grid step of both segment endpoints can hold. A hold substitutes for
that resource's suppression callback, and the existing shared ledger charges
one work minute and, for an engine, up to its normal per-minute water amount.
The adapter checks the resulting actual duty and water deductions against the
kernel costs. Units cannot hold and suppress in the same minute. A failed line
cannot receive more work from its old mission. Once its reservation is retired
by delivered evidence, a new offer and authorization can restart a failed line
at zero strength. A fresh mission can also choose the opposite side of an intact line: the orientation
changes when the newly authorized unit first performs hold work, not when it
is dispatched. The standalone kernel trusts the caller to authorize such a
change; simultaneous assignments on the same edge must agree on the side. If
safety, access, duty, water, or target checks fail, existing operations can
stop or return the unit; a proposal does not override those checks.

While a holding unit works, it can report a coarse `holding` or `breached`
status; when an unavailable line is encountered it reports `unavailable`. These
are field observations, delivered through the normal incident-day event bus
with its synthetic radio delays. Public observations and report history contain
only delivered reports, not line strength or future deliveries. They are not a
continuous line-state feed. The standalone kernel's separate `report_delay`
setting applies to its own `observe`/`reports` API; adapter reports use the
incident-day bus instead. Private containment work records, hidden line state,
undelivered communications and joint checkpoints are trusted-driver data.

## Public transcript, replay and reward verification

`trajectory()` returns `wildfire-containment-public-transcript-v1`: the public
reset arguments and result, followed by ordered outermost episode API calls,
arguments, public results or actor-visible errors, and before/after minutes.
The recorded calls include situation and report reads, draft checks, route and
evacuation queries, ordinary commands and waits, `propose_hold`, and
`authorize_hold`. Internal calls are not recorded twice. `trajectory()` itself,
trusted verification, private checkpoints and replay-control calls are not
transcript entries. Reset starts a new transcript. Mid-episode `outcomes()` is
rejected because it would expose hidden state; terminal outcomes are public.

The transcript contains only information returned to the actor or supplied by
it, not private line strengths, undelivered reports, future event schedules,
minute audits, simulator checkpoints or evaluator labels. The reset entry
includes a seed only if the caller supplied one. Protect transcript storage as
appropriate for the actor's own submitted data. Inputs to recorded calls must
be finite JSON values; unsupported values are rejected before the call changes
state. A public result that cannot be serialized, an interruption, or a failure in a
call that may mutate state invalidates the transcript even if the clock did not
move. Malformed public call signatures, including a conflicting caller-supplied
`self` keyword on a bound call, are checked before entering a method and
recorded as recoverable TypeErrors. Explicit pre-mutation hold, route and
evacuation identity validation failures, and failures of the pinned read-only
queries, also remain recordable. Route or
evacuation failures after the engine starts building approaches still invalidate
the transcript. A failed base restore also invalidates the
transcript and re-enables recording; restore a valid checkpoint or reset before
publishing or verifying. Reads add transcript entries but do not advance simulation
time or consume fire randomness. This API is for a single trusted driver; it is
not a concurrent or adversarial sandbox.

For trusted replay, save `trajectory()` separately from the result of
`private_replay_pins()`. **The pins are private:** they include the scenario's
seed and identity. Construct a fresh adapter with independently trusted, pinned
inputs, network, containment configuration and pressure scale, then call
`fresh.replay_transcript(transcript, **pins)`. The method checks the trusted
transcript digest and the core, adapter-source, input, network, scenario, seed
and configuration pins, reproduces reset and each public call in order, and
compares every recorded result or error using canonical JSON, preserving the
difference between booleans, integers and floating-point numbers. The expected transcript digest must
come from a trusted source, not be recomputed from an untrusted replacement.
Discard a replay instance after any error. Hashes detect accidental changes;
they are not signatures or authentication against malicious input.

After a terminal or abandoned episode, `verify_episode()` performs that fresh
replay, compares the complete private checkpoint including fire, operations,
communications, containment records and the minute audit, and independently
recomputes the existing incident-day reward from the replayed minute audit.
It checks the resulting outcome fields and recorded reward. An abandoned or
censored run is not a successful completed day. This checks deterministic
consistency and independent reward accounting, not the physical accuracy of
the fire, a separately observed containment audit, or policy quality.

`checkpoint()` and `restore()` preserve the transcript as well as host and
fire state, RNGs, missions, proposals, reservations, private work records and
queued communications. Do not expose these checkpoints to an actor. They pin
this package's Python sources and the underlying identities, and their integrity
hashes detect accidental changes, not malicious tampering.

The separate `IncidentWeatherRuntimeV1` and `WeatherDayFire` are currently
**incompatible** with this adapter: the weather runtime installs its own
process-wide lifecycle and fire hooks, and the two implementations have no
shared weather/moisture or checkpoint protocol. The adapter rejects those active
hooks before a step rather than silently running without weather coupling.
Ordinary wind events in the pinned incident-day scenario still update the
adapter's fire and affect containment. Temperature, humidity and rain effects
from the separate weather runtime are not applied.

The source-map **uncertain-land censor guard is also unsupported** here. After
normal input validation, the adapter rejects any input whose estimated-fuel
metadata identifies unresolved land/water cells; it does not replace unknown
land, suppress the guard, or run until the guard would trigger. A source-map
input with no such unresolved cells is not rejected for that reason alone, but
still has to satisfy the core input and approval checks. Supporting these inputs
requires handling a skipped fire step and joint checkpoint clocks at the actual
censor boundary.

The adapter uses a narrowly scoped instance-level fire-step interception during
the existing private minute lifecycle, guarded by the core source pin. It does
not modify the installed core. It fails closed on unsupported fire stepping,
resource-accounting disagreement, unresolved-land censoring, unreviewed core
versions and artifact export. Host-side exceptions after a minute begins are not
transactional; restore a prior joint checkpoint rather than retrying a partially
applied step. Typed OpenEnv/tools, persistent artifact export, an independent
containment audit, full-day acceptance and the active-case/policy matrix remain
unimplemented. Do not use this adapter to claim the original benchmark's scores
or validation guarantees.

The focused fixture test exercises the real request, offer, authorization,
travel, setup, shared engine water and duty, exhaustion, delayed report and
joint-checkpoint replay. Focused regressions also cover automatic returns after
water or duty exhaustion, retrying the same line after public status arrives,
and declined replacement holds, including delayed delivery and stale reports.
A separate probe places one controlled synthetic fire source
next to the built segment and checks that the actual environment fire step
blocks that crossing while a matching `DayFire` ignites it. This controlled
probe is not a historical or physically calibrated fire scenario. A separate
CPU test repeats the same public request, directional proposal and hold
authorization on two instances, restores a third from a joint checkpoint,
reaches minute 1,440 without censoring and compares the final checkpoints. It
then verifies a fresh transcript replay and independent reward recomputation.
This single synthetic case is not the active-case/policy acceptance matrix.

## Fire derivative and line rule

`ContainmentDayFire` retains the `DayFire` and `RegionalFire` inheritance chain.
Because the existing `EdgeFireStep` has no candidate callback, its versioned
derivative copies that step and filters eligible source-to-target candidates
before the original maximum-probability aggregation. With no defended lines,
focused tests match the existing step over multiple seeds and a weather change,
including fire and suppression RNG state. This does not establish parity with
future core changes; review the copied step whenever upstream changes.

The kernel parameters are explicit abstract exercise assumptions. Each minute,
existing strength decays, then eligible assignments add `work_gain` up to
`max_strength`. Eligible directed crossings supply pressure equal to the
source cell's heat after its normal per-step update times `pressure_scale`.
The effective pressure adds the positive projection of wind along the crossing
times `wind_factor`; wind points in its direction of travel in grid coordinates
(positive x east, positive y south). For each line, the strongest effective
pressure in that minute **toward its protected endpoint** causes wear equal to
`min(max_strength, wear_factor * effective_pressure)`. Wear accumulates even
when the line still blocks the crossing. After wear, a crossing is blocked only
if line strength is strictly greater than effective pressure. Zero strength,
or a failed threshold at positive strength, marks the line breached. Neither
an old mission nor merely waiting restores it; only a fresh authorized mission
can restart it at zero. A reverse crossing can still breach the line by exceeding
the instantaneous threshold, but does not cause cumulative wear. The standalone
kernel accepts an optional protected endpoint; if omitted, attacks in either
direction cause wear. It exposes `restart_line` to its trusted driver and
defaults wear to zero for backward compatibility; the
incident-day adapter requires positive wear and wind factors.

For an orthogonal crossing, the corresponding edge is tested. A diagonal has
two possible two-edge orthogonal paths around its corner; it is blocked only
if each path has a blocked edge. The original requirement for fuel in both
corner cells remains. Edges shared by eligible candidates receive the maximum
pressure for each directed crossing, then crossings are processed in sorted
order. All four edges of a diagonal may be challenged even if another edge
already blocks its path; an edge can wear or breach without a successful
ignition. A blocked candidate does not discard an unblocked candidate from
another source. Every originally eligible target consumes the original single
RNG draw, even if all its candidates are blocked; the draw is compared with the
best unblocked probability. Divergent fire states can lead to different draws
on later steps.

These rules do not model actual line width, spotting, terrain workability,
productivity, crew safety or real fire intensity. A line's existence is not a
guarantee of containment. `ContainmentDayFire.from_day_fire` copies a normal
fire and RNG at a handover boundary; install it after warmup and any clock reset.
Its checkpoint format is distinct from the frozen fire checkpoint. The kernel
alone does not own a resource ledger or check dispatch, arrival or safety.
