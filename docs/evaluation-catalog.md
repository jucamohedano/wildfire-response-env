# Evaluation-only catalog

`incident_day_evaluation_v1` contains 24 synthetic operational configurations,
with four configurations in each of the six incident-day families. Its registry
status is `active_evaluation` and its split is `evaluation`. The catalog has no
training set. Evaluation results must not be used to tune policies, choose
configurations or update a training or retrieval corpus for the same frozen run.

The cases share the pinned source landscape, road network, simulator mechanisms,
and development parent scenarios. They also inherit their parents' fire seeds
and geography. They measure transfer across operational configurations on that
shared context; they do not establish held-out independence, performance on
unseen terrain, or independent historical incidents. Operational inventories,
demand and timing changes remain synthetic assumptions. Runtime consistency does
not establish physical or reward calibration.

## Corrected interface for new policy evaluations

Use the explicit [Ops v3 contract](ops-v3.md) for new model runs so delivered
population arrivals are reconciled and approach errors are actionable. Install
`envs/wildfire_ops_v3` alongside the core, select `observation_version='v3'` in the
adapter/factory or `--observation-version v3` in the evaluation CLI, and keep its
observation identity with the run. The prepared tasks and reward are unchanged.
The default v2 path remains available for reproducing frozen results; do not
silently relabel those observations or resume them as v3.

## Evaluator APIs and model boundary

Discovery (`list_tasks`, `/tasks`) and reset are trusted evaluator APIs. The
evaluator selects explicit catalog task IDs and keeps their mapping private;
their suffixes can reveal the template and must not be supplied to the model.
Evaluation observations and public artifact headers use the generic
`task_id: incident-day-evaluation-v1`. Masking that field does not make the
catalog or its shared parents independent held-out evidence.

The model receives only the allowed policy tools, their public results and
observations. It has no discovery/reset API, raw evaluator session-state API,
generator access, or filesystem access to code, manifests, templates or private
artifacts. The evaluator must enforce this tool and process boundary; the HTTP
client alone is not a model sandbox.

With the optional adapter installed, the evaluator uses the configured
`wildfire_openenv.incident_day.IncidentDayEnvironment` instance:

```python
assert environment.list_splits() == ['development', 'evaluation']
tasks = environment.list_tasks('evaluation')
# After freezing code and manifests, start an explicit evaluation task:
# observation = environment.reset(task_id=tasks[0]['id'])
```

Evaluation discovery requires both input and network pins to match the active
evaluation registry entry exactly. A fixture, unknown pins, an inactive catalog,
or an unrelated catalog yields no evaluation tasks. Explicit selection with
mismatched pins is rejected, including in archival mode. Discovery returns task
IDs and split names to the evaluator only; scenario IDs, seeds, future schedules,
digests and private evaluator material do not belong in model context.

Development defaults and discovery remain unchanged: reset without a task ID
selects the existing development default, and `list_tasks('development')` excludes
evaluation cases even when archived replay is enabled. The named REST client's
`list_tasks()` and `GET /tasks` retain development discovery. Use
`list_tasks(split='evaluation')` or `GET /tasks?split=evaluation` for evaluation.
The evaluation response has `default_scenario_id: null`; reset requires an explicit
ID to select an evaluation case. Unsupported splits, duplicate or unknown query
parameters, and unavailable evaluation input/network pins or catalogs return HTTP
400. Other endpoints still reject query strings. The client rejects unsupported
split names locally with `ValueError`; server errors surface as `HTTPError`.

```python
from wildfire_openenv.incident_day_client import IncidentDayClient
client = IncidentDayClient('http://127.0.0.1:8005')
tasks = client.list_tasks(split='evaluation')['tasks']
# After freezing code and manifests:
# observation = client.reset(scenario_id=tasks[0]['id'])
```

The opt-in real loopback test compares one full evaluation day against the local
API, including observations, reward, termination and public-field guards. After
code and manifests are frozen, run:

```bash
WILDFIRE_RUN_EVALUATION_HTTP=1 WILDFIRE_LOCAL_RUNTIME=/path/to/frozen-package \
  python -m pytest -q tests/test_evaluation_external.py
```

Without the explicit opt-in, this simulation test is skipped. The separate
`tests/test_incident_day_evaluation_http.py` contract tests use mocked resets and
do not compile scenarios or simulate days.

## Freeze before execution

