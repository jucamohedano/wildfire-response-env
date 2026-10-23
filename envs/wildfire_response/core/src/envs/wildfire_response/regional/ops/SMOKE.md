# Ops readback development smoke run

Historical interface-v2 run at commit `3202d49`. Current broader objectives and
results are in [OBJECTIVES.md](OBJECTIVES.md) and [CONTROL_SMOKE.md](CONTROL_SMOKE.md).

Run on 2026-10-23 with one CPU worker, no model inference, and no API keys.
This is a procedure/execution check across six development fixtures and three
policies, not a held-out benchmark or evidence of operational firefighting skill.

## Configuration and reproduction

- Interface: `wildfire-ops-control-center-v2`.
- Communication protocol: `wildfire-ops-readback-v1`.
- Task pack: `wildfire-ops-task-pack-v0.2.0`, six tasks (`ops-000`–`ops-005`).
- Reward: `wildfire-ops-objective-fraction-v1`.
- Simulator: `regional-response-v2-alpha.2`.
- Seeds: underlying scenario defaults `20000`–`20005`; the random policy uses the
  same episode seed for its action sampling.
- Budget: 60 decisions, two model minutes per decision.

From the repository root, with Python 3.12 and the core installed or on the path:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --output .runtime/ops-readback-smoke
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --replay .runtime/ops-readback-smoke/ops-001/dispatch/trajectory.json.gz
```

Use a fresh output directory. The suite exports a public trace, summary and
verification result per task/policy, an aggregate summary, and a separate
evaluator task manifest. The public trajectory does not disclose the synthetic
readback-fault schedule. Its version/hash fields identify the task and simulator
data; the source digest is additional rollout metadata.

## Results

- All 18 episodes passed public replay and core accounting verification.
- `passive`: score 0 on all six tasks; 0 corrections, 0 invalid actions.
- `random_valid`: score 1 on all six tasks; 2 corrections, 0 invalid actions.
- `dispatch`: score 1 on all six tasks; 2 corrections, 0 invalid actions.
- Each policy naturally terminated the false-alarm episode and reached the time
  limit in the other five. Thus 15 of 18 episodes were truncated.
- Total: 941 decisions, 7.5171 seconds wall time including verification and
  artifact writes; peak process resident memory 35,278,848 bytes (33.6 MiB).

Runtime is a local measurement without inference. The reported resident memory
is the process high-water mark across sequential episodes, including replay and
verification; it is not a per-episode memory requirement or GPU estimate.

Core regression checks: 153 tests ran with 9 skipped; root extraction checks:
3 tests passed. The wheel built successfully. Protocol tests cover the matching
readback gate, corrections and delivery times, atomic failures, stale offers,
reset isolation, JSON round-trip replay and tampered readback rejection.
An external client imported the wheel from an isolated target directory, replayed
the faulted `ops-001` trajectory, and completed/verified an `ops-003` episode.

## Interpretation and next gate

The deterministic baseline demonstrates that a public client can request,
correct, confirm, authorize, observe arrival, and release a unit through the same
API a model would use. Correction and confirmation alone earn no reward.

The random control also completes every arrival assignment under these budgets.
The current tasks therefore do not distinguish prompt, careful dispatch from a
randomly delayed variant. Most fires remain unresolved at the horizon even with
full objective credit, because these tasks only assign reconnaissance arrivals.
Do not use these scores to claim that a policy manages fires effectively.

Before model selection or RL, add evidence-handling and population-protection
tasks with independently checked completion conditions, pressure-test their
timing and resource constraints, and create new incident-grouped held-out cases.
Fault timing must vary beyond the two fixed development examples. Avoid adjusting
deadlines merely to make a particular baseline fail.

## Recorded hashes

- Core pack SHA-256: `db20dc7806eac7d5b0b07cffd06931c9d40120f0813bd8b6fb80ff1d13f6bcf0`.
- Task manifest SHA-256: `de63b1e15b9c9ac0460a3cbc10a381d2584af4247d690062fea5e3d23bd5ff23`.
- Source SHA-256: `8cb3a159cb408f183d40c8b0bbc3c870ed81f51e5b5431d0e19a32308f520ba3`.
