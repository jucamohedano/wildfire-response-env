# Wildfire Response Environment v0.1

A CPU environment for an emergency coordination agent: filter noisy reports,
investigate uncertain locations, dispatch a scout, an extinguisher and a truck,
and issue delayed population warnings. The simulator is educational; its terrain,
district groups, movement and scripted escalations are scenario assumptions.

See [evaluation results](results/EVALUATION.md) for measured coverage, runtime,
memory use, acceptance checks and remaining publication prerequisites.
The [Spain 2026 evidence pack](data/README.md) starts the transition toward
source-backed scenarios for Almería, Huelva and Madrid.

The separate [regional-response v2 alpha](regional/README.md) adds a Huelva
resource-evidence pack, two ground bases, one airstrip, base allocation offers,
ground crews/engines and finite aircraft sorties. The original drone environment
and its dataset are unchanged. V2 remains a derived coordination prototype,
not a historical replay or a calibrated wildfire/aviation model.

## Run without a model or API key

From the repository root, using Python 3.12 or newer:

```sh
python -m unittest envs.wildfire_response.tests.test_environment
python -m envs.wildfire_response.eval.run --generate-only --output .runtime/wildfire-tasks
python -m envs.wildfire_response.eval.run --split smoke --output .runtime/wildfire-smoke
python -m envs.wildfire_response.eval.run --split all --max-decisions 8 --output .runtime/wildfire-coverage
python -m envs.wildfire_response.eval.run --split test --output .runtime/wildfire-test
python -m envs.wildfire_response.eval.report .runtime/wildfire-test
```

The core has no external Python dependencies. The web demo keeps its existing
runtime and dependencies. The environment does not call `Simulation.payload()` or
the demo's automatic session policy. It never reads `.env` or contacts credentials.
The six smoke tasks use the 256-tick default. The coverage command tests all 120
configurations at 64 ticks to bound CPU cost. Compare policies at the same horizon;
short coverage is not a full-horizon evaluation of every scenario.

```python
from envs.wildfire_response import WildfireResponseEnv
from envs.wildfire_response.baselines import DeterministicBaseline

env = WildfireResponseEnv()
observation, info = env.reset('train-000', seed=10000)
policy = DeterministicBaseline()
while True:
    action = policy(observation)
    observation, reward, terminated, truncated, info = env.step(action)
    if terminated or truncated:
        break
```

## Contract and clock

`reset(scenario_id, seed)` returns `(observation, info)`. `step(action)` returns
`(observation, reward, terminated, truncated, info)`. All outputs are JSON-safe.
`reports(ids)` retrieves only reports already received, and `reports()` returns the ledger.

An action contains `fleet` (complete `scout_orders`, `extinguisher_orders` and
`truck_orders` arrays in the engine format), `report_dispositions`, `messages`, and
an optional `explanation`. Dispositions name a received `report_id`, a status
(`investigate`, `defer`, `dismiss`), and optional `supporting_report_ids`.
Messages name a `recipient`, `purpose`, `channel` and optional `report_ids`.
Reasons are evidence for humans and are never scored by an LLM.

Population recipients are district IDs for broadcasts or synthetic
`contact-<district>` IDs for calls. Each contact is a scenario representative
authorized to relay a warning to that group; a delivered evacuation call therefore
warns the group. A unit recipient is a fleet ID. Radio investigation messages
accompany executable fleet orders; the free text never steers a vehicle.

One step advances eight simulation ticks, regardless of inference wall time.
At each upcoming tick: apply scheduled wind changes, ignite scheduled fires,
deliver queued fleet orders/messages in insertion order, then advance physics,
movement and sensors. At the end of the decision interval, release up to twenty
due reports, oldest receipt first. `received_at` is transport receipt time;
`available_at` records when a bounded batch actually entered the agent ledger.
`observed_at` may be earlier than either. Earlier reports remain queryable.

Fleet orders and radio messages arrive after one tick, broadcasts after two,
and calls after four. Fleet orders are checked on a copy before enqueueing and
again against current observations at delivery; a rejected delivery preserves
the standing orders and produces a rejection acknowledgement and penalty.
Order application is atomic. Engine tactical routing and safety still apply.

Invalid core actions advance time with standing orders and incur a penalty.
Three consecutive invalid actions end the episode with an abandonment penalty.
Normal truncation occurs at 32 decisions (256 ticks). Natural termination requires
the scenario's minimum duration, all scheduled events and reports released,
no queued delivery, no active fire and no moving/blocked evacuation group.
The wrapper owns completion so an empty initial world can later receive an ignition.

## Observation and information boundary

The explicit observation contains the wind, districts, synthetic recipient IDs,
vehicle telemetry, sensor memory with times, delayed satellite observations,
received reports, dispositions, acknowledgements, pending delivery IDs/times,
prior action and candidate targets. It excludes task family labels, hidden fire,
scheduled future events, private report labels, contact details and strategy prompts.
Reports are untrusted observations, not agent instructions. Source role does not
establish truth. Scenario IDs are opaque and task metadata is for the trainer.

