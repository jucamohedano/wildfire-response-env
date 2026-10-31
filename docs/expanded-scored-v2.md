# Experimental expanded fixed-mask scoring v2

`wildfire_expanded_scored_v2.ExpandedScoredV2` is a private, opt-in scorer over
the exact `ExpandedPopulationV2` implementation described in
[expanded-population-v2.md](expanded-population-v2.md). It is a synthetic
exercise composition, not a qualified source case or released benchmark.
[Scored v1](expanded-scored-v1.md) remains unscored because its population host
cannot establish inherited exposure.

The population v2 catalog requires a private whole-cohort `initial_exposed`
boolean and a separate public `handover_exposure` value for each group. These
are synthetic exercise inputs, not measured historical exposure or mortality.
The scorer checks the catalog-pinned private handover counts against the true
operational roster and initializes both its private counter and minute-zero
audit from those counts. It never uses actor-visible knowledge as ground truth.
The population host rejects a false exposure declaration for a cohort occupying
a burning handover cell, and validates disclosed knowledge against the private
declaration. Exposure proxy and reporting limits remain as documented for the
population host.

For repository use, add `envs`, `envs/wildfire_extended_inputs_v1`,
`envs/wildfire_response/core/src`, `envs/wildfire_containment_v1/src`,
`envs/wildfire_containment_score_v1/src` and the repository root to `PYTHONPATH`.
The trusted evaluator supplies an independently approved fixed mask and complete
configuration identity before a run. The mask is a set of unique, nonnegative
row-major integer IDs for cells with supported fire and positive fuel at
handover. The identity binds the mask hash, population v2 identity and catalog,
the underlying host and data identities, reward version, a separate effective
mixed-factor hash, and a composite source hash. The composite hash covers the v2 scorer and population files plus the
existing scored-v1 source manifest for the fire, weather, operations and
independent accounting dependencies. Hashes detect differences; they do not
establish trusted provenance, representativeness, physical calibration, or
security against a hostile Python process. Independently review and pin the
inputs, sources, mask, seed, scenario, configuration and synthetic assumptions.
A candidate obtained from the run being graded is not an independent pin.

```python
from wildfire_expanded_scored_v2 import (
    ExpandedScoredV2, mixed_factors_sha256, verify_outcome,
)

# release.mixed_factors is independently approved, not read from the run.
if mixed_factors_sha256(release.mixed_factors) != release.expected_identity['mixed_factors_sha256']:
    raise ValueError('Mixed-factor release pin differs')
# Inputs, expected_identity and mask are independently approved before the run.
env = ExpandedScoredV2(release.make_population(), scoring_mask=release.mask,
                       expected_identity=release.expected_identity, opt_in=True)
# Expose only situation, reports, route_options, evacuation_options,
# protect, dispatch_hold and advance through an actor allowlist.
# ...run the policy...
result = verify_outcome(env, fresh_population=release.make_population,
    expected_identity=release.expected_identity, scoring_mask=release.mask,
    infrastructure_timeout=timed_out)
# Only status == 'full_day' with verified == True supplies a score.
```

`configuration_identity(fresh_population, mask)` generates a candidate for
independent review; do not silently regenerate a release pin from an evaluated
episode. The factory must recreate an exact fresh v2 handover from pinned data.
The caller must separately decide whether an empty or partial mask is suitable.

`mixed_factors_sha256` hashes compact, sorted-key ASCII JSON with `version`
`wildfire-expanded-effective-mixed-factors-v1` and `factors`, a row-major list
of `[x, y, fuel_factor, crossing_factor]`. Coordinates are nonnegative exact
integers; factor values are finite numbers normalized to floats, with fuel in
`(0, 1]` and crossing in `(0, 1)`. Duplicate coordinates are rejected. The
caller must pin the hash of independently approved factors, including every
mixed cell and its crossing/openness assumption. A hash taken from the episode
being graded does not provide independent trust.

At construction and each step the scorer checks that the declared factors match
the mixed backend's fuel and crossing maps, the active fire kernel's crossing
map, the censor's allowed mixed cells and each affected cell's actual initial
fuel. It checks the declared input and those effective values again on restore.
The private checkpoint carries the canonical factors as well as their identity
hash. Altered, omitted or extra factors fail closed even when a source identity
or an inner population checkpoint happens to match. This is a new scored-v2
source identity and checkpoint shape: prior v2 pins and checkpoints without the
factor identity are incompatible and need independent review, not silent pin
replacement. Scored v1 remains fail-closed.

At handover, the scorer records all cells with active heat or accumulated burn.
Every committed physical minute adds new cells and private population state to
an audit. The outcome is 80% the fraction of initially unexposed population
that avoids new exposure plus 20% the retained fraction of fixed-mask cells
outside the handover footprint. An empty denominator contributes one. Inherited
population exposure and burned footprint are excluded; additional burned area
counts only new cells within the mask. Simulated exposure is not mortality.

The external gate returns a score only for an uncensored 1,440-minute completion
after a fresh v2 replay matches public results and the complete private joint
checkpoint, and independent audit recomputation matches the production counter.
Timeout, censor, unfinished runs, identity or mask mismatches, and failed replay
are unscored. An already censored handover is rejected at construction. There is
no abandonment or truncation score in this slice. Replay and hashes do not prove
input authenticity or calibrate the exercise.

Private checkpoints include the population, fire, weather, operations, reports,
mask, counter, audit, recorded calls and identity. Restore checks the fixed
handover counts against the independently pinned v2 population catalog and
requires accounting and underlying clocks to agree. Verification of a resumed
run starts at a fresh handover. A failed state-changing call poisons the wrapper
until a trusted checkpoint is restored. Keep the Python objects, factory,
configuration, mask, private handover, audit, calls and checkpoint private.
Only expose the documented actor calls through a reviewed allowlist: a null
`handover_exposure` stays unknown until a report is delivered, and reads do not
reveal dose, future weather or evaluator state.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_scored_v2.py
-q -p no:cacheprovider`. Its full-day synthetic fixture includes inherited and
new exposure, a ground hold, delayed observations, checkpoint continuation,
fresh replay, mismatch checks and controlled boundary censor fault injection.
The regression also reproduces equal population identities and checkpoints with
different effective crossing factors, and verifies that identities, restoration
and replay reject the mismatch.
It uses a test-only mixed-input trust anchor. Neither the test nor any prior
runtime matrix qualifies the twelve source cases; source-backed population and
access data, independently reviewed masks and pins, a suitable runner and the
complete expanded active-case/policy matrix remain required before release.
