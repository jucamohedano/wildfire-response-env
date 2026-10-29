# AgentEnv runtime and local dataset validation

Issue #30 remains **open**: a complete native AgentEnv framework task and native full-trace Parquet replay have not yet been demonstrated. A bounded workflow is prepared in `.github/workflows/agentenv-validation.yml` but has not been run. No model, API credentials, Hub upload or private dataset was used.

## Local checks, 2026-10-29

Python 3.12.9 with the pinned AgentEnv framework 0.9.1358, protocol 0.1.373, HF plugin 0.3.0, datasets 5.1.0 and pyarrow 26.0.0 was installed into a temporary venv using network-enabled package installation. The frozen prepared and topographic inputs matched the checked-in hashes and the 12-task development catalog. A deterministic passive development episode `bench-dev-71f777cb5ff8c9f2` completed 60 decisions and independently replayed with score 0.542406353976602; creation plus replay took 5.95 seconds. The existing in-process MCP parity test separately replayed all 60 actions successfully. These are synthetic simulator observations; they are not an AgentEnv framework task run.

The existing exporter produced local Parquet containing 12 development task rows, one supplemental episode and 60 supplemental transition rows, with no native AgentEnv episode rows. `eval/check_agentenv_dataset_roundtrip.py` loaded the task, supplemental episode and transition configurations using `datasets.load_dataset`, compared every observation, action, reward and terminal flag with the raw trace, and independently replayed it; the loader and replay took 3.998 seconds. It reports a partial failure because **datasets 5.1.0 cannot load the empty native episode Parquet**: `DatasetGenerationError`, caused by `ArrowInvalid: BatchSize must be greater than 0, got 0`. The checker exits nonzero in that case rather than marking the whole export passed. The three successfully loaded schemas are recorded in the local `/tmp/agentenv-local-check/roundtrip.json`, not uploaded.

The actual plugin command `agent-env hf publish /tmp/agentenv-local-check/fixture --name wildfire-ci --split development --out /tmp/agentenv-local-check/native-empty` also executed locally and reported one task and zero runs. This is proof of local plugin export only, not a native run or a successful native episode load.

`tests/test_agentenv_dataset_roundtrip.py` passed seven focused comparison, reward, hash and framework-link tests, including a local framework-store lookup using the plugin publisher’s selection and namespace routing. A separate real in-process MCP test, `tests/test_agentenv_fixture.py`, drove the actual `BenchmarkEnv` through its MCP adapter for 60 decisions and 120 simulated minutes and independently replayed it in 3.758 seconds. This exercises the driver and adapter; it does not exercise AgentEnv deployment or Docker.

## Prepared Docker CI fixture

The CI fixture is named `synthetic-agentenv-fixture-v1`; its prepared, domain and catalog hashes differ from the frozen real-input hashes. It uses the actual `BenchmarkEnv` and enforces 60 decisions, a 120-minute terminated horizon and independent replay. Its task ID can match the identifier generated for the frozen catalog; the input and catalog hashes, not the ID alone, identify this synthetic configuration.

The pinned framework's `run_code` step can target a deployed environment directly, without deploying or prompting an agent. The workflow generates a disposable fixture from the tracked synthetic test builders, builds and deploys its MCP environment via the real AgentEnv framework, drives it through `run_code`, invokes its replay verifier, executes the plugin's local-only `agent-env hf publish --out`, and loads the resulting nonempty native task/episode Parquet, adds the simulator extension from that same recorded run, and independently replays the loaded trace. It has a ten-minute job limit and bounded command timeouts. Only a compact synthetic result report is uploaded as a short-lived GitHub Actions artifact. No ignored prepared/domain pack, private catalog or real raw trajectory is copied to GitHub, and no new credentials or secrets are required.

The pinned framework's local sandbox can execute host subprocesses, but the generated MCP-server environment deploy step resolves a Docker image and the framework's dry-run explicitly rejects this host because Docker is absent. A host subprocess or the in-process MCP test is not represented as a successful framework deployment. The workflow is intended for a Docker-capable runner; its success, elapsed time and native schema are pending an actual CI run.

The pinned HF plugin's record allowlist excludes `run_code` script results and exports native trajectories only from prompt responses. The driver now returns its public trajectory and verifier result into the framework's `script_results`. After local native publication, the checker locates the same completed framework instance using the published episode ID, adds simulator episode and transition Parquet plus raw JSONL with that foreign key and a canonical trace hash, and independently replays the exact loaded trace. The native episode reward must match the simulator's independently replayed graded score; replay success is a separate boolean. The native tables and raw record are hash-checked unchanged and native chat trajectories remain empty. The entire native-to-extension workflow is **pending CI execution**; no actual framework run or successful extension roundtrip is claimed yet. The framework may impose payload limits; no such limit has yet been observed for this fixture. Source-code and geographic data redistribution rights remain separate gates.

## Supplemental trace check

For a local export containing a replayable development trace:

```sh
PYTHONPATH=envs/wildfire_response/core/src python eval/check_agentenv_dataset_roundtrip.py \
  --dataset-dir /path/to/local-export \
  --prepared-inputs /path/to/prepared/inputs.json \
  --topographic-inputs /path/to/topographic/inputs.json \
  --episode-id 'exact development episode_id' \
  --report /tmp/agentenv-supplemental-roundtrip.json
```

The checker uses offline mode, verifies the prepared/domain/catalog pins, restricts selected records to development and `training_eligible=false`, compares all transitions, and invokes the benchmark's independent replay and reward verification. It reports the actual loaded schemas and any native loader failure. It does not fetch or publish a Hub dataset.

The first native CI execution and local publication succeeded, but the checker queried outside the CLI’s namespace-routing context and found no candidates. The checker now enables the same namespace routing and `latest` selector used by the publisher; the fixed CI path has not yet been rerun.
