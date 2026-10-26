# Opt-in benchmark training

`train/benchmark.py` is separate from the legacy `train/grpo.py` recipe. Its CPU
commands never load a model, make provider requests, download weights, rent
hardware, or establish model readiness. Catalog discovery is public IDs/counts
only: 160 training, 40 validation, 40 test, and 12 development tasks.

## Local configuration and CPU checks

Use the existing `.runtime/openenv-test-venv/bin/python` with
`PYTHONPATH=.:envs/wildfire_response/core/src:envs/wildfire_response/openenv`.
The configuration JSON contains `BenchmarkEnv` constructor arguments:

```json
{
  "prepared_inputs": "/absolute/path/prepared_inputs.json",
  "topographic_inputs": "/absolute/path/topographic_inputs.json",
  "expected_prepared_sha256": "PINNED_PREPARED_SHA256",
  "expected_topographic_sha256": "PINNED_TOPOGRAPHIC_SHA256",
  "slope_enabled": true,
  "allow_synthetic_conditions": true,
  "profile": "central"
}
```

The default catalog is compiled from those pinned inputs. A local `catalog` and
`expected_catalog_sha256` can additionally pin its exact contents. A configuration
cannot override the 60 decisions, two minutes per decision, or 120-minute horizon.

```sh
python train/benchmark.py inspect --config /path/benchmark-config.json
python train/benchmark.py dry-run --config /path/benchmark-config.json --task-id TRAIN_ID
python train/benchmark.py export-demo --config /path/benchmark-config.json \
  --task-id TRAIN_ID --policy cooperative --output /path/fresh-demo.json
python train/benchmark.py verify-demo --config /path/benchmark-config.json \
  --demonstrations /path/fresh-demo.json
python train/benchmark.py freeze-bank --config /path/benchmark-config.json \
  --demonstrations /path/fresh-demo.json --output /path/fresh-bank.json
```

`inspect` includes `first_training_task`. `dry-run` explicitly commits one wait,
then scores with tagged passive finalization; its output is **not an expert
demonstration or model readiness evidence**. A boundary-censored dry run fails
instead of reporting a trainable reward. `export-demo` runs a complete public
`PublicDispatcher(policy='cooperative'|'dispatcher')`; exceptions, invalid
commits, censoring, early stopping, and verification failures prevent export.
No tactical fallback completes a failed policy.

Recorded training trajectories can be imported using `import-trajectory
--trajectory RECORDING --trajectory-sha256 SHA256 --output FRESH_DEMO`, with the
same `--config`. `RECORDING` is an envelope with
`version="wildfire-recorded-training-trajectory-v1"`,
`source="recorded_training_trajectory"`, integer `provider_failures=0`, integer
`fallback_actions=0`, one `"model"` entry in `origins` per transition, and the
complete core export in `trajectory`. The externally supplied checksum is
`checksum(recording)` from this module, not a byte hash of pretty-printed JSON.
Raw traces without failure/origin provenance are refused. The complete trace is
independently replayed before generating labels. Artifact self-checksums detect
changes; replay also checks examples, actions, observations, reward, and catalog.

## Actor data and retrieval

All resets, demonstrations, retrieval builds, SFT examples, and GRPO rollouts
require membership in the frozen **train** split, checked before reset. Changing
the task string or catalog checksum cannot import held-out examples.

Model observations and instructions use `regional.benchmark_v1.model`, contract
`wildfire-benchmark-model-tools-v1`, and observation version
`wildfire-benchmark-delivered-evidence-v1`. The objective is 80% population
protection and 20% unburned footprint. Unknown dynamic values stay null; exposure
truth and evaluator metadata never enter model context. The initial context
contains the latest 20 reports/events and delivered counts. `get_reports` and
`get_events` support `ids`, `offset`, and `limit` (1–20), with three queries per
decision; the query helper selects only already delivered, public records.

Committing tools share the compact v2 action parser and current core validation.
Checks and evidence reads do not advance time. RL preserves an invalid commit's
engine feedback and two-minute time advance so the policy can recover. Three
consecutive invalid commits use the engine's abandonment rule: only independently
verified, uncensored abandonment with zero engine reward becomes a zero-reward
RL sample tagged `label_origin="failed_rl"`, never an expert label. Recognized
committing tools with malformed arguments execute an explicit invalid engine
action with parse diagnostics; they never silently wait. Unknown tools, malformed
noncommitting calls, provider failures, boundary censoring, unexplained truncation,
and failed verification remain unscored exceptions.

SFT demonstration construction rejects **every** invalid committed transition and
all abandonment, including episodes that could later recover. Scoring ordinary RL
episodes may passively advance standing missions to minute 120 using empty actions
tagged `passive_finalization`. Such transitions never become expert labels.
Provider failures must call `abort`. Verification and evaluator outcomes remain
outside the tool response and SFT context; actor feedback exposes only action
validity and bounded validation errors. Readiness audits separately record RL
label origin, invalid-action counts, and parse diagnostics.

