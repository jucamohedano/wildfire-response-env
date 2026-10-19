# Director adapter acceptance — 2026-10-19

This measures `regional-director-tools-v1` protocol correctness, **not model
performance**. All responses were scripted or injected test fixtures. No provider
credentials were loaded, no model requests were sent, and no training or
publication was executed. HTTP 429 behavior was tested with an injected exception,
not a live request.

## Checks

- Full environment test discovery: **88 tests, 79 passed and 9 optional SDK tests
  skipped** in the base Python installation, **64.970 seconds**.
- With OpenEnv 0.7.0 installed: **8 additional tests passed**, including the external
  v2 client, full director check/submit parity, session reset isolation and both
  existing v1 local-adapter/discovery tests, **5.405 seconds**. The existing v1
  external-server test was not re-executed in this stage.
- The benchmark fingerprint remains
  `e882e0f8ba58246ef3e4c3d271d32a6d54a1756d980be52bcccb524ff84ad840`.
  No frozen simulator, baseline, scenario or reward input was changed.
- A fresh wheel installed outside the repository successfully verified that lock,
  imported the director/OpenEnv bridge, read the packaged docs/results, and
  completed an independently verified, exact-replayed scripted episode.
- The temporary localhost server was stopped after testing. Docker remains
  unavailable; no container build or hosted deployment is claimed.

## Complete training-scenario smoke

`regional-train-001`, seed 30001, 60-decision horizon, at most 120 total requests:

- **21 decisions**, completing naturally at model minute **42**.
- **42 scripted requests**, **21 non-advancing checks**, zero protocol errors and
  zero invalid actions.
- Reward **-0.007678798411122146**, equal to exact replay and the cooperative
  reference at the same budget. Exposure and unresolved-fire components are zero;
  153 committed resource-minutes and burned fraction 0.000028798411122144986.
- The isolated-wheel run, including the matched baseline, took **0.906 seconds**.
  This excludes provider latency and is not a model inference measurement.

The scripted transport deliberately uses the cooperative reference to exercise
the same model-facing tools. Equality is expected and tests the wiring; it cannot
show that a learned policy understands the task, adapts successfully, or beats
any baseline. The external session reproduces the same public observations,
rewards and lifecycle flags as the local API for these scripted actions.

Tests also cover private-field exclusion, retention of clear sightings and report
times, revised drafts after rejection, the three-check limit, malformed/nonfinite/
oversized JSON, unknown or truncated responses, budget exhaustion, auto/forced
tool modes and stop-without-retry behavior. Reset clears per-episode history and
request records. Incomplete runs are not assigned completed-episode scores.

## Next execution gate

The first adapter was implemented after the frozen baseline audit. Mechanical
training-scenario tests are not a held-out model-policy generalization result.
All held-out outcomes remain reported in [the audit](../HELDOUT.md), including
warning/route sensitivity; no cases were removed or tuned away.

Before a live trial, explicitly unpause provider calls, verify a current free model
supports the chosen tool parameters, choose the request/token/decision budget and
use a new training-scenario output directory. Keep immediate stop on limits and
preserve incomplete attempts. Realism work, a fresh final model evaluation set,
license/asset clearance and container validation remain separate gates before
training or publication claims.