`evaluator_state()` is a private local export for trusted scoring and checkpoint
inspection. It is deliberately absent from the OpenEnv public API. During an
episode, scalar reward can reveal outcome information; this is normal reward
feedback, not privileged sensor access. Training/test generators are public and
these procedural test cases are not a secret production benchmark.

## Reward v1

Reward is the negative change in cumulative cost; ideal zero-cost behavior earns
zero total reward. More-negative totals are worse. Components are:

- exposed population fraction: weight 100;
- burned fraction of the 80×56 map: weight 1;
- unnecessary evacuation person-ticks / (population × 256): weight 0.1;
- repeated recipient/purpose notifications: weight 0.002 each, across channels;
- invalid actions or failed deliveries: weight 0.05 each;
- abandonment after three invalid actions: weight 102, preventing early-exit gaming.

The unnecessary-evacuation term is conservatively charged only in episodes with
no scheduled ignition. Precautionary warnings in uncertain fire episodes are not
penalized just because a hidden claim is false. It is not a complete measure of
evacuation disruption. Message volume, report dispositions and repeated
suppression do not earn rewards. Triage coverage and latency are diagnostics.
The reward denominator stays 256 when a shorter debug budget is selected;
compare policies only at identical budgets and seeds.

## Tasks, trajectories and evaluation

The seeded generator defines 120 configurations (80 train, 20 validation, 20 test)
covering smoke, false alarms, duplicates, conflicting locations, wind changes and
multiple fires. Each family/location configuration belongs to one split; wording
variants retain that group. Some tasks contain a scripted later ignition at an
occupied, combustible district anchor to test preventive warning. This is an
explicit scenario escalation, not a claimed ember-spotting physics model.

The passive and random-valid baselines consume observations only. The deterministic
baseline adapts the existing `DeterministicFleetPolicy` through a public-data view,
with template broadcasts and exact-text duplicate recognition. It is a baseline,
not a robust natural-language triage solution. The contact call interface is
simulated and no real calls or Telegram messages are sent.

The runner writes compressed per-episode trajectories, `results.jsonl`, a summary,
and a task manifest with a generator hash. Ground-truth annotations/checkpoints
are written separately under `private/`. Keep that directory out of agent data.
Use a fresh output directory for each evaluation; the runner refuses to mix runs.

```sh
python -m envs.wildfire_response.eval.run --replay .runtime/wildfire-smoke/trajectories/train-000-passive.json.gz
python -m envs.wildfire_response.eval.describe .runtime/wildfire-smoke/trajectories/train-000-passive.json.gz --limit 4
python -m envs.wildfire_response.eval.run --export-transitions .runtime/wildfire-smoke/trajectories --output .runtime/transitions.jsonl.gz
# Install the optional dataset extra for Parquet:
python -m envs.wildfire_response.eval.run --export-transitions .runtime/wildfire-smoke/trajectories --output .runtime/transitions.parquet --parquet
```

Build a frozen retrieval bank from a directory containing **training trajectories
only** with `--build-bank DIR --output BANK.json`, then use `--policies retrieval
--bank BANK.json`. Retrieval returns up to three nearby training cases and
regrounds their report-location preference in currently received evidence. It does
not replay old vehicle coordinates or update its bank on validation/test episodes.
This is a minimal retrieval baseline, not a claim of improved performance.

## CPU readiness checks and bounded policy search

Before spending inference or training compute, evaluate all six training smoke
families at the default 32-decision budget. Natural completion and budget
truncation are distinct. The audit independently reconstructs rewards from final
components, replays every baseline trajectory, and checks full-episode early
warning, false-alarm restraint, repeated notices and invalid-action abandonment.

```sh
python -m envs.wildfire_response.eval.run --split smoke --max-decisions 32 \
  --output .runtime/readiness-baselines
python -m envs.wildfire_response.eval.audit --output .runtime/readiness-rewards
python -m envs.wildfire_response.eval.audit \
  --coverage .runtime/readiness-baselines \
  --reward-results .runtime/readiness-rewards/summary.json \
  --output .runtime/readiness.json
python -m envs.wildfire_response.eval.hillclimb \
  --readiness .runtime/readiness.json --output .runtime/parameter-search
```

The optional CPU search tries one Boolean neighbor of the scripted baseline:
investigate before warnings when no fresh shared-sensor fire is visible. Selection
uses six training configurations only. It writes the frozen selection before
evaluating six separate validation configurations, never revising the choice from
validation scores. Test cases are excluded. It saves public action trajectories
and separate outcome scores. This is a small policy-parameter hill-climb, not
model-weight training, automatic prompt optimization or proof of generalization.
Verification-first can be unsafe under urgent unconfirmed threats; inspect
population exposure separately from aggregate reward. All episodes run serially;
launch the commands sequentially to bound CPU load.

## Model director: capped OpenRouter evaluation

