# Grouped synthetic evaluation v1

Completed 2026-10-24 using implementation commit `378ca75`, one CPU worker and
the locked v1 generator. No model inference, API keys, GPU or paid compute was
used. These are simulated configurations and baseline policy rollouts, not real
wildfire incidents or expert operational demonstrations.

## Dataset and held-out status

- 120 incident-configuration groups, each with three seed/timing/wording variants.
- Train: 80 groups, 240 cases. Validation: 20 groups, 60 cases.
- Test: 20 groups, 60 cases; **no test policy outcomes evaluated**.
- All 360 cases passed structural checks, with disjoint seeds, all six families
  in each split, and no cross-group operational duplicates after removing IDs,
  seeds, source names and wording.
- Public metadata and private configurations exported separately. Dataset: 1.4 MiB.

The 600 completed policy episodes are 300 train/validation cases times two
policies. They are not 600 independent incident groups. Test definitions were
only structurally validated. No policy, factor, deadline or numerical reward
weight was adjusted after observing validation outcomes.

## Baseline outcomes

Training, 240 cases per policy:

- **Control:** 222 full-control passes (92.5%); 6 episodes with exposed people,
  11 with active fire at the horizon. Population-safety component passed 229/240;
  98 simulated delivery failures were observed. Mean objective coverage: 0.9771.
- **Informed random-valid:** 217 full-control passes (90.4%); 10 episodes with
  exposed people, 18 with active fire. Population safety passed 226/240;
  93 delivery failures. Mean objective coverage: 0.9646.

Validation, 60 cases per policy:

- **Control:** 55 full-control passes (91.7%); zero exposed episodes, 1 active-fire
  episode. Population safety passed 56/60, so four cases still had unfinished
  population movement. Incident resolution passed 59/60; 30 delivery failures.
- **Informed random-valid:** 53 full-control passes (88.3%); zero exposed episodes,
  4 active-fire episodes. Population safety passed 56/60; 28 delivery failures.

All 600 completed episodes passed seeded public replay and independent regional
accounting, with zero invalid actions. Every public trajectory was saved. The
controller's training population failures and validation movement/fire failures
remain in the dataset; successful mean coverage does not erase those failures.

The random control samples subsets of the informed controller's candidates. It
is not an uninformed random action policy. Differences here are descriptive,
not evidence of statistical superiority or real-world effectiveness. Failure
does not establish that a scenario was unavoidably lost; feasibility and
counterfactual improvements require further analysis.

## CPU cost and artifact integrity

- Pilot: 12 episodes across six training groups, one seed per group; 10.87 seconds
  and 33.2 MiB peak process RSS. Both baselines passed these easier pilot cases.
- Full train batch: 480 episodes, 405.67 seconds, 37.1 MiB peak RSS.
- Validation batch: 120 episodes, 92.47 seconds, 44.6 MiB peak RSS.
- Combined batch wall time: 498.14 seconds (8 minutes 18 seconds), including
  replay, accounting and artifact writes. Separate sequential processes were used.
- Saved training artifacts: 11 MiB; validation artifacts: 2.7 MiB.
- A cache-resume check reused all 480 completed training records in 0.068 seconds,
  without rerunning their policies. Resume timing is distinct from original
  evaluation cost and replaces the latest invocation timing in `summary.json`.

Every record is written after verification through an atomic temporary-file
rename and has a content checksum. Resume verifies experiment/source identity
and record integrity. All 600 trajectory files were counted and source hashes
matched the evaluation code. Peak RSS is a local process high-water mark,
not a GPU or model-inference memory estimate.

Validation also ran 183 core tests with 9 skips and 3 extraction checks. The
12 new generator/transport tests passed, including delay/failure
effects, retry accounting, static wind/geometry validation, split isolation,
test-execution guard, replay binding, corruption rejection and cached resume.
An installed wheel client reproduced a generated episode with a failed delivery
and replayed its saved trajectory. The original frozen regional benchmark lock
remained unchanged.

## Reproduction and artifacts

See [DATASET.md](DATASET.md) for build, audit, pilot, full evaluation and resume
commands. The local artifacts for this run are:

- `.runtime/control-dataset-v1-final/`: public split metadata, private cases and manifest.
- `.runtime/generated-pilot-v1-final/`: six-family pilot records and public trajectories.
- `.runtime/generated-train-v1/`: 480 verified policy records and trajectories.
- `.runtime/generated-validation-v1/`: 120 verified policy records and trajectories.

No generated test evaluation directory was created. These data artifacts are
local and reproducible; they have not been published to a dataset host.

Pinned identifiers:

- Generator: `wildfire-control-generator-v1`.
- Catalog lock SHA-256: `208c663c51fdbb1d8ed2d4677089c442467906bcd4b75f3bcc40b84807cc65a4`.
- Concrete cases SHA-256: `449ca214c64a6f9497f57f4bf555fa2402e520930e1d65b29c6054cecf15f7ce`.
- Evaluation source SHA-256: `08c57d3d6e376fbd998a814c891b17f9beda10e700235d6ec9e6b80a344e2426`.
- Ops interface v4 / task pack v0.4.0 / evidence v2 / reward v3 / scenario adapter v1.

## Next gate

Analyse training failures before building demonstrations or a retrieval bank;
do not label every baseline trajectory as a successful example. Use validation
for development, keep the 20 test groups unconsumed until choosing the policy,
and report every safety/outcome component. The equal objective fraction still
needs safety-priority calibration and expert/domain review. This release does
not validate real-world wildfire control or establish an optimal policy.
