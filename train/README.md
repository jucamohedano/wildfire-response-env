# Training recipes

For the current benchmark use [BENCHMARK.md](BENCHMARK.md) and `benchmark.py`.
It separates CPU demonstration/retrieval preparation from explicitly authorized
SFT, actual adapter validation and GRPO. The legacy recipe below does not operate
the new delivered-evidence population/area benchmark and is retained for history.

The canonical recipe is packaged once in
[`core/.../regional/training`](../envs/wildfire_response/core/src/envs/wildfire_response/regional/training/README.md).
`grpo.py` is the project-level entrypoint, analogous to the separate evaluation
tools in PortSimEnv. Heavy training libraries are not core dependencies.

Use an isolated Python 3.12 runtime with development headers and install the
CUDA wheel plus the pinned requirements. From this repository root:

```sh
uv python install 3.12
uv venv --managed-python --python 3.12 .venv-training
uv pip install --python .venv-training/bin/python torch==2.11.0 \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-training/bin/python \
  -r envs/wildfire_response/core/src/envs/wildfire_response/regional/training/requirements.txt
uv pip install --python .venv-training/bin/python -e envs/wildfire_response/core
OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  timeout --signal=INT --kill-after=30s 900s \
  .venv-training/bin/python train/grpo.py --output .runtime/grpo-pilot \
  --steps 2 --completion-tokens 1536 --tool-iterations 8
```

This is an explicit GPU job, not a default quickstart. Prior pilots did not produce
a useful trained director; do not rerun unchanged zero-gradient experiments at
scale. Next work is a verified training-only request → offer → authorize warm-start
set, followed by longer closed-loop rollouts and reward-diversity checks. No
HappyRobot account, paid model provider, Hub upload or automatic cloud rental is involved.