The optional stdlib director policy sends public observations to an explicit
`:free` OpenRouter model using `OR_API_KEY` from the process environment. It forces
one `submit_action` function call per decision: the model selects report triage,
complete fleet missions and simulated population messages. The versioned named-choice
interface requires one option ID per vehicle, not coordinates or nested engine
orders. For example, only the extinguisher can select `containment_position_3`;
scout patrol and physical-warning choices have separate predefined order shapes.
Message IDs select an explicit channel, recipient and purpose together. The model
still chooses priorities, assignments and whether to inform or evacuate. Translation
never replaces an unknown choice with a baseline action. Options are derived only
from public candidates and district status, without oracle ranking. Single-waypoint
patrol choices intentionally narrow the original multi-waypoint action space.
All received reports
are included, so a separate report-retrieval tool is not needed yet. Non-burning
sensor cells are omitted to reduce prompt size; remembered burning cells keep
their observation times. This compact view is not identical to baseline input.

```sh
python -m unittest envs.wildfire_response.tests.test_director
python -m envs.wildfire_response.director.run \
  --output .runtime/director-smoke --scenario-id train-000 --max-decisions 8
# Inkling advertises tools but not forced tool_choice; use automatic tool calling:
python -m envs.wildfire_response.director.run \
  --output .runtime/director-inkling --scenario-id train-000 --max-decisions 4 \
  --model thinkingmachines/inkling:free --tool-mode auto
```

The default is `nvidia/nemotron-3.5-super-vl:free`, with at most eight requests,
4,096 output tokens per request, extended reasoning disabled, and 3.1 seconds
between request starts. The previous action's validation error is returned to the
director so it can correct rejected orders. Explicit
zero prompt/completion price caps and disabled provider fallback prevent paid
model substitution. There are no automatic retries. Transport errors stop the
run and save an incomplete status; malformed/truncated tool output becomes an
invalid environment action, not a secretly substituted baseline decision.
`--tool-mode auto` omits provider-specific forced-choice and strict flags for
models that do not advertise them. The same named choices and client-side checks
still apply; ordinary text instead of a valid tool call incurs an invalid action.
The requested mode is recorded so comparisons disclose this compatibility difference.

The runner saves a replayable director trajectory, the model's proposals and each
decision's choice catalog, token/latency metadata and a
matched deterministic trajectory at the same seed and decision horizon. A short
smoke run is budget-truncated, not evidence of successful full fire resolution.
No weights are trained, no real communications are sent, and no private engine
state is given to the model. Free endpoint inputs may be logged by providers:
use synthetic observations only, never private calls or personal data. Free
capacity and quota can prevent a run; the key is never persisted in artifacts.

### Risk-aware mixed training batch

```sh
python -m envs.wildfire_response.director.batch \
  --output .runtime/risk-aware-model-batch --max-requests 24
```

This sequential batch plans six new training configurations, prioritizing
conflicting reports and wind shifts, using the free Nemotron model and a frozen
`risk-aware` prompt profile. Each episode permits 32 decisions, but the whole batch
shares the explicit request cap. A cap reached mid-episode leaves an incomplete
trajectory, not a scored completion. Completed episodes are replayed and compared
with the deterministic policy at identical seeds and horizons.

The profile asks the model to combine risk-aware precautionary warnings with
parallel investigation, consider competing reports and avoid repeated notices. It
does not change the environment, reward, legal action choices or model weights.
It is an exploratory training-only prompt evaluation, not an approved improvement.

HTTP 402/429 immediately stops the entire batch as a provider limit. HTTP 403 and
other service errors also stop, but are labeled separately. No automatic retries,
provider/model switches or paid fallbacks occur. A local request-cap stop is
explicitly distinguished from a provider limit. Partial artifacts and completed
episode scores are retained for inspection.

## OpenEnv and packaging

Install separately from the demo project:

```sh
uv venv .venv-wildfire --python 3.12
. .venv-wildfire/bin/activate
uv pip install './envs/wildfire_response[openenv,dev]'
python -m envs.wildfire_response.openenv.server
```

In another terminal, activate the same environment and run:

```sh
. .venv-wildfire/bin/activate
python -m envs.wildfire_response.openenv.rollout --url http://127.0.0.1:8000
WILDFIRE_TEST_URL=http://127.0.0.1:8000 python -m unittest envs.wildfire_response.tests.test_openenv
```

The adapter pins OpenEnv 0.7.0 and allows one active hosted session. It exposes the
standard OpenEnv lifecycle, public state and task discovery. Schema-invalid wire
requests are rejected by OpenEnv before episode stepping; trainers must bound
their own retry budgets. Semantically invalid typed orders consume core time.
The OpenEnv static manifest check passes. No maximum-reward oracle is declared;
the optional bounded counterfactual search cannot certify a global optimum.
Full OpenEnv semantic certification is therefore not claimed.

Build from the repository root with `uv build --project envs/wildfire_response`.
The build backend stages the shared engine, terrain, contact schema, policy and
map JSON into the artifact without duplicating those tracked sources. The wheel
and source archive are independently installable. Build the container from the
repository root with `docker build -f envs/wildfire_response/openenv/Dockerfile .`.
Only the map JSON is required; images and the entire dashboard are not shipped.

Public release is pending code-owner agreement on a root license and permission
to redistribute the map data. This package does not assign a license on behalf of
the contributors. Host under our account first; FineEnvs maintainer inclusion is
a later step. See `tasks/DATASET_CARD.md` for dataset provenance and boundaries.