`FrozenRetrievalBank.from_demonstrations` replays every source demonstration,
then freezes at most 2,000 public cases. A case contains a bounded public context
and its verified action. Matching uses a fixed vector of current minute, delivered
report counts, unlocated reports, available/unknown resources, observed/unknown
smoke, and active incident counts. Ties sort by stable case digest. Queries return
at most four cases and 16 KiB. No outcome, future schedule, family label, held-out
score, or evaluator truth participates in matching. Held-out queries cannot
update the bank. Training rebuilds it exclusively from replayed training inputs
and saves the frozen artifact; it does not trust an arbitrary bank JSON as input.

## Model readiness gate

SFT requires complete, verified train-only demonstrations and the explicit
budget/hardware guards. It has **no model-quality gate**: its purpose includes
improving tool compliance. A validation report is optional and is not used to gate
SFT or construct its labels.

GRPO requires a separately authorized **actual evaluation of the accepted SFT
adapter** and an externally supplied `checksum(report)`. CPU baseline metrics
cannot pass by meeting numerical thresholds. This bounded pilot gate is separate
from the complete 40-task validation benchmark. The report needs:

- `version="wildfire-benchmark-validation-v1"`, `status="complete"`, `verified=true`,
  `source="model_evaluation"`, `model_loaded=true`, positive integer
  `model_inference_calls`, and zero `scripted_actions` and `provider_failures`.
- Exact `model_id`, immutable `model_revision`, `contract_version`, `env_version`,
  `observation_version`, `reward_version`, `catalog_sha256`,
  `environment_implementation_sha256`, `model_contract_sha256`, and `reward_rules_sha256`, as returned by
  `expected_model_provenance(environment)`, plus matching prepared/topographic
  input hashes and `profile` / `profile_sha256`.
- `adapter_files_sha256` matching the accepted SFT adapter files exactly, and
  `readiness_plan_sha256=checksum(readiness_plan(environment))`.
- Exactly four `groups`: the **first validation group in frozen catalog order**
  for each of `uncertain_smoke`, `wind_shift`, `overlapping_fires`, and
  `failed_protection`. Each uses only its variant-0 `task_id` and assigned `seed`,
  with matching `group_id`, `family`, `variant=0`, `split="validation"`, `verified=true`,
  and `boundary_censored=false`.
- Four `rollouts` per group, with distinct integer `rollout_id` values 0–3. Each
  repeats the group's exact `task_id` and environment `seed`, and contains
  `source="model_evaluation"`, `verified=true`, `boundary_censored=false`, a finite
  `reward` in [0, 1], positive integer `model_inference_calls`, zero integer
  `scripted_actions` / `provider_failures`, and integer `committed_actions` /
  `valid_committed_actions`. The report's inference-call count must equal their sum.

The gate recomputes valid commits over those 16 model rollouts (at least 95%) and
the fraction of the four groups with reward variation greater than 1e-10 (at least
25%, or one group). Variation must come from model rollouts of the **same task and
environment seed**; two different physical seed variants cannot establish GRPO
signal. Fewer than four repeated rollouts, substituted tasks/seeds, censoring,
invented groups, duplicate rollouts, and wrong provenance fail closed. `inspect`
publishes the preregistered task/seed plan without performing inference. Sampling
randomness may vary between model rollouts; the incident's environment seed cannot.
The rest of the 40-task validation benchmark remains separate. Neither this gate
nor full validation contributes loss data or retrieval updates. The operator's
report checksum identifies trusted evidence; metadata does not substitute for
actually running the model evaluation.

## Later, explicitly authorized GPU stages

No training or inference run is authorized by adding this implementation. A later
operator must agree a time budget and spending limit, pre-provision a BF16 CUDA
GPU and the exact locally cached checkpoint, and install the existing pinned
requirements from `regional/training/requirements.txt`. The recipe checks torch
2.11.0, transformers 5.20.1, TRL 1.16.0, PEFT 0.21.2, and huggingface_hub 2.2.0.
It only loads **Qwen/Qwen3-1.7B**, revision
`70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, with `local_files_only=True`, offline
flags, disabled telemetry, and nonthinking chat templates.

The host wrapper is mandatory and verified through `/proc`: the immediate parent
must be `timeout --signal=KILL WALLs`. The agreed time budget and wall may not
exceed 7,200 seconds. A POSIX timer additionally terminates the process; the host
wrapper also bounds native/GPU operations that do not return to Python. These
commands illustrate a later 30-minute run with zero incremental local GPU cost;
the operator must supply the actual agreed spending limit when compute is paid.

```sh
timeout --signal=KILL 1800s python train/benchmark.py sft \
  --config /path/benchmark-config.json --enable-gpu \
  --agreed-budget-seconds 1800 --wall-seconds 1800 --agreed-budget-eur 0 \
  --demonstrations /path/verified-train-demo.json --output /path/fresh-sft-run
