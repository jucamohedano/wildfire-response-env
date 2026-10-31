# Experimental expanded weather and ground movement slice

`wildfire_expanded_full_v1.WeatherMixedMovementV1` is an opt-in,
synthetic-fixture-only integration slice, not the twelve-case full incident-day
environment. It combines the v4 mixed-fire/weather diagnostic with the existing
expanded host's ground router, `Operations`, and `EventBus`. It implements
actual crew and engine dispatch, mobilization, movement, setup and delayed
resource reports. At the work callback it returns unavailable: no suppression,
line work, duty, or water consumption is fabricated. It has no reward or outcome
model, and no aircraft or population operations.

The caller supplies an independently reconstructed `MixedInputsV2`, pinned
weather drafts, a synthetic-only operations catalog and pinned road network,
plus containment settings. Cases containing non-wind events or incoming reports
are rejected rather than silently skipped. The selected case's seed, initial ignition, wind
and center must agree in both catalogs. The network and input contracts are
validated by the existing host. All ground starting positions, routes and
movement segments require confirmed accessible land; missing road modes and
unknown, blocked, non-land or mixed access remain excluded. The current v4
source reconstruction marks **all** access unknown, so it cannot start a ground
resource: the slice does not promote the candidate road graph into operational
access. The test-only synthetic fixture explicitly supplies accessible cells;
it is not evidence for the actual map or the requested source cases.

The mixed-fire diagnostic performs ignition censoring and warm-up before the
operations clock starts. Each incident minute checks the private fire before
any operational mutation, advances the actual `Operations` lifecycle with the
current scheduled wind and hazard checks, then calls the existing weather/fire
step exactly once. Its mixed-fire backend checks before and after that step.
If it censors, further movement and reports stop; a post-step censor may have
already changed private operations state and cannot be scored. An exception
inside a state-changing call poisons the host; restore a trusted checkpoint.

Weather observations enter the existing communications bus at their scheduled
observation and receipt times; omitted receipt times mean immediate delivery in
this slice. Public routes use the initial handover wind and initial fire as
stale planning information. Before dispatch, during movement and throughout the on-scene setup delay, the
host rechecks the actual current fire and wind. A ground unit that becomes
unsafe during setup is returned or marked as needing assistance before work
can start; no duty or water is charged for that interruption. A ground unit at
a hazardous base is marked as needing assistance instead of being made
available, resting or refilling there; any refill reservation is released.
The status is delivered through the communications bus, and the unit stays
unavailable for dispatch while its location is unsafe. Initial fire observations are stale
after warm-up. The situation and reports expose delivered observations only;
they do not disclose the future schedule, moisture, fire state, audit or RNG.
The Python object, construction inputs, checkpoints and scenario are trusted
private data; expose only an appropriate reviewed actor allowlist.

The private joint checkpoint preserves the existing weather/fire checkpoint,
operations, bus, delivered knowledge and identity. Restore checks identity,
integrity, clocks and exact checkpoint reproduction; digests are not
credentials or authentication. This is checkpoint continuation, not a full
public-call transcript verifier. A successful synthetic continuation does not
qualify the source inputs or establish physical calibration.

The backend seam for real suppression remains in
`ExpandedContainmentFireV1.step`: it rejects all nonempty commands and clears
its internal assignments. The full containment host instead depends on
`step_with_containment(assignments=...)` and checks its returned costs against
`Operations`' work and water deductions. A reviewed expanded backend interface
must validate assignments before mutation, apply the existing containment
kernel once, enforce both censor boundaries, and reconcile resource costs even
when an interruption or post-step censor occurs. Reaching a unit's work state
without that seam is not evidence of suppression. Full incident integration
also requires source access evidence, population and aircraft lifecycle,
fixed-mask accounting, public replay and the active-case/policy matrix.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_full_v1.py
-q -p no:cacheprovider` with the project dependencies installed. Its isolated
synthetic test covers mixed fire, scheduled weather, crew and engine movement,
delayed reports, unavailable work, unknown-access rejection, checkpoint
continuation, privacy, and ignition and controlled boundary censoring. The
boundary test directly sets up a synthetic ignition, fire age and deterministic
RNG to exercise the physical post-step censor. It does not qualify real cases.

## Ground hold slice v1

`WeatherMixedHoldV1` is a separately versioned synthetic-only extension of the
movement slice. `dispatch_hold(resource_id, target_grid, option_id, edge,
protected_cell)` assigns a crew or engine to an orthogonally adjacent edge.
Both edge cells and the target must have confirmed access; the target and the
arrived resource must be adjacent to both endpoints. Routes and position are
rechecked against the current private fire and wind. The selected side must be
an endpoint, and breached, expiring or conflicting lines are unavailable.
Regular dispatch without a hold still has no suppression behavior.

`MixedHoldFireV1` validates each assignment against a copy of the existing
containment kernel, then runs the existing mixed-fire step with that assignment
batch. It checks the complete fire both before and after the step. It does not
implement a new spread model. A cloned `Operations` instance performs the
normal work and water deductions; those changes and queued operational reports
are committed only after the physical fire step returns matching per-resource
costs. A post-step censor preserves the matching private charges and suppresses
reports. An exception after stepping poisons the host and requires restoring a
previous trusted joint checkpoint; the fire mutation is not automatically
rolled back. Checkpoints contain the missions, weather/fire, operations and
communications and are trusted private data. This is continuation, not a
public-transcript replay verifier or a full incident-day API.

The v4 source access restriction above is unchanged; the test uses synthetic
access and a test-only mixed-input trust anchor. Tests run actual crew and
engine holds, finite water and duty exhaustion, fire spreading into the setup
hazard area for both ground kinds and both slices, unsafe-position rejection,
cost mismatch and recovery, fresh-instance action replay, checkpoint continuation,
and post-step censoring.
A separate controlled paired-fire experiment exercises an actual blocked
crossing and compares it to identical fire without the line. Its imposed
ignition and RNG are a synthetic physics test, not an operational safety claim.
Population, aircraft, full event and communication lifecycles, independent
scoring, and full-case/policy validation remain unimplemented here; there is no
score claim.
