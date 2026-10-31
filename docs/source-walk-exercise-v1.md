# Synthetic predeployed crew walk exercise v2

**Privacy:** The underlying legacy host can include a private destination record in a closure report. Use the [redacted facade](source-public-facade-v1.md) when exposing reports to an actor.

`wildfire_source_walk_exercise_v1.SourceWalkExerciseV1` is a separate, opt-in,
**unscored synthetic exercise** around the restricted source host. It accepts
only an independently pinned `expanded-crew-observed-front-walk-v1` candidate
whose parent is the reviewed wider-staging document, whose two walk hypotheses
belong to one source case, and whose selected candidate exactly reconstructs
from this host's delivered handover fire and wind. It verifies the selected
parent resource and case/fire-draft identities, the source v2/v4 pins, input
grid, source-backed supported cells and elevations, and reconstructed pinned
water geometry. The caller must also independently approve and pin the complete
integration identity and a non-sensitive public crew ID. `configuration_identity`
produces a candidate for review; it does not approve that identity. A caller
must obtain a new fresh host after identity generation.

The crew is **assumed predeployed** at the candidate's private staged position;
the pre-handover route is unknown. The walk is an exercise hypothesis, not
operational access or a safety assessment. Its profile allows at most 2 km,
uses 30% maximum coarse cell grade, excludes mapped water under a 5 m buffer,
and permits upwind/lateral cells adjacent to modeled fire. It is weaker than
the existing router hazard buffer. Unmapped water and fine terrain are unknown.
No ground suppression, line work, or road return is authorized.

At each incident minute the wrapper checks current modeled fire, current modeled
wind, source-backed land, grade and mapped-water clearance before each short
swept segment. It checks again after the fire spreads; a hazardous current
position becomes `needs_assistance`. A changed front triggers a reverse walk if
its next segment remains safe. Once the crew reaches its initial staging point,
road return remains unknown and the state is `needs_assistance`. Finite crew
duty and work budgets are charged during walking, front occupancy and retreat;
exhaustion stops movement. Ground status is available only through delayed
reports and the latest delivered public state. The actor does not receive the
private path, current fire, wind forecast or source evidence. The underlying
restricted host and walk lifecycle share the same incident clock and EventBus.
A censor suppresses subsequent observations.

The constructor requires `candidate`, `expected_candidate_sha256`, `parent`,
`v2`, `v4`, `candidate_index` (0 or 1), `public_crew_id`, `expected_identity`,
and `opt_in=True`. All documents, selected case identities, geometry, actual
positions, calls and checkpoints are private. The fixed source data and private
candidate must be present for the water reconstruction. Source, data and pin
mismatches fail closed. The wrapper implements private trusted checkpoints,
restore and exact command replay; the entire checkpoint/transcript digest must
be stored independently. Hashes do not sandbox a hostile Python process.

This module is separate from `RestrictedSourceScoredV1` and cannot be accepted
by its exact-host scorer or by either qualification runner. The focused tests
use synthetic geometry and a synthetic fake host to exercise movement,
pre/post-spread invalidation, delayed reports, finite budgets, retreat,
checkpoint, replay and rejected commands. The separate walking-input tests
cover the exclusion of v4 mixed cells, including those classified as land in v2.

Private development smokes of walk v1 and the current walk v2 implementation
reconstructed the pinned nominal v4 source inputs and one corrected source
candidate against the restricted v2 host. Handover fire and modeled and
delivered wind matched; fresh-host construction, dispatch and five further
minutes completed in the synthetic walking phase. For these smokes, the
candidate integration identity was generated in the same run; it is not an
independent release approval. The other alternative, a full day, a hidden-hazard
transition on source inputs, source-backed retreat, work, and operational access
were not tested by the current walk v2 smoke. No source-case score or physical
safety conclusion follows.

Only the authorized case at the fixed position in the pinned twelve-case parent
is eligible, even if a different candidate is supplied. Crew commands are
validated against the latest delivered phase only. A publicly eligible request
normally consumes one minute; if the actual phase has since changed, rejection
is queued as a delayed `synthetic_ground_command_result` rather than disclosed
immediately. A censor or unsupported event can stop the host before that tick,
and a censor suppresses reports. Publicly invalid commands do not advance time.
For equal observation minutes, the lexicographically greatest event ID breaks ties; older observations never
replace newer ones even if delivered later. An invalid aircraft target or other
rejected call leaves the episode usable only after the wrapper verifies that the
host, ground state and call ledger were unchanged. The remaining proposed walk
is rechecked against current hazards before each movement step. These checks do
not make the hypothetical route physically safe.

This behavior has identity `wildfire-source-walk-exercise-v2`. Prior walk v1
identities, transcripts and checkpoints require a fresh independently reviewed
identity; they cannot be silently reused. The restricted host and scorer are
unchanged.