timeout --signal=KILL 1800s python train/benchmark.py validate \
  --config /path/benchmark-config.json --enable-gpu \
  --agreed-budget-seconds 1800 --wall-seconds 1800 --agreed-budget-eur 0 \
  --demonstrations /path/verified-train-demo.json \
  --sft-adapter /path/fresh-sft-run --output /path/fresh-readiness-run
timeout --signal=KILL 1800s python train/benchmark.py grpo \
  --config /path/benchmark-config.json --enable-gpu \
  --agreed-budget-seconds 1800 --wall-seconds 1800 --agreed-budget-eur 0 \
  --validation-report /path/fresh-readiness-run/validation.json --validation-sha256 REPORT_SHA256 \
  --demonstrations /path/verified-train-demo.json \
  --sft-adapter /path/fresh-sft-run --output /path/fresh-grpo-run
```

The explicit `validate` stage runs real local nonthinking model generation with
the same pinned Qwen checkpoint and accepted SFT adapter. It uses the same GPU
opt-in, spending/time limits, offline loading, and mandatory host timeout. It
performs the four preregistered tasks times four rollouts, changing only model
sampling seeds (`20261026 + rollout_index`). Structured calls use the same actor
context, compact parser, checks, and bounded evidence queries as training.
Evaluator data never enters generation messages. Invalid recognized commits retain
engine feedback; verified three-invalid abandonment is a scored zero RL failure.
Malformed envelopes, unknown tools, provider/model errors, censoring, or replay
failure stop the run without a final readiness artifact. Bounded generation may end early; scoring then uses only
tagged passive finalization, never a tactical fallback.

On complete real inference, validation saves `validation.json`, its canonical
`validation.sha256` (use this value for `REPORT_SHA256`), and per-rollout public
trajectories/origin tags with verification under private subdirectories.
`summary.json` separately reports `readiness_passed`; a completed evaluation with
insufficient reward variation remains blocked for GRPO. A timeout or exception
leaves `status.json` incomplete and no complete validation artifact. The pure
`assemble_readiness_report` helper accepts fake transports for CPU tests, but
marks those results `source="fake_model_transport"`, `model_loaded=false`, and
zero model inference calls; they cannot satisfy the gate or be published as
actual validation by the recipe. CPU dry runs never set actual model flags.

If retrieval is used, pass the same verified training demonstrations to validate
and GRPO. The report pins `retrieval_sha256`, which GRPO checks against its frozen
training bank. Validation never updates that bank or supplies training examples.

SFT trains completion tokens from complete replayed demonstrations; all compact
and query tool schemas are supplied. Oversized examples fail instead of silently
truncating labels. LoRA uses rank 8, alpha 16, zero dropout, and Q/K/V/O projections.
GRPO loads the checksummed SFT adapter and uses the current train-only environment
factory and full-horizon verified reward, never legacy scenarios. Both default
to two updates; optimizer steps, generation size, and tool turns are bounded.
Missing/nonfinite gradients, entirely zero gradients, nonfinite adapter weights,
or unchanged trainable weights prevent success. No Hub publication, automatic
downloads, cloud rental, or CPU training fallback exists.

For GRPO, the readiness report must come from an explicitly authorized evaluation
of that exact accepted SFT adapter: its `adapter_files_sha256` must match the
SFT summary and files. Base-model readiness alone cannot authorize the next stage.
The four-group readiness evaluation must be explicitly authorized after SFT via
`validate`; neither SFT nor GRPO runs it automatically.

`run.json` records strict pins and budgets before model loading. A successful run
also saves the frozen retrieval bank, checksummed adapter files, and sealed
`summary.json`. A timeout or exception without that summary is incomplete.
Budget values are recorded agreements; infrastructure spending must be enforced
by the operator's already provisioned host. The hardware-specific SFT/GRPO path
and real model validation paths have not been executed as part of this CPU-only
implementation. Their exact pinned hardware path remains untested.

## OpenEnv

`wildfire_openenv.benchmark` reuses the prepared observation/state transport and
adds bounded report/event reads to `BenchmarkAction`. Discovery exposes only IDs
and split names. The app has one active session, simulation mode, and a localhost
CLI. Use persistent `/ws` sessions; OpenEnv's stateless HTTP reset/step endpoints
create separate environments and do not preserve an episode between requests.
Event reads accept up to 20 `event_ids` or a nonnegative `after_cursor`, with a
1–20 record limit. IDs cannot be combined with a nonzero cursor. Unfiltered reads
return `data.next_cursor`, including the unchanged end cursor for empty batches,
so the same cursor can retrieve later-delivered events. ID lookups return a null
next cursor. Unknown/future IDs and cursors beyond received evidence are rejected;
read fields never become engine orders or advance the episode.
Tests compare a complete direct-core action sequence, rewards, and public trace
with both the local adapter and an in-process TestClient WebSocket session.
