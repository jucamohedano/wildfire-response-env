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
