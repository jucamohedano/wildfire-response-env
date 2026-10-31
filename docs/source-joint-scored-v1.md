# Shared synthetic joint scoring v1

`wildfire_source_joint_scored_v1.SourceJointScoredV1` is an opt-in trusted-driver
scorer for the exact `SharedSyntheticIncidentV1` host described in
[shared-synthetic-fixture-v1.md](shared-synthetic-fixture-v1.md). The underlying
host advances one fire/weather model, one Operations state and one EventBus.
This is a **synthetic fixture only**; it is not a source-case benchmark or
historical reconstruction. No actor-facing score or mid-episode reward is added.

A trusted evaluator supplies an independently reviewed configuration identity
and a fixed mask before the run. The identity binds the exact joint host and
its fixture, road, work sites, weather, physics and source identities; the
separate scorer and dependency source hashes; reward version; mask hash; a
canonical mixed fuel/crossing-factor hash; a canonical terrain-profile hash; a
handover-exposure hash; the full private handover checkpoint hash; and
effective static operations, routing and fire configuration hashes. The
host still enforces its own independent fixture, road, work-site and draft pins.
A digest detects differences but does not authenticate untrusted inputs or
establish physical calibration. A candidate identity produced from the episode
being graded is not an independent pin.

`handover_exposure_sha256(groups)` hashes a versioned, sorted roster of IDs,
counts, private `initial_exposed` booleans and optional public
`handover_exposure` declarations. `mixed_factors_sha256(factors)`, imported from
`wildfire_expanded_scored_v2`, hashes sorted `(x, y, fuel, crossing)` factors as
specified in [expanded-scored-v2.md](expanded-scored-v2.md). The caller must
independently approve and pin both inputs. The scorer checks initial private
exposure against the fixture and the true Operations roster, and checks declared
mixed factors against the active fire, backend, censor and mixed-cell initial
fuel. It repeats the factor checks at action boundaries, every tick and after
restore. The factor inventory and hashes remain evaluator-private. The initial facility
water stocks are bound by the fixture and the handover checkpoint; the static
operations configuration excludes only their evolving stock values. The routing
configuration binds the live graph, edge whitelist, exclusions, work sites,
node mappings and router grid state. The static fire configuration includes the
actual containment settings, pressure, rules, spread multiplier and slope flag;
weather-driven spread factor is state, pinned at handover and checked through
the subsequent checkpoint and replay.

`terrain_profiles_sha256(profiles)` separately hashes a canonical inventory of
all terrain profile names and their `fuel`, `spread` and `duration` values using
the version `wildfire-source-joint-terrain-profiles-v1`. Names and field sets
are exact; values must be finite non-boolean numbers, with nonnegative fuel and
spread and positive duration, normalized to floats. The evaluator must approve
and pin these values independently of the episode under test. The scorer
checks that the mixed-fire step is the bound kernel, that its `PROFILES` global
is the actual loaded terrain-module table, and that the canonical values match
the external pin. It checks at action boundaries, after each tick, and before
and after restoring a checkpoint. The private checkpoint includes the canonical inventory.
A detected change poisons the scorer until the trusted profile binding and a
trusted checkpoint are restored. These checks do not sandbox a hostile Python
process or guarantee detection of a change that is undone inside a single tick.

This adds a required identity field and checkpoint inventory: prior joint
scorer identities and checkpoints without the terrain-profile pin are
incompatible and require independent review rather than silent repinning.

The mask is an independently selected set of unique nonnegative row-major IDs
of supported, fuel-bearing cells at handover. The scorer excludes inherited
burned cells from its eligible area denominator and inherited exposed people
from its population denominator. On a completed uncensored 1,440-minute run the
outcome is 80% the fraction of initially unexposed people without new exposure
and 20% the fraction of eligible mask cells not newly burned. An empty
denominator contributes one. Simulated exposure is not mortality. Censor,
unfinished runs, timeout, identity or mask mismatch and failed replay are
unscored; private nonterminal/censored diagnostics have zero graded score.

For repository use, add `envs`, `envs/wildfire_extended_inputs_v1`,
`envs/wildfire_response/core/src`, `envs/wildfire_containment_v1/src`,
`envs/wildfire_containment_score_v1/src` and the repository root to `PYTHONPATH`.

```python
from wildfire_expanded_scored_v2 import mixed_factors_sha256
from wildfire_source_joint_scored_v1 import (
    SourceJointScoredV1, handover_exposure_sha256, terrain_profiles_sha256, verify_outcome,
)

# release data and expected_identity are independently approved before the run.
if (mixed_factors_sha256(release.mixed_factors) != release.expected_identity['mixed_factors_sha256']
        or handover_exposure_sha256(release.groups) != release.expected_identity['handover_exposure_sha256']
        or terrain_profiles_sha256(release.terrain_profiles) != release.expected_identity['terrain_profiles_sha256']):
    raise ValueError('Independent release pins differ')
env = SourceJointScoredV1(release.make_fresh_host(),scoring_mask=release.mask,
                          expected_identity=release.expected_identity,opt_in=True)
# Expose only situation, reports, dispatch_hold, clear_aircraft,
# dispatch_aircraft, protect, release, request_status and advance.
# ...run the policy...
grade = verify_outcome(env,fresh_host=release.make_fresh_host,
    expected_identity=release.expected_identity,scoring_mask=release.mask,
    infrastructure_timeout=timed_out)
# Only a verified full_day result supplies a score.
```

`configuration_identity(fresh_host, mask)` only accepts a fresh handover and is
a candidate for review, not a trusted release pin. The fresh factory must reproduce the exact handover from
independently pinned inputs. A multi-minute wait is executed as individual host
minutes to record each audit step; the host, weather and Operations still share
one clock. No reads advance time. Verification replays the recorded actor
mutations from a fresh host, compares their public responses and the complete
private scorer/host checkpoint, and independently recomputes every outcome from
the minute audit. A timeout remains unscored even if an in-flight action later
completes. An already censored handover is accepted only as an unscored censored
state.

The Python objects, factory, fixture, pins, mask, audit, transcript and
checkpoints must remain private. A `null` handover exposure remains unknown to
the actor until a report is delivered. Expose only the actor allowlist through a
reviewed facade. A failed state-changing call poisons the scorer until a trusted
checkpoint is restored. `restore` requires an independently stored digest of the
entire scorer checkpoint; the nested host digest is checked as part of that
trusted envelope. Hashes and replay do not protect against a hostile trusted
Python process.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_source_joint_scored_v1.py
-q -p no:cacheprovider`. The fixture uses a test-only mixed-input trust anchor,
synthetic road nodes and work sites, controlled pre-step and post-step
censor injections, and terrain-profile mutation and binding checks. It does
not qualify any of the twelve source cases. A separately approved source host,
source population and access evidence, fixed masks and identities, transport
review, and a complete active-case/policy matrix remain outside this fixture.
