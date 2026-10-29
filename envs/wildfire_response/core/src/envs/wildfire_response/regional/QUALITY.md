# Regional v2 quality audit — 2026-10-19

This records the earlier training-calibration stage. The subsequent
[frozen held-out audit](HELDOUT.md) and [director adapter](director/README.md)
advance its next gates without changing its recorded inputs or rewards.

This alpha audits task contracts, programmatic grading and weak policies.
It does **not** claim a proven optimum,
historical incident reconstruction, validated emergency procedures or publication
readiness. No model-provider requests or training jobs were used for this audit.

## Contract and objective changes

- `regional-response-v2-alpha.2` adds three bounded, non-advancing action checks
  per decision. They validate only the current public contract; they do not
  forecast outcomes or reveal future events. OpenEnv exposes these through the
  typed `operation: check` action, separately from `operation: advance`.
- Received sensor sightings now create persistent legal target IDs. A fire edge
  outside the initial report sector can therefore be addressed once observed,
  without generating targets from hidden fire. The reference policy also keeps
  unverified reports eligible for investigation instead of forgetting them by age.
- `regional-outcome-cost-v2` adds a terminal cost of 2 for active or scheduled fire
  left unresolved. The previous objective could prefer warning everyone and
  leaving the incident burning. This is an explicit reward-version change, not a
  comparable improvement over the earlier reward-v1 results in `RESULTS.md`.
- An independent accounting implementation reconstructs outcome components and
  reward from private raw checkpoints, minute records and communications. It also
  checks inventory conservation, lifecycle transitions, water capacities/refill
  provenance, finite terrain values and one full-load aircraft drop per sortie.
  It does not reuse the environment's cost functions. Replay is an additional,
  separate check, not an independent fire-physics implementation.

## Draft tasks and calibration

`regional-task-pack-v0.2` contains 36 synthetic configurations: 24 training,
6 validation and 6 test. Every split includes all six families. Configuration
groups, locations and seeds are disjoint; templates, geography and simulator
assumptions are shared. This is not a held-out-real-incident or held-out-map claim.
Public JSONL files contain task metadata; full configurations and report-generation
annotations are exported separately under `private/` for evaluator use only.

Training-only calibration exposed two engineering problems before the current run:
off-axis variants removed the population-risk signal, and escaped observed fire
could lack legal targets. Synthetic wind alignment and observation-derived targets
corrected these problems. No tasks were silently discarded. Validation and test
outcomes were not used to select these corrections, policies or reward weights.

The current training audit runs all 24 training tasks against passive, seeded
random-valid, blanket-warning and cooperative policies, at the same 60-decision
budget. Every rollout is independently checked and replayed exactly.

Frozen-source audit at revision `a497959ce6f4e2f8fa3fafa71e52166e4a77aded`,
under `.runtime/regional-v02-frozen-calibration-2026-10-19/`:

- **96 episodes:** all independent accounting checks and exact replays passed;
  zero invalid actions and zero weak-policy difficulty flags.
- **Passive:** mean reward -4.048260; mean population exposure fraction 0.023810;
  4/24 natural completions.
- **Random-valid:** mean reward -1.152632; zero population exposure;
  0/24 natural completions.
- **Blanket-warning:** mean reward -1.673002; zero population exposure;
  4/24 natural completions.
- **Cooperative:** mean reward -0.017922; zero population exposure;
  24/24 natural completions.
- Sequential CPU evaluation, including replay: **87.284 seconds**, peak RSS
  **32,772 KiB**. This is a local measurement, not a hosting performance guarantee.

All policy rewards match the preceding exploratory run exactly. That run took
80.598 seconds at 32,412 KiB peak RSS; the repeat illustrates normal local runtime
variation. The frozen task manifest records SHA256
`7907b0183a72be356a443f17557ee511ecf3a9e757242f58c524ff2ce3ef3c1c`.

Natural completion requires no remaining incident events/reports, active fire,
moving population or assigned resources. A policy can avoid exposure yet still
truncate because it leaves fire unresolved or resources deployed. False-alarm
restraint can beat the deliberately over-mobilizing reference policy. Weak-policy
flags are diagnostics, not proof of challenge, optimality or model readiness;
`ready_for_model_training` remains false pending a separate readiness review.

## Verification and transport

- 67 core, policy, evidence and regional tests passed.
- Three additional OpenEnv 0.7.0 adapter tests passed, including an external
  WebSocket client whose observations, rewards and end flags match the local API.
- Both existing v1 local-adapter/discovery tests also passed; the original server
  and task contract remain separate from this v2 transport.
- A checked external cooperative rollout completed `regional-train-001` at model
  minute 42 with reward -0.007678798411122146.
- The dedicated v2 server supports one active episode and exposes no checkpoint,
  private generation label or future-event endpoint. The v1 server is unchanged.
- A freshly built wheel was installed into an isolated directory and run from
  outside the repository. Its false-alarm episode completed and replayed exactly;
  v2 task discovery and the packaged OpenEnv manifest were also checked.
- A container recipe and manifest are provided; Docker is unavailable locally,
  so no image build or hosted deployment has been validated.

## Reproduce and next gates

```sh
python -m unittest envs.wildfire_response.tests.test_regional \
  envs.wildfire_response.tests.test_regional_quality
python -m envs.wildfire_response.regional.evaluate \
  --output .runtime/regional-training-audit-new --split train
python -m envs.wildfire_response.regional.openenv.server --port 8001
# In another terminal, after installing the optional OpenEnv dependencies:
REGIONAL_TEST_URL=http://127.0.0.1:8001 python -m unittest \
  envs.wildfire_response.tests.test_regional_openenv
```

Use a fresh output directory. Preserve manifests, public trajectories, private
evaluator records and source digests together for reproducibility, but never send
private records to a model policy. Old schema/reward versions require their
recorded source revision. Do not mix scores across versions.

Before model comparison, freeze this task/objective version and run held-out
evaluation without tuning on its outcomes. A v2 model-tool adapter and budgeted
model trial remain separate work; provider requests remain paused. Before public
publication, resolve the missing root code licence and asset/source redistribution
rights, validate the container, and document a stronger held-out/generalization
story. Real base coordinates, road travel, dispatch logs and measured fire or
suppression dynamics are still absent. Source-grounded names and capability
classes must not be presented as real fleet allocations or operational validation.
