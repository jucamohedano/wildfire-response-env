# Qwen3 GPU pilot results — 2026-10-19

**The environment is connected to real local-weight GRPO training, but a useful
learned director has not yet been demonstrated.** The small model uses the tools,
yet does not complete the request → offer → authorization dispatch cycle in these
samples. Zero within-group reward variation then prevents GRPO learning.

## Model, machine and setup

Official `hf models` searches/cards identified post-trained Qwen3-0.6B and
[Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B); the pilot used the latter at
revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`. No provider API key or paid
inference endpoint was needed. The user-supplied CloudRift RTX 5090 VM was selected
by its full rental identity and verified through SSH, not confused with the other
L40S rental. No machine was rented, reset or terminated by this work.

PyTorch 2.11.0+cu128 passed a BF16 CUDA kernel on compute capability 12.0. Native
TRL tool schemas and grouped batch settings were validated. The first attempt
failed during pretraining evaluation because system Python lacked `Python.h` for
Triton's fused loss kernel. It remains recorded as incomplete with zero optimizer
updates. An isolated uv-managed Python 3.12.9 fixed this without sudo or modifying
system Python. Triton's driver compilation then passed.

Direct dependencies: Transformers 5.20.1, TRL 1.16.0, PEFT 0.21.2 and Hub 2.2.0.
The older Hub 1.17.0 pin was correctly rejected by the resolver before training;
the remote training dependencies are separate from the local `hf` search CLI.

## Completed pilot 002: two-tool-turn plumbing test

Source revision `697a9c0`, two optimizer steps, 768 completion tokens, LoRA rank 8,
BF16, two samples per group and non-thinking mode:

- **45.987 seconds** including loading and before/after training-scenario scoring;
  peak PyTorch allocated GPU memory **4.224 GiB**.
- **12 rollouts** independently verified and exactly replayed, including passive
  full-horizon completion; not 12 naturally resolved incidents.
- **23 checks and one submitted action** across those rollouts. The two-turn cap
  could be consumed entirely by checks, leaving too little room to act.
- Gradient norms **0.335387, 0**; the adapter digest changed. The nonzero update
  came from small outcome variation, not demonstrated effective fire suppression.
- Before/after sampled mean reward both **-30.572629**. No policy improvement or
  held-out generalization was demonstrated.

## Completed pilot 003: more room to issue orders

Source revision `dbd48e2`, fresh base/LoRA initialization, still two optimizer steps
but 1,536 completion tokens and up to eight tool turns. The training-only prompt
clarified that checks do not execute orders and should be followed by submission.
Frozen simulator inputs and reward weights were unchanged. This is not an isolated
ablation of one parameter, nor a continuation of the first adapter.

- **45.457 seconds**, peak PyTorch allocated GPU memory **4.507 GiB**.
- **12 more rollouts** independently verified and exactly replayed.
- **12 submissions, 16 checks, 40 resource requests and 16 messages**;
  **zero authorizations or releases**. Requesting units alone did not dispatch them.
- Both training groups had identical outcome rewards within their respective
  sampled group: gradient norms **0, 0**, and the adapter digest **did not change**.
- Before/after sampled mean reward **-2.002199 / -2.003199**. Because the weights
  are identical, this small difference is sampling variation, not learning.
- Behavior differs from pilot 002 because prompt/rollout settings changed. It
  cannot be attributed to a learned improvement from these zero-gradient steps.

The runs exercised the real tool interface, backward/optimizer path, LoRA saving,
full-horizon outcome reward and independent evaluator—not a canned reward or a
scripted model substitute. Nevertheless, completed trainer steps are not sufficient
evidence of useful training. Neither checkpoint is promoted as a trained director.
The expanded context/rollout settings were not large enough to force meaningful
closed-loop control, and simply extending a cap does not teach the model to continue.

## Artifacts and checks

`pilot_results.json` records metadata, full before/after metrics, gradient norms,
adapter digests, action counts and the unchanged benchmark fingerprint. Full
adapters/tokenizers, public trajectories, separate private evaluator artifacts,
logs and a software freeze are backed up locally under
`.runtime/cloudrift-qwen3-pilot-2026-10-19/` and retained on the VM under
`~/wildfire-rl-pilot/`. No checkpoints or private trajectories were pushed to Git
or Hugging Face.

Regression discovery: **92 tests, 83 passed and nine optional SDK tests skipped**,
79.747 seconds. GPU preflight, native tool extraction and five CPU environment/
benchmark checks also passed on the VM. The new training-only budget test passed
locally. The previous OpenEnv SDK was not installed into this isolated training
venv; this pilot invokes the same core directly through TRL's environment factory.

Both completed runs had 15-minute hard timeouts, no telemetry integrations/Hub
uploads, no OpenRouter calls and no validation/test model evaluation. GPU memory
returned to **0 MiB used / 0% utilization** afterward. The VM remains rented and
running; stopping a training process does not stop CloudRift rental billing.

## Next useful experiment

Do not scale zero-gradient runs unchanged. First build a small training-only,
programmatically verified warm-start set showing valid multi-turn sequences:
investigate/request → receive offer → authorize → act on observations → release,
along with appropriate communication and restraint. Do not automatically treat
the over-warning reference as an optimal teacher or label truncated rollouts as
demonstrations. Keep actor prompts free of private truth/future labels.

Then compare the warm-started actor against its own untrained/prompt-matched
baseline, with longer actual decision rollouts and more than two samples per
group if measured memory permits. Check for real outcome diversity and nonzero
finite gradients before a longer GRPO job. Retain the unchanged programmatic
outcome reward—no reward for message/check volume or format alone. A fresh final
evaluation set is needed for new model-generalization claims; the earlier held-out
baseline results are already observed. No public publication readiness is claimed.
