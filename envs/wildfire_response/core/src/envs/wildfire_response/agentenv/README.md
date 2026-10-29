# AgentEnv and Hugging Face export

This exporter serves the **archived night-response benchmark**. It requires
`--allow-archived-catalog`; its output is labeled archived and is not an active
incident-day training dataset. Historical format/runtime evidence remains valid.

Issue [#30](https://github.com/jucamohedano/wildfire-response-env/issues/30) tracks this port.

## Plan and data contract

1. Pin the 120-group benchmark catalog and the two prepared Málaga input hashes.
2. Generate four AgentEnv bundles (`development`, `train`, `validation`, `test`) from registered task IDs. Each bundle has JSON task DAGs, a Dockerized MCP server, an eval manifest and a split allowlist. Task prompts never include future events, scenario factors or private labels.
3. Build the task and empty native-episode Parquet tables with the pinned `agentenv-hf` schemas. AgentEnv-native episodes stay empty until AgentEnv has actually run the bundle.
4. Optionally import only development model traces. Replay-check them first, put public observation/action/reward transitions in supplemental Parquet and preserve the native public trajectory as compressed JSONL. Imported traces are never training-eligible.
5. Load the Parquet tables locally, parse every bundle, and compare one full MCP replay against its source trace. Public Hub upload remains blocked by unresolved code and asset redistribution terms.

The output follows `agentenv-hf`: `tasks/<bundle>.parquet`, `episodes/<bundle>.parquet`, `raw/<bundle>.jsonl`, and `bundles/<bundle>/`. The separate `trajectories/` tables are the project's simulator-transition contract, not AgentEnv-native chat transcripts. No private evaluator files or provider request logs are exported.

## Local export

Install the optional tools into an isolated environment; the simulator core remains dependency-free:

```sh
uv pip install --python .runtime/agentenv-venv/bin/python \
  -e 'envs/wildfire_response/core[agentenv-hf]'
```

Export the frozen catalog and, optionally, replay-verified development traces:

```sh
PYTHONPATH=envs/wildfire_response/core/src \
  .runtime/agentenv-venv/bin/python -m envs.wildfire_response.agentenv.export \
  --allow-archived-catalog \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --trajectory-root .runtime/luna-subagent-benchmark-20261026 \
  --output .runtime/wildfire-agentenv-hf
```

Use a fresh output path. Omit `--trajectory-root` to export tasks only. The exporter refuses stale inputs/catalogs, traces outside development, failed replay, and existing output directories. The output is local only; this command does not publish to Hugging Face.

## Validate locally

The MCP parity test runs a real MCP client against the adapter in-process and replays every action from one development trace:

```sh
PYTHONPATH=envs/wildfire_response/core/src \
  .runtime/agentenv-venv/bin/python eval/check_agentenv_integration.py \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --trajectory .runtime/luna-subagent-benchmark-20261026/bench-dev-71f777cb5ff8c9f2/model/trajectory.json.gz \
  --report .runtime/agentenv-mcp-integration.json
```

The exporter itself parses every bundle and writes the Parquet schemas used by `agentenv-hf`. With the optional `datasets` dependency installed, check a split using:

```python
from datasets import load_dataset
train = load_dataset(".runtime/wildfire-agentenv-hf", "wildfire-train_tasks", split="train")
transitions = load_dataset(".runtime/wildfire-agentenv-hf", "wildfire_trajectory_transitions", split="development")
```

## AgentEnv run

`agent-env hf run` requires a dataset on the Hub, and `agent-env run` deploys the bundle with Docker. Neither hosted execution nor Docker deployment is claimed by the local MCP parity test. Once rights are cleared and the dataset is intentionally published, install the AgentEnv HF plugin and run a selected split, for example:

```sh
agent-env hf run OWNER/DATASET --bundle wildfire-train --task TASK_ID --model MODEL_ID
```

The bundle uses the default `claude-code` AgentEnv agent; select another installed AgentEnv agent with the framework's supported override if needed. Each server instance accepts task IDs from its own split only.
