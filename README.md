# Wildfire Response Environment

Standalone, programmatic wildfire coordination for evaluation and open-weight RL
experiments. A coordinating agent investigates noisy reports, requests resources
from multiple bases, authorizes offers and sends simulated population instructions.
There is **no HappyRobot dependency, live dispatch, live messaging or API-key
requirement** for the CPU environment. This repository is the development source
of truth; the Hackspain checkout is historical context, not a required service.

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
optimal policy. A public Space, AgentEnv/Harbor adapter and viewer are not claimed.

## CPU quickstart

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
