# Incident-day runtime validation

Implemented on 29 October 2026; tracked by [#50](https://github.com/jucamohedano/wildfire-response-env/issues/50) and [PR #52](https://github.com/jucamohedano/wildfire-response-env/pull/52).

The [complete report](../results/runtime-validation-20261029.html) records **48 full-day runs**: all 12 active development configurations crossed with passive, dispatcher, conservative and stress policies. Every run reaches 1,440 minutes, replays exactly and independently reproduces reward. There are 261 exact checkpoint-continuation checks, 69,120 observed operational ticks and no blocking runtime contradictions. The dedicated OpenEnv regression runtime passed 408 tests with no skips, plus 67 subtests. These are related development exercises, not independent historical incidents or empirical calibration.

## Delivered checks

1. **Complete coverage.** `eval/validate_incident_day_runtime.py` derives the domain from the active registry and matched input/network identities. Aliases cannot substitute one representation for another. Missing, blocked and interrupted cells remain explicit; a smaller policy set cannot pass. The manifest pins the engine, validator sources, scenario, input/network and lifecycle identities.
2. **Every operation.** `eval/incident_day_action_checks.py` covers all eight tools and all committed kinds: knowledge/report reads; approach and destination previews; draft checks; resource requests, authorizations and releases; shelter and evacuation/relocation; centre status; and bounded waiting. Reads/drafts cost zero minutes, commits one, waits at most 60 with evidenced early wake. Accepted commands and downstream effects are reported separately.
3. **Contextual bounds and invariants.** Setup, mobilization, refill and rest use the applicable scenario profile. Inherited, interrupted, blocked and censored spans remain distinct. Causal deliveries, finite stocks/duty, population counts, destination capacity and command timing are checked. An unavailable route may prevent arrival; it is never clipped into an arbitrary maximum ETA. Bounds describe the exercise, not aviation or labour law.
4. **Actual movement evidence.** `eval/incident_day_movement_checks.py` observes operations privately without changing the fire kernel or its audit. It independently interpolates timed paths, checks directed road geometry, modeled aircraft departures/drops and destination capacity each tick. Unverifiable segments are unsupported rather than successful checks. Physical access and real aviation clearance still require independent evidence.
5. **Reproducible behavior and recovery.** Public-only policies have versioned JSON checkpoints. Extra reads do not alter weather, fire or communications continuation. Phase-boundary continuations compare actions, public results, fire state and audits. The runner atomically saves progress with environment, policy and observer state; a missing or truncated public report can be regenerated, including after the last completed cell. Actual interrupted and completed-report recovery was exercised without changing scientific results.
6. **Fault detection.** Tests corrupt timing, movement, direction, stocks, accounting, matrix membership and checkpoints, with valid controls. The first matrix exposed an observer false positive for crews stopping for assistance; the specific transition was corrected while retaining off-path and stalled-active-travel detection. That preliminary batch is not the release evidence.

## Reproduce on CPU

Install the core and download the pinned prepared-data release using the [download guide](hub-runtime.md). Then run from the repository root:

```bash
PYTHONPATH=envs/wildfire_response/core/src:. python -m eval.validate_incident_day_runtime \
  --data-dir .runtime/wildfire-response-spain \
  --output /tmp/my-wildfire-matrix --wall-seconds 3600

# Resume the same output with unchanged code, policies and data.
PYTHONPATH=envs/wildfire_response/core/src:. python -m eval.validate_incident_day_runtime \
  --data-dir .runtime/wildfire-response-spain \
  --output /tmp/my-wildfire-matrix --wall-seconds 3600 --resume
```

One CPU worker runs the matrix. A budget stop is incomplete and resumable, not a successful benchmark score. Private journals, detailed findings, audits and checkpoints stay under the output's private directory. Shared reports and Hub tables contain aggregate evidence only; validation rows are excluded from actor input and training labels.

## Still separate

The new aerial review suggests land at all 27 centres left unclassified by the cached land-cover query. It does not recover historical fuel or whole-cell water fractions; existing source masks and guards are unchanged. Source/terrain approval and operational/physical calibration remain in #34/#42. Reward calibration remains in #27, and a representative grouped training/held-out corpus remains in #18. Do not tune on held-out outcomes or change archived two-hour IDs, seeds, scores or replay contracts.

No paid inference, GPU training or broad scenario regeneration was used. The owner separately authorized a corrected private Hub release; public publication remains outside this delivery.
