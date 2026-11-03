# Benchmark qualification v2

The old absolute safety/terrain score can approach one when the agent does
nothing. Most of the landscape remains untouched, and most current exercises
do not expose people under the passive policy. This is a scoring and task
qualification problem; it is not evidence that a model solved the incident.

This opt-in candidate preserves every frozen scenario, trajectory and v1/v2
result. It adds a terminal evaluator alongside the packaged environment. The
environment's existing reward is retained as a **raw diagnostic**, not the new
benchmark grade. No actor tool, observation, fire dynamics or checkpoint changes.

## Objective and grade

The outcome cost remains population first:

```text
C = 0.8 × newly exposed / initially eligible population
  + 0.2 × newly burned cells / initially eligible burnable cells

grade = clip((C_passive − C_agent) / (C_passive − C_reference), 0, 1)
```

Inherited damage/exposure is excluded using the initial private audit. All three
runs must share scenario, seed, landscape, network, code, observation contract,
initial audit, denominators and cell area. The reference is the best observed
scripted online policy in the declared candidate set, **not a proven optimum**.
It must not increase exposure relative to passive behavior. A positive cost gap
requires at least one person protected, or equal exposure and at least one cell
saved. These are minimum measurement-resolution checks, not realism evidence.

Where a reference qualifies, passive behavior scores zero and matching the
reference scores one. Actions, message volume and completion earn no bonus.
Keep the raw weighted components, absolute improvement, signed normalized gain
and a `reference_exceeded` flag: beating a reference is possible, and clipping
must not hide it. A task without demonstrated positive headroom is unqualified;
it receives no invented score.

The grade ranks policies under the same 80/20 cost. Averaging task-relative
grades gives each exercise equal weight; it is not an estimate of aggregate
real-world harm prevented. A high grade on a fire-area-only exercise does not
demonstrate population-protection skill.

## Qualification before ranking

The checked-in [protocol](../data/benchmark_v2/protocol.json) is
`candidate_not_released`. An official aggregate requires all 24 expected tasks,
four per operational family, positive reference headroom, and a separately
recorded **weak active** policy scoring at most 0.6 on each task. That policy
must be preregistered, use only delivered observations and perform genuine
operational actions. Renaming passive behavior is not a valid control. Its
verified outcome, source hash, initial-world identity and policy label are
required; a standalone number cannot unlock qualification.

The protocol preregisters the existing `conservative` policy: it releases one
working unit, sends one shelter instruction at a known stationary location,
requests one centre update, then waits. Its source, dispatcher dependency,
configuration, physical kernel and observation contract are pinned before new
runs. Collection must record matching policy receipts in the report and saved
execution, with their canonical hash in the run pins. Activity is recounted from
valid committed operational orders; reads, waits and status queries do not count.

The 0.6 threshold is an exercise-design choice, not a physical constant. Fix it
before collecting the control runs. If weak policies saturate the grade,
redesign under new task identities or improve a valid reference, then freeze
and rerun qualification. Do not tune thresholds to rescue a failing catalog.

Numeric qualification is separate from explicit protocol release. Candidate
diagnostics never become official scores automatically. Missing tasks and
unqualified references prevent the full-catalog aggregate; they are not dropped
from a smaller, more flattering mean. Private anchors and per-case curation
outcomes remain outside Git and actor observations.

## Execution verifier

`wildfire_benchmark_v2.BenchmarkVerifier` binds the protocol and anchors to an
externally supplied canonical SHA256. It checks loaded grading code against
the recorded source identity, execution/replay evidence, independent reward
agreement, every minute of outcome accounting, reset identity, movement and
capacity coverage, and checkpoint/query-independence evidence. It recomputes
exposure and newly burned cells from the private audit before grading.

Infrastructure failures, missing evidence, censoring, incomplete runs and pin
mismatches remain unscored. A verified three-invalid-action abandonment is a
policy failure worth zero on an otherwise qualified task; it does not fabricate
a full-day outcome. Verified late recovery or terminal assistance retains the
score with separate adverse-outcome flags, as required by the existing policy.
Those flags and a high grade do not establish operational safety.

