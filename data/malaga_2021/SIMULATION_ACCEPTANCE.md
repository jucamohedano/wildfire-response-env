# Simulation-first acceptance, 25 October 2026

UI expansion is paused. The current target is a coherent, deterministic
**synthetic control-centre environment**, not a perfect or operationally safe
wildfire forecast. No runtime/kernel parameters changed during these checks.

## Terrain checks

`terrain_control_validation_v1.json` pins official IGN point records, sheets,
product documentation and a small5m WCS diagnostic patch. Reproduce offline:

```sh
PYTHONPATH=envs/wildfire_response/core/src .runtime/malaga-data-venv/bin/python \
  -m eval.validate_terrain_controls --output .runtime/terrain-controls-new.json
```

Restore the exact hashes/paths in that JSON first. Source URLs are retained;
live responses and generated sheet dates may change, so a changed download must
be reviewed and versioned rather than silently accepting a new pin.

- The MDT05 producer documents orthometric heights. This does not identify the
  exact vertical realization or acquisition edition of our WCS extract.
- At Reales, the25m sample is about4.01m below the surveyed pillar-base height;
  its sample centre is8.47m from the mark. A separate5m sample is about1.80m
  below it, at2.75m horizontal offset. These are diagnostic differences, not
  accuracy failures or a correction factor: the mark is not verified bare ground.
- The coastal REDNAP pavement mark differs by about0.25m from its25m source
  sample. It is outside the active cropped domain and cannot validate that domain.
- Exact native void encoding remains unknown. The runtime still rejects missing
  heights, uses one complete positive-height surface and censors boundary contact.
  No mixed flat/slope fallback, filling or survey-based correction was introduced.
- Survey points do not validate spread probabilities, wind conversion, fuel
  moisture, suppression, roads or evacuation travel. Those remain declared
  exercise assumptions. The quarantined earlier terrain remains excluded.

## Component acceptance

The pinned one-worker comparison runner executed38 episodes, repeated all38,
and independently replayed their exported JSON through the existing verifier.
It covers six development families, three policies and slope on/off, plus two
boundary probes. The larger batch is a regression matrix, not38 independent
historical incidents or a held-out training evaluation.

`eval.check_topographic_components` scans those existing exports instead of
launching another simulation batch. It checks1,114 decision observations for
clock/report/weather causality, bounded water, ordered protection delivery,
acknowledgement and execution, absent premature effects, selected actor-view
privacy fields, repeated replay evidence and boundary exclusion. Required
exercised events include readback correction/confirmation, resource dispatch,
arrival and return, clarification, water consumption, wind changes and recovery
from a failed population broadcast. Separate unit regressions cover invalid
actions, reset/session isolation, other safety gates and broader privacy cases.

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.check_topographic_components \
  --rollouts .runtime/simulation-acceptance-v1/rollouts \
  --output .runtime/components-new.json
```

Real localhost OpenEnv clients additionally completed all six cases in both
slope modes and both boundary probes. Full observations, actions, rewards and
exported replay results matched the local Python API. Reports and private
trajectories stay in ignored local storage; their hashes are recorded in the
operator audit. No model, training, live messaging or paid compute was used.

The76 primary/repeat executions took201.207s with75,423,744 bytes peak runner
RSS. Other validation processes ran concurrently, so this is a fresh resource
observation, not an isolated throughput benchmark or comparison with prior timing.

## Test efficiency

The delegated audit found no justified whole-test deletions. Immutable serialized
fixture reuse removes30 redundant completed-episode setups in objective tamper
tests and two repeated false-alarm baselines; every caller gets fresh decoded
objects. A new isolation assertion protects the fixture boundary. Reusing an
unchanged local verification result removes12 extra full replays in the adapter
matrix; adapter verification and independent exported replay remain separate.

One paired local measurement of the objective/control modules changed from
39 tests in9.793s to40 tests in5.567s, including the new isolation test. Treat
this roughly43% reduction as a single measurement, not a stable performance claim.
Never cache mutable sessions, policies, corruption-test files or shared dictionaries.
Optional dependency tests run in their isolated environment; skipped legacy
external-client checks are exercised separately against temporary local servers.

## Remaining gates

- Exact extract metadata and mark-to-ground offsets are unresolved source-fidelity
  work, not silently declared passing. No historical reconstruction is claimed.
- Physical wildfire and response calibration is still absent. Measured weather
  remains causal context, not fire-front physics or an aviation safety limit.
- Broader hard cases, disjoint configuration-group splits and frozen reward/schema
  versions are required before training or benchmark publication.
- Redistribution permissions and release packaging remain separate checks.

This gate establishes tested component integration in the stated synthetic mode.
It does not certify every possible action/scenario, operational realism or safety.
