# Frozen held-out baseline audit — 2026-10-19

The protocol and benchmark lock were committed at `e768e14` **before** either
held-out split was run. Every validation and test configuration was evaluated
once against all four frozen baselines. No simulator, reward, scenario or baseline
changes were made in response to these outcomes. See `heldout_results.json` for
all 48 case scores, outcome components, flags, timing and provenance, and
[BENCHMARK.md](BENCHMARK.md) for the predeclared protocol.

Benchmark: `regional-benchmark-v0.2.0`, SHA256
`e882e0f8ba58246ef3e4c3d271d32a6d54a1756d980be52bcccb524ff84ad840`.
All 48 runs record the same source digest. All independent accounting checks and
exact replays passed; zero invalid actions and zero current weak-policy flags.
These checks do not certify simulator realism or sufficient learning difficulty.

## Validation: six configurations, 24 episodes

- Passive: mean reward **-20.714873**, mean exposure fraction **0.190476**,
  **1/6** natural completions.
- Random-valid: **-0.396761**, exposure **0**, **0/6** natural completions.
- Blanket-warning: **-1.672949**, exposure **0**, **1/6** natural completions.
- Cooperative reference: **-0.012418**, exposure **0**, **6/6** natural completions.
- Sequential CPU evaluation including replay: **26.797 s**, peak RSS **32,556 KiB**.

## Test: six configurations, 24 episodes

- Passive: mean reward **-11.191182**, mean exposure fraction **0.095238**,
  **1/6** natural completions.
- Random-valid: **-20.120926**, exposure **0.190476**, **0/6** natural completions.
- Blanket-warning: **-30.244496**, exposure **0.285714**, **1/6** natural completions.
- Cooperative reference: **-0.021950**, exposure **0**, **6/6** natural completions.
- Sequential CPU evaluation including replay: **21.484 s**, peak RSS **33,032 KiB**.

## What did not generalize, and what this means

Blanket-warning exposes 200/350 simulated people in each of `regional-test-033`
and `regional-test-034`, whereas passive exposes none in those cases. Both policies
expose 200/350 in `regional-test-031`. Warnings change the synthetic population's
position; the simulator uses slow straight-line movement toward a fixed synthetic
refuge, with blocking/exposure checks but no realistic route planner or validated
road network. This result must not be interpreted as real evacuation advice.
It is a simulator sensitivity and realism limitation, not a failure to be silently
removed. The current weak-policy flag rules do not diagnose evacuation realism.

The reference combines suppression and warnings, so its success does not isolate
the value of communication, aircraft, cooperation or any one capability. Six cases
per held-out split, shared templates and one map cannot establish broad
generalization. False-alarm restraint can beat the over-mobilizing reference even
though it beats weak baselines on active-fire tasks. No policy is claimed optimal.

These outcomes have now been inspected. They are an observed baseline audit, not
a fresh secret model test set. Future task or policy development needs a separately
versioned, genuinely unobserved evaluation set for a new final generalization claim.
Do not tune this benchmark and relabel the same splits as untouched.

No model inference or training occurred. The next engineering check is the v2
director tool adapter on training scenarios with scripted transports. A real model
trial remains explicitly gated; publication still requires licensing/asset checks,
container validation and clear synthetic-data/realism limitations.