Before running simulations or scoring, freeze the code revision, exact package
manifest, catalog membership, scenario seeds and digests, input/network pins,
policy definitions and reward/validator identities. Keep these frozen manifests
with the private run evidence and its report digest. Changing a configuration,
parent, event, policy or dependency requires a new recorded identity and a new
run; preserve the prior manifests and results. The transport manifest advertises
membership and interface scope, and does not itself certify a frozen run.

Keep evaluator audits, checkpoints, curation metadata, seeds and provider logs in
private storage. Actor observations use the adapter's public allowlists. Report
missing, incomplete or unsupported checks explicitly; catalog availability alone
does not establish execution or acceptance of all 24 configurations.

## Build and validate locally

From the repository root with the core, PyArrow and pytest installed, reuse the
pinned downloaded package described in [the runtime guide](hub-runtime.md):

```bash
python -m eval.build_completion_bundle \
  --source .runtime/wildfire-response-spain --output /var/tmp/my-evaluation-pack \
  --include-evaluation
python -m eval.evaluate_packaged_runtime \
  --data-dir /var/tmp/my-evaluation-pack --output /var/tmp/my-evaluation-results \
  --wall-seconds 1800
# Continue an incomplete budget-limited run with the same code and package:
python -m eval.evaluate_packaged_runtime \
  --data-dir /var/tmp/my-evaluation-pack --output /var/tmp/my-evaluation-results \
  --wall-seconds 1800 --resume
```

Generation seed: `20261102`. Each family contains one limited-water, one
high-population, one slow-mobilization and one late-crew-reserve configuration,
assigned in deterministic order. Family event windows and source geography come
from the corresponding development parents. Fire seeds are inherited explicitly;
these are operational-demand variants, not new weather physics or new incidents.

`tasks/evaluation.parquet` contains only the evaluation tasks. Validation crosses
24 tasks with passive and dispatcher scripted policies on one CPU worker. These
policies check runtime behavior; their trajectories are not expert labels or
training examples. `report.json` retains all complete, blocked and failed cells;
only complete, eligible, verified runs receive a score. Interruptions are
resumable with private environment, policy and observer state. Changing code or
data invalidates resume. No inference key or GPU is required.

Render an aggregate HTML report to a new private file:

```bash
python -m eval.render_evaluation_report \
  --report /var/tmp/my-evaluation-results/report.json \
  --output /var/tmp/my-evaluation-results/review.html
```

The `passive-dispatcher-operations-v1` qualification contract requires aggregate
evidence for situation/report reads, approach/destination lookups, committed
orders, status requests, bounded waits, resource requests and authorizations,
evacuation and status responses. Per-cell movement, capacity, stock, timing,
privacy and replay checks remain mandatory. Draft checks, explicit releases and
shelter orders are optional for these two baselines; the original four-policy
development matrix's all-operation coverage gate remains unchanged. This
qualification contract is not a requirement that an evaluated model use every
tool. Before reset/restore proceeds, the runner checks each compiled scenario's
seed, digest and curation identity against the frozen Parquet task row.

## Reporting policy

The selected protocol is [evaluation-protocol-v1](../data/evaluation_v1/protocol.json):
retain the unchanged verified reward and report evidenced adverse outcomes
separately. Late recovery and stationary assistance unresolved at the horizon
are operational flags, not assertions that recovery later succeeded. A high
population-first score is not a resource-safety certificate. Broken invariants,
replay mismatches, missing evidence and unfinished/censored episodes stay
unscored. The original strict report and findings are always retained.

For the historical observer correction, the completed recorded-journal replay
is pinned to commit `95aeb3d`. Reproduce that original replay from that checkout;
its observer-only code allowlist intentionally rejects later reporting additions.
The current reporting tool derives a score view from those hashed artifacts
without resetting the simulator or invoking a policy:

```bash
python -m eval.derive_evaluation_scores \
  --revalidation /var/tmp/my-evaluation-revalidation \
  --expected-report-sha256 REVIEWED_FINAL_REPORT_SHA256 \
  --output /var/tmp/my-evaluation-scores.json
python -m eval.render_evaluation_report \
  --score-view /var/tmp/my-evaluation-scores.json \
  --output /var/tmp/my-evaluation-scores.html
```

The JSON view preserves `status`, `score` and original findings, and adds
`reported_score`, `adverse` and `operational_status`. Its lineage identifies the
user-selected reporting policy, corrected evidence and source report hashes.
Future evaluations must record the reporting policy before comparing models.
