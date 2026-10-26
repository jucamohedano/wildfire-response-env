# Efficient grouped scenario generation

Generator `wildfire-control-generator-v1` defines 120 incident-configuration
groups. Each group owns three seeded timing/wording variants, giving 360 cases:
80/20/20 groups and 240/60/60 cases in train/validation/test. All six incident
families appear in every split. This is synthetic simulation data; no generated
transcript, readiness value or outcome is represented as a real observation.

## Composition and leakage checks

Configuration factors cover fire pressure and overlapping ignitions, report
delay, wind strength/change, initial resource commitments, transport delay and
first-attempt failure, report noise and ignition placement. Incoming templates
represent phone, chat, responder and satellite-summary channels. Reports include
duplicates, inaccurate location claims, conflicting locations and unlocated
messages. Their observation and receipt times remain separate.

Groups are assigned before seeds or wording variants. All variants stay in the
same split; seeds do not overlap. Structural auditing additionally removes IDs,
seeds, source names and wording, then checks operational configuration signatures
for cross-group duplicates. Every split contains all families and distinct factor
combinations. These checks prevent seed/wording-only copies crossing splits;
they do not establish independence from the shared simulator or real incidents.

The generator uses data-only composition and caches validated cases. It does not
call a language model, render a scene, launch a GPU, or copy the simulator.
`dataset_lock.json` pins catalog, concrete cases, generator source and base-pack
hashes. Changed generation inputs fail verification instead of silently changing
an experiment. Regenerate a deliberately versioned lock when releasing new data.

Structural validation covers geometry, valid wind components, event/report times,
unique IDs, bounded report counts, existing resources and legal readiness/delay
values. Static validation covers all 360 cases without evaluating test policies.
It does not prove that every stress case is winnable. Baseline failures must be
retained and analysed; do not discard them or call them impossible without
additional feasibility evidence.

## Transport and resource mechanics

The ops scenario adapter extends the frozen regional environment with assumed
initial resource commitments and delayed requests/messages. A held unit is
unavailable initially and returns to availability at its configured time. The
agent observes current availability, never the private future release schedule.
Pending resource requests are public so a client can avoid duplicate requests.

The first evacuation attempt can fail when its scheduled delivery is processed.
Failure emits an acknowledgement and does not initiate population movement.
Subsequent attempts can deliver; delivered evacuation begins movement through
the existing engine mechanism. `message_deliveries` exposes pending/delivered/
failed attempts, IDs, issue/completion times and due times, but never a future
failure flag. An acknowledgement establishes transport status, not safety.

Evidence contract v2/reward v3 allow one retry per acknowledged failed delivery
without classifying it as a redundant notice. Sending again while pending or
after delivery remains redundant. The four equal terminal objective fractions
are unchanged; their safety/calibration limitations still apply. Baselines use
only public delivery status to decide whether to retry.

## Build and audit

From the repository root with Python 3.12 and the core installed or on its path:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.dataset audit
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.dataset build \
  --output .runtime/control-dataset-v1-final
```

Public split JSONL files contain metadata only: IDs, group, family, split, seed,
variant and integrity fields. Full configurations are separate under `private/`.
The manifest records structural validation and that test policy outcomes have
not been evaluated by the build operation. These manifests/configurations are
for the trainer/evaluator; an agent receives only its current public observation.
Generated case IDs pin their seed; use another variant ID instead of overriding
its seed and silently mixing a benchmark case with an unregistered rollout.

## CPU evaluation and trajectories

Start with six groups and one variant per group before scaling:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.dataset evaluate \
  --split train --max-groups 6 --variants 1 --traces all --output .runtime/generated-pilot-v1-final
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.dataset evaluate \
  --split train --traces all --output .runtime/generated-train-v1
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.dataset evaluate \
  --split validation --traces all --output .runtime/generated-validation-v1
```

Default policies are `control` and constrained `random_valid`. They use the same
registered cases/seeds, one CPU worker, no paid inference and no GPU. Each
episode undergoes public replay and independent regional accounting. Only one
episode's trajectories are retained in memory; records are written atomically
per case/policy. `--traces all|failures|none` controls stored public trajectories.
Completed records include checksums. `--resume` reuses verified records only when
the source hash, catalog, cases, policies and output settings match; corrupt
records or mismatched runs are rejected. No model-training job is launched.
Invocation timing in `summary.json` describes the latest attempt; a cache-only
resume is not a measurement of original policy-evaluation cost. See the
[recorded batch results](DATASET_RESULTS.md) for the initial train/validation run.

The output `run.json` records the exact experiment; per-case records contain
summary and evaluator verification, while `*.trajectory.json.gz` contains the
public policy trajectory. Evaluator records must not be given to the agent.
`summary.json` reports every objective, exposed/unresolved episode counts,
delivery failures, invalid actions, runtime and process peak RSS. Always inspect
safety components before interpreting mean objective coverage as performance.

## Held-out protocol

Build/audit/test-harness checks may inspect test configurations for structural
validity, but this release's baseline execution uses train and validation only.
The test evaluator requires both `--split test` and `--consume-test`, recording
consumption in that run. Do not invoke it until choosing a policy and freezing
its selection procedure. If its outcomes guide subsequent development, retire
that test set and release a new one.

This is an accidental-use guard and an evaluation protocol, not a secrecy or
security boundary: reproducible generator code and case definitions are available
to a developer. The model-facing tools do not expose those private definitions.
The 20 test groups are still a modest engineering benchmark, not deployment
validation. More seeds, broader failure models, expert feasibility review and
calibrated safety priorities remain necessary for stronger claims.

## Compatibility

Ops interface v4, task pack v0.4.0, scenario adapter v1, evidence v2, reward v3.
The original regional benchmark files and physics are unchanged. Old ops v3
traces replay from commit `3ea2778`, v2 from `3202d49`, and v1 from `fdc2a10`.
The twelve earlier development fixtures remain available; generated split cases
are listed explicitly using `list_ops_tasks('control', split='train')`.