## Reproduce privately

Install the small, dependency-free evaluator with Python 3.12+:

```sh
python -m pip install -e envs/wildfire_benchmark_v2
python -m eval.qualify_benchmark_v2 \
  --source-report /private/ops-v3/report.json \
  --expected-source-report-sha256 "$REVIEWED_REPORT_SHA256" \
  --task-freeze /private/evaluation-freeze.json \
  --expected-task-freeze-sha256 "$REVIEWED_FREEZE_SHA256" \
  --data-dir /private/evaluation-pack \
  --output /private/new-qualification
```

This reads already recorded runs one at a time; it does not run simulations or
models. It verifies the prepared pack and frozen task rows, writes owner-only
anchors/diagnostics into a fresh directory, and preserves its source files.
Its address space is capped at 256 MiB and each evidence file has a size bound.

Use the pinned anchor manifest for a subsequent recorded execution:

```sh
python -m eval.grade_benchmark_v2 \
  --manifest /private/new-qualification/anchors.json \
  --expected-manifest-sha256 "$REVIEWED_ANCHOR_SHA256" \
  --source-report /private/run/report.json \
  --expected-source-report-sha256 "$REVIEWED_RUN_REPORT_SHA256" \
  --saved-run /private/run/private/episode.json.gz \
  --expected-saved-sha256 "$REVIEWED_EPISODE_SHA256" \
  --task-id "$TASK_ID" --policy "$POLICY" \
  --allow-candidate-diagnostic --output /private/new-verdict.json
```

Only the trusted evaluator receives anchors and private audits. Removing the
diagnostic flag never overrides the release gate. This is evaluation tooling,
not a changed training reward or a new public dataset release.

## Reference designs reviewed

The useful pattern is **controllable outcome cost + reference normalization +
trivial-policy screening + terminal eligibility audit**, all ordinary code.

- [PortSim's pinned reward](https://github.com/adithya-s-k/FineEnvs/blob/b0f4c2f9526e3c45d608b4f92f6ec6c71fecc152/07-simulation-environments/portsim-v1/envs/berth_planning/core/berth_core/reward.py#L71-L108)
  subtracts a reference cost and scales by the controllable gap. Its
  [generator screening](https://github.com/adithya-s-k/FineEnvs/blob/b0f4c2f9526e3c45d608b4f92f6ec6c71fecc152/07-simulation-environments/portsim-v1/envs/berth_planning/core/berth_core/dock.py#L270-L291)
  rejects easy instances and checks solver proof status. Later references can
  be best found rather than proven optimal.
- [AirOps control-centre grading](https://github.com/earakely-scale/agentenv-airops-plugin/blob/4ebd72663d0e7cc7f7b71a141ffb2c2dfde3c2c7/src/agentenv_airops/occ/day.py#L76-L89)
  compares costs against best-found and baseline references. Its
  [eligibility verifier](https://github.com/earakely-scale/agentenv-airops-plugin/blob/4ebd72663d0e7cc7f7b71a141ffb2c2dfde3c2c7/src/agentenv_airops/bundles/airops-occ3/artifacts/airops-occ3-verifier/verify.py#L11-L23)
  accepts only eligible recorded grades. Neither design needs an LLM judge.

Their exact curves, bonuses, aviation costs and thresholds do not transfer to
wildfire response. Our linear improvement fraction intentionally gives passive
behavior zero and retains the requested 80/20 harm objective.

## Remaining release evidence

Historical projections are not fresh model evaluations. Existing saved runs
can establish raw accounting and reference headroom, but cannot establish the
missing weak-active baseline or robustness across new seeds. Complete those
checks on memory-safe private CPU compute before claiming a ranked benchmark.

The current packaged runtime also has one fixed incident sector: `route_options`
accepts only `sector_id='incident'`, and suppression operates near the assigned
target. This candidate does not add multi-front command capabilities or claim
competence at them. Reward qualification cannot substitute for validating the
available actions, scenario sensitivity, fire physics or real-world calibration.
