# Wildfire Response Environment

Standalone, programmatic wildfire coordination for evaluation and open-weight RL
experiments. A coordinating agent investigates noisy reports, requests resources
from multiple bases, authorizes offers and sends simulated population instructions.
There is **no HappyRobot dependency, live dispatch, live messaging or API-key
requirement** for the CPU environment. This repository is the development source
of truth; the Hackspain checkout is historical context, not a required service.

## Current benchmark candidate

The current opt-in environment is `wildfire_core.benchmark_v1.BenchmarkEnv`.
It uses delivered evidence, safety-first outcome ordering and a separate80/20
population/area learning reward. Its catalog has160training,40validation and40test
episodes plus12development probes. Older regional, terrain and reactive modes
remain available as versioned comparisons; their results are not interchangeable.

Start with the [environment card](ENVIRONMENT_CARD.md), [dataset card](DATASET_CARD.md),
[fidelity limits](FIDELITY.md), and [benchmark commands](data/benchmark_v1/README.md).
The [staged training recipe](train/BENCHMARK.md) supports CPU preparation and
explicitly gated Qwen SFT, adapter validation and GRPO. Code readiness is not
evidence that a trained model improves. Publication still requires the
[license and asset checks](LICENSE_STATUS.md).

The [frozen CPU validation report](results/benchmark-validation-20261026.html)
records 428 verified episodes across development, sensitivity, validation and test.
It reports reference policies only; no trained model has been evaluated.

An offline Python client can use the already prepared, pinned inputs:

```python
from wildfire_core.benchmark_v1 import BenchmarkEnv
from envs.wildfire_response.regional.benchmark_v1.catalog import task_ids

environment = BenchmarkEnv(
    '.runtime/malaga-prepared-inputs-v1/inputs.json',
    '.runtime/topographic-inputs-v1/inputs.json',
    expected_prepared_sha256='ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108',
    expected_topographic_sha256='e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd',
    allow_synthetic_conditions=True,
)
observation, info = environment.reset(task_ids(environment.catalog, 'development')[0])
while True:
    observation, reward, terminated, truncated, info = environment.step({})
    if terminated or truncated:
        break
verification = environment.verify_episode()
assert verification['passed']
```

The example deliberately waits; it demonstrates the API, not a good response
policy. Inputs must exist locally with those hashes; no download or substitution
is performed. For OpenEnv, install the optional package and run
`python -m wildfire_openenv.benchmark --mode simulation --slope-enabled true`
with the four `WILDFIRE_PREPARED_*` / `WILDFIRE_TOPOGRAPHIC_*` path/hash variables.
Use a persistent WebSocket session. The UI's **Benchmark · delivered evidence**
group exposes development tasks only.

## Layout, following PortSimEnv

