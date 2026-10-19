# Single-GPU Qwen3 training pilot

This optional package adds a native TRL `environment_factory` interface around the
existing regional environment. It does not replace the frozen benchmark, modify
its outcome weights, contact HappyRobot, or publish models/data. Dependencies stay
in a separate training environment rather than the demo's installation.

## Model choice and compute

The official Hugging Face search and model cards were checked with `hf models`:

- **Qwen/Qwen3-1.7B**, revision
  `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`: initial actor candidate.
- **Qwen/Qwen3-0.6B**, revision
  `c1899de289a04d12100db370d81485cdf75e47ca`: smaller pipeline/debug alternative.

Use the post-trained BF16 models, not base-only checkpoints, GGUF exports, FP8
inference exports, embedding/reranking models or speech models returned by a broad
Qwen search. Both selected model cards declare Apache-2.0 and support non-thinking
mode/tool use. The model names are advertised size classes, not measured GPU-memory
requirements. Actual training memory must be measured for the selected configuration.
Sources: [1.7B card](https://huggingface.co/Qwen/Qwen3-1.7B),
[0.6B card](https://huggingface.co/Qwen/Qwen3-0.6B),
[TRL environment integration](https://huggingface.co/docs/trl/en/openenv).

The supplied CloudRift VM was verified with the CLI and SSH: one RTX 5090,
32,607 MiB reported VRAM, driver 580.159.03, seven allocated vCPUs and 24 GiB RAM.
A BF16 CUDA matrix multiplication passed on PyTorch 2.11.0+cu128 (CUDA 12.8,
compute capability 12.0). No other rental was used or created.

## Outcome reward and stop handling

`RegionalTrainingEnv` exposes only `check_action` and `submit_action` as tools.
They use the same five action fields as the director adapter, with typed arguments
and descriptive docstrings for TRL's automatic tool-schema extraction. Received
reports and sensor timestamps pass through the public-context whitelist. Future
events, scoring annotations, checkpoints and credentials never enter tool output.

Every group member receives its own reset instance with the same frozen scenario
and assigned seed. The pilot uses only `regional-train-001` and
`regional-train-003`; validation/test tasks and seed overrides are rejected.

**Stopping generation early is not rewarded as success.** The reward callback
keeps existing missions in force and passively advances the remaining episode
without new orders, to natural termination or the original 60-decision horizon.
Thus future incidents still occur and unresolved fire still incurs its frozen
terminal cost. This is explicit passive completion, not an automated tactical
fallback. The resulting trajectory is independently graded and exactly replayed;
passive-finalization decisions are separately labelled. Three invalid actions
retain the engine's existing abandonment/truncation behavior.

Reward is the unchanged full-episode negative outcome cost: no JSON-format bonus,
tool-call-volume reward, hidden-label reward or LLM judge. Tool errors before a
method executes cannot earn a format penalty/bonus, and a no-tool rollout receives
the passive full-horizon outcome. Identical rewards within a GRPO group can give
zero learning signal; inspect that diagnostic rather than claim improvement.
These rollouts are RL samples, not automatically accepted demonstrations or
preference labels. Private evaluator artifacts stay in separate directories.

## Isolated installation

Use a Python build with development headers. The VM's system Python lacked
`Python.h`; Triton's fused log-probability kernel failed before optimizer updates.
The remedy is an isolated uv-managed Python runtime, not disabling verification or
changing rewards. A C compiler and NVIDIA driver libraries are also needed.

```sh
export UV_PYTHON_INSTALL_DIR="$HOME/wildfire-rl-pilot/python"
uv python install 3.12
uv venv --managed-python --python 3.12 .venv-training
uv pip install --python .venv-training/bin/python torch==2.11.0 \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-training/bin/python \
  -r envs/wildfire_response/regional/training/requirements.txt
hf download Qwen/Qwen3-1.7B --revision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
```

Keep the CUDA-wheel installation step: the requirements file pins the PyTorch
release but does not select its CUDA index. A GPU smoke kernel and Triton driver
compilation should pass before model loading. The tested direct dependencies are
PyTorch 2.11.0+cu128, Transformers 5.20.1, TRL 1.16.0, PEFT 0.21.2 and Hub 2.2.0.
The local model-search CLI remains independent of that remote training environment.

## Bounded execution

```sh
python -m unittest envs.wildfire_response.tests.test_regional_training
OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
timeout --signal=INT --kill-after=30s 900s \
  .venv-training/bin/python -m envs.wildfire_response.regional.training.pilot \
  --output .runtime/qwen3-pilot --steps 2 --completion-tokens 1536 --tool-iterations 8
```

The pilot uses LoRA rank 8 on attention projections, BF16, SDPA, gradient
checkpointing, two sampled completions per group, eight tool iterations, non-thinking
mode and no vLLM server/reference-model copy (`beta=0`). Both requested optimizer
steps and total completion length are bounded. Telemetry integrations and Hub
uploads are disabled. It performs before/after **training-scenario** smoke scoring,
saves an adapter, checks whether trainable parameters changed, records memory/time
and retains every independently verified rollout. It does not treat a noisy
before/after difference as proof of generalization.

Use a fresh output directory. Incomplete attempts retain `status.json` and are not
reported as successful training. Check reward variation, tool parsing, finite
gradients, actual adapter change and resource use before increasing the workload.
The CLI permits up to eight updates for diagnosis, not an unattended long run.
Keep an external wall-clock timeout because a step-level timeout cannot bound an
in-flight generation/kernel. No new GPU rental or model publication is automatic.

This is an initial training integration, not the release-readiness certification.
A real director needs longer multi-decision rollouts, sufficient reward variation,
more training configurations, a fresh untouched evaluation set and validated
realism limitations. Licensing/asset permissions and public deployment remain
separate gates. The existing observed held-out baseline audit must not become an
unacknowledged model-selection set.
