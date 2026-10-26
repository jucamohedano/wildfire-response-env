# Wildfire OpenEnv adapter

The separate `wildfire-openenv` distribution depends on the local CPU core and the
tested OpenEnv 0.7.0 SDK. From repository root:

```sh
uv pip install --python .venv/bin/python -e envs/wildfire_response/core \
  -e 'envs/wildfire_response/openenv[dev]'
.venv/bin/python -m wildfire_openenv.server --port 8001
# In another terminal:
.venv/bin/python envs/wildfire_response/openenv/rollout.py --url http://127.0.0.1:8001
```

Use `wildfire_openenv.client.WildfireClient` and
`wildfire_openenv.models.WildfireAction`. These forward the already tested
regional wire contracts; check actions do not advance time, final actions do.
One active hosted episode is supported. The inherited v1 drone adapter is a
compatibility module, not the canonical server for this project.

Build the supplied container from repository root with
`docker build -f envs/wildfire_response/openenv/Dockerfile .`.
No container build or hosted Space deployment is claimed until explicitly tested.
The root layout follows PortSimEnv, but there is no copied port viewer, MCP
playground or AgentEnv/Harbor integration. No live communication account is needed.

## Isolated execution checks

Keep the optional SDK out of the dependency-free core interpreter. Use an
installed Python 3.12 or newer; the explicit interpreter path prevents `uv` from
choosing a different managed interpreter:

```sh
uv --cache-dir .runtime/openenv-uv-cache venv \
  --python "$(command -v python3)" --no-python-downloads .runtime/openenv-test-venv
uv --cache-dir .runtime/openenv-uv-cache pip install \
  --python .runtime/openenv-test-venv/bin/python \
  -e envs/wildfire_response/core -e 'envs/wildfire_response/openenv[dev]'
.runtime/openenv-test-venv/bin/python eval/check_openenv_integration.py \
  --report .runtime/openenv-integration-legacy.json
.runtime/openenv-test-venv/bin/python -m unittest discover \
  -s tests -p test_openenv_execution.py -v
```

The runner starts two real localhost subprocess servers, supplies
`WILDFIRE_TEST_URL` and `REGIONAL_TEST_URL`, and executes the original nine tests,
including all three external-client checks. A skipped test fails the run.
Servers use inherited loopback sockets on ephemeral ports and are stopped on
success, failure, or timeout. No model, API key, container, or remote service is
used. Public package downloads are needed only for installation. Dependency
versions and counts are included in the JSON report. Exit codes are 0 for pass,
1 for failure, and 2 for unsupported runtime (including denied socket access or
missing optional distributions).

## Prepared-data development server

`wildfire_openenv.prepared` is a separate optional adapter for
`wildfire_core.prepared.PreparedDataEnv`. It requires a local input bundle and a
caller-provided payload digest. Its server configuration explicitly chooses
`allow_synthetic_conditions=True`; direct `PreparedEnvironment` construction
requires that keyword. These are six **development** exercises on one landscape,
with synthetic operations and physics assumptions, not training/test splits or
historical replay. The adapter keeps the inherited regional server unchanged.

From repository root, after the local compiler has created the pinned bundle:

```sh
export WILDFIRE_PREPARED_INPUTS="$PWD/.runtime/malaga-prepared-inputs-v1/inputs.json"
export WILDFIRE_PREPARED_SHA256=ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108
.runtime/openenv-test-venv/bin/python -m wildfire_openenv.prepared --port 8002
```

Use `PreparedClient` and `PreparedAction` from `wildfire_openenv.prepared`.
`reset(task_id='prepared-dev-000', seed=771100)` starts a case. `step` with
`operation='advance'` commits one action; `check` delegates to the public core
`check_orders` validator and consumes its bounded check budget without advancing
time. `situation` reads the current public observation and `trajectory` returns
the public trace in `result.observation.trajectory`. State contains only episode
ID, decision count, minute, and termination flags. One active hosted episode is
supported. Typed, bounded ops/readback/triage/clarification/protection fields reject
unknown fields and free-text explanations; core validation determines semantic
validity, including atomic invalid-commit behavior.

This executable client/server comparison starts fresh subprocesses itself:

```sh
.runtime/openenv-test-venv/bin/python eval/check_openenv_integration.py \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --report .runtime/openenv-integration-actual.json
```

It runs the legacy nine checks, then completes all six prepared cases with the
scripted protection baseline and identical local/server seeds and actions. It
compares every observation, metadata field, reward, termination flag, safe state,
and complete public trajectory, including the implementation hash. Local and
server traces are separately exported under
`.runtime/openenv-integration-trajectories/`, read back, compared against the
original objects, and required to have identical bytes/SHA256. The exported
server trace is replay-verified. Use `--trajectory-dir` for a different output
directory. The report explicitly marks prepared checks `not_requested` when no
bundle arguments are supplied; a legacy-only pass does not claim prepared-data
verification. The seven adapter unit tests use a synthetic fixture independently
of the compiler and measured bundle.