The reference is Adithya S Kolavi's
[PortSimEnv v1](https://github.com/adithya-s-k/FineEnvs/tree/main/07-simulation-environments/portsim-v1),
inspected at FineEnvs revision `410fb3454a903646f814bb752513c56fed3eae78`.
Its [article](https://huggingface.co/spaces/FineEnvs/simulation-rl-environments)
and [Space](https://huggingface.co/spaces/FineEnvs/PortSimEnv) are separate from
its GitHub source. Our repository follows that source/data/core/adapter/evaluation
separation, with an additional directory for the training recipe:

```text
README.md, DESIGN.md, project.yaml, PROVENANCE.md
data/huelva/                    evidence and source policy
envs/wildfire_response/
  core/                         installable wildfire_core, frozen simulator and grader
  openenv/                      installable wildfire_openenv, server/client/container
  tasks/regional-v0.2.0/         public task metadata and locked manifest
  tools/                        deterministic dataset export
eval/                           sequential baseline evaluation and replay
train/                          bounded Qwen3 LoRA/GRPO entrypoint
results/                        measured results index and migration acceptance
assets/                         asset provenance; no copied port viewer/banner
tests/                          standalone extraction checks
```

No PortSim source, Barcelona records or assets were copied. We do not inherit its
CP-SAT optimality guarantee or claim a trained wildfire director. Our grader checks
outcome accounting/lifecycle invariants and replay, not physical realism or an
optimal policy. A public Space or AgentEnv/Harbor adapter is not claimed. The
optional local operator UI below is not publicly deployed.

## Bounded DeepSeek benchmark evaluation

`eval.run_benchmark_cloudrift` uses the **current benchmark** observation/tool
contract, unlike the older `run_cloudrift_ops --reactive` runner. It attempts three
development probes (uncertain smoke, false alarm, overlapping fires), each with
the unchanged 60-decision/120-minute horizon and matched passive/dispatcher CPU
references. This is not a held-out leaderboard or training-data collection.

After explicitly approving a new spending cap and supplying a valid CloudRift
inference key, run from the repository root:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_benchmark_cloudrift \
  --output .runtime/deepseek-benchmark-NEW-RUN \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --allow-cloudrift --key-stdin --usd-cap 5 \
  --max-requests 160 --max-tokens 2048 --seconds 1800 --request-timeout 180
```

The hidden prompt keeps the key out of command arguments and artifacts. Alternatively
use `CLOUDRIFT_INFERENCE_KEY` without `--key-stdin`. Non-streaming calls use only
`deepseek-ai/DeepSeek-V4-Flash-0731`. The runner reserves a conservative maximum
estimated charge before each request using the current API-advertised rates;
unknown-usage failures retain their reserve. This is not a provider billing limit.
No retries, fallback models, automatic budget increases, or scripted completion
of partial model episodes occur. Time limits prevent new calls; an in-flight
request may use its remaining request timeout.

Each run writes `plan.json`, `summary.json`, `report.html`, compressed trajectories,
and request records including bounded/redacted rejected-response diagnostics.
Independent evaluator audits stay in separate `private/` folders. A provider
failure or exhausted budget stops the whole sample, retaining an **unscored**
partial trajectory. Three consecutive invalid commits remain a scored abandonment.
The cap may be insufficient for even one full episode; never present partial
progress or completed CPU references as a completed model evaluation.

The [2026-10-26 DeepSeek trial](results/deepseek-benchmark-20261026.html) attempted
all three probes: each ended in protocol abandonment, with no full-horizon model
completion. Sixteen responses cost an estimated $1.610742 at the API-advertised
uncached rates. The report includes exact captured tool diagnostics; it does not
establish firefighting-policy competence. Follow-up integration work is tracked
in [issue25](https://github.com/jucamohedano/wildfire-response-env/issues/25).

## Local 2D control-centre UI

The optional [UI](ui/README.md) adds a flat source-derived terrain map, optional
IGN aerial imagery/OpenStreetMap context, report/triage and clarification controls, base offers/readbacks and
resource authorization, population protection, scripted playback and verified
read-only trace replay. It uses the existing Python API; it does not duplicate
the simulator or change physics/rewards. Its dependencies remain isolated from
the core and training packages. Run it locally; no public deployment is implied.

## CPU quickstart

The original quickstart below exercises the preserved regional version. Use the
current benchmark example above for delivered-evidence population/area evaluation.

The new [reactive incident mode](envs/wildfire_response/core/src/envs/wildfire_response/regional/incident/README.md)
adds delayed resident calls, crew support requests, persistent outstanding updates
and resource arrival estimates. Select `incident-dev-*` in the UI; the optional
`--reactive` provider-runner flag uses focused v2 tools. The original scenarios
and tool contract remain available for comparisons and replay.
The [initial CPU comparison](results/reactive-incidents-20261026.json) records ten
verified episodes, including delayed protection and a reconnaissance-only support
request probe. It evaluates simulator mechanics, not a newly trained model.

From this repository root, Python 3.12+:

```sh
uv venv -p 3.12
uv pip install --python .venv/bin/python -e envs/wildfire_response/core
.venv/bin/python eval/replay.py --output .runtime/first-episode
.venv/bin/python eval/replay.py --replay .runtime/first-episode/trajectory.json.gz
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m unittest discover \
  -s envs/wildfire_response/core/src/envs/wildfire_response/tests -q
```

```python
from wildfire_core import WildfireResponseEnv
from wildfire_core.baselines import CooperativeBaseline

environment = WildfireResponseEnv()
observation, info = environment.reset('regional-train-001')
policy = CooperativeBaseline()
while True:
    observation, reward, terminated, truncated, info = environment.step(policy(observation))
    if terminated or truncated:
        break
```

The friendly `wildfire_core` API targets the plane/base regional environment.
Inside the core distribution, `envs.wildfire_response` and `simulator` retain their
historical paths so frozen scenario hashes, rewards, tests and old trajectories
remain compatible. Those are internal compatibility modules, not another checkout
or a second simulator implementation. Legacy v1 fixtures remain for regression.

## Wildfire Ops Control Center

The intended learned role is the **ops desk/controller**: it reviews incoming
information, coordinates resources, communicates with bases and population groups,
and monitors reported execution. It does not act as the incident commander. Bases
and field units are deterministic simulated counterparts in the initial design.
The current v0.2 implementation and APIs retain the historical `director` naming
for compatibility; see the [ops interface notes](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/README.md).

The ops facade uses `wildfire-ops-objective-fraction-v3`. Control tasks score
evidence-backed triage, population safety, incident resolution, and communication
discipline at episode end, with a separate pass/fail result for each. Full credit
requires all four, including no unresolved fire. See the [objective definitions](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/OBJECTIVES.md)
for the formula, evidence rules and calibration limits. The original regional
v0.2 environment retains its separately weighted `regional-outcome-cost-v2` score.

```python
from wildfire_core import OpsControlCenterEnv
from wildfire_core.ops import ControlBaseline

env = OpsControlCenterEnv()
observation, info = env.reset("control-000")
policy = ControlBaseline()
while True:
    observation, reward, terminated, truncated, info = env.submit_action(policy(observation))
    if terminated or truncated:
        break
print(env.verify_episode()["objective_result"])
```

The new facade exposes `get_situation`, `get_reports`, `check_orders`,
`request_resources`, `authorize_resources`, `release_resources`,
`confirm_readbacks`, `correct_readbacks`, `triage_reports`, `send_messages` and
`advance`. Each committed tool call advances one configured
simulator decision; reads and checks do not. See the [ops interface notes](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/README.md)
and [contract](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/CONTRACT.md).
Use `env.trajectory()` to export the public trace and `env.verify_episode()` after
episode end to reproduce the objective fraction and replay the simulator.
The checked-in `wildfire-ops-task-pack-v0.4.0` contains six broader control tasks
and six arrival-only dispatch exercises, all development-only. The control cases
reuse existing regional training configurations; they are not new held-out data.
The new [locked generator](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/DATASET.md)
adds 120 configuration groups with three variants each, split 80/20/20 by group.
It supports delayed reports/requests, initial resource commitments, message
failures and acknowledged retries. Test policy outcomes are left unconsumed.
The [recorded CPU run](envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/DATASET_RESULTS.md)
verifies 600 train/validation policy episodes and preserves their public trajectories.

Ops interface v4 requires a matching base readback before authorization. Two
fixtures in each task set include a synthetic mission readback error. The controller corrects it,
waits for the corrected readback, then confirms and authorizes. The dispatch
baseline uses only public observations and performs this sequence automatically.
Run the control CPU smoke suite and replay an exported episode with:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --output .runtime/ops-development-smoke
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --replay .runtime/ops-development-smoke/control-001/control/trajectory.json.gz
```

The AirOpsEnv article describes related but distinct task versions: v1 models one
ops desk at one hub; v2 adds ops and crew desks; v3 builds on v2 with passenger
modeling and a customer desk. These versions are useful structural references, but
this project does not claim equivalence to AirOpsEnv.

## Tasks and evaluation

The frozen regional pack has 36 synthetic configurations: 24 train, six validation
and six test. It shares one approximate map and is not a historical-fire replay.
The lock remains `regional-benchmark-v0.2.0`, SHA256
`e882e0f8ba58246ef3e4c3d271d32a6d54a1756d980be52bcccb524ff84ad840`.
Task metadata exports are public; full configurations/checkpoints remain evaluator-only.

```sh
.venv/bin/python envs/wildfire_response/tools/build_dataset.py --output .runtime/tasks
.venv/bin/python eval/run_eval.py --output .runtime/train-audit --split train
```

Use fresh output directories. Every baseline rollout is independently verified and
exactly replayed. The existing held-out baseline outcomes have already been seen;
they are not a fresh secret model-selection set. See [results](results/README.md).

## Communication evidence

The new opt-in [protection mode](envs/wildfire_response/core/src/envs/wildfire_response/regional/protection/README.md)
adds fallible synthetic shelter, sequenced/corrected instructions, separate
delivery/readback/execution, resource-bound in-person warnings and safety-gated
reward. Its offline context gate validates declared metadata without pretending
that real weather/geographic joins are complete. Spanish v1 remains replayable.
Protection/prepared/topographic/stress episodes now use reward
`wildfire-protection-safety-gate-v2`: population safety carries80%, and triage,
incident resolution and communication discipline share the remaining20% equally.
Safety failure or topographic boundary censoring still yields zero; all four
goals are needed for1.0. Old equal-quarter protection results retain their v1
labels and require their pinned engine revision for replay. The legacy regional
cost and generic ops fraction contracts remain unchanged.
See the [protection validation report](results/protection-development-v1.html),
including the retained unsafe-shelter counterexample and remaining data gates.

The opt-in [Spanish development bridge](envs/wildfire_response/core/src/envs/wildfire_response/regional/grounded/README.md)
now builds four private EGIF anchors into 12 synthetic development cases and
connects them to `SpanishDevelopmentEnv`. It adds delayed report-location
clarification, source-informed timing assumptions, seeded episode replay and a
CPU baseline runner. Real weather, geography and shelter mechanics are not implied.
The [development validation report](results/spanish-development-v1.html) records
the 36 replayed baseline episodes and the limitations of these exercises.

The [operational-source audit](data/operational/README.md) now inspects actual EGIF
2021 records and ICS-209-PLUS report sequences. See the [HTML findings](results/operational-source-audit-v1.html)
for field coverage, missingness, CPU measurements and the recommended next scope.
These evaluator-only samples are not enabled as training tasks.

The [communication evidence pack](envs/wildfire_response/core/src/envs/wildfire_response/data/COMMUNICATIONS.md)
adds four official sources and eight attributed summaries from three incidents,
with explicit missing-data and simulator-capability gates. It includes no authentic
call transcripts. Source-inspired exercises keep evaluator provenance separate;
the locked scenarios and benchmark results are unchanged.

## Optional OpenEnv and GPU training

The [OpenEnv package](envs/wildfire_response/openenv/README.md) installs separately:

```sh
uv pip install --python .venv/bin/python -e envs/wildfire_response/core \
  -e 'envs/wildfire_response/openenv[dev]'
.venv/bin/python -m wildfire_openenv.server --port 8001
```

The [training recipe](train/README.md) requires an explicitly provisioned GPU and
isolated heavy dependencies. Existing Qwen3-1.7B pilots established the LoRA/GRPO
execution path, **not useful learned control**: the sampled actor requested
resources but did not authorize them, and later groups produced zero gradients.
Next work is a verified training-only multi-turn warm start, not an unattended
larger RL run. No training, provider calls or cloud rental starts automatically.

## Repository versus Hugging Face

GitHub hosts code, tests and recipes. Hugging Face may later host a Space, dataset
and model checkpoints. Nothing is published there by creating this GitHub repo.
Keep this repository private until the inherited code licence and asset/data
redistribution permissions are resolved. No model weights, credentials, private
evaluator trajectories or raw cloud snapshots are committed. See [provenance](PROVENANCE.md).
