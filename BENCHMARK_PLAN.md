# Benchmark v1 implementation

Approved objective: a competitive programmatic RL benchmark first, followed by a
bounded Qwen learning pilot. Headline ranking prioritizes zero simulated exposure,
then protected population, then additional burned footprint. The graded training
reward is 80% population protection and 20% unburned footprint. Exposure is not a
mortality estimate. Existing evaluation versions are not regraded.

## Review increments

1. Delivered-evidence observation contract and public-only action validation.
2. Population/area outcomes, independent accounting, replay and calibration profiles.
3. Eight-family catalog: 120 groups, 80/20/20 train/validation/test, two seeds per group;
   public-information reference policies, development communication ablations.
4. Current Python/UI/OpenEnv execution, evaluation reports and manifest metadata.
5. Train-only demonstrations, frozen retrieval baseline, gated SFT/GRPO recipe and
   private release documentation.

Each increment gets an issue and draft PR; no automatic merge. CPU validation starts
with twelve development cases and one worker before broader baseline evaluation.
Held-out policy results must not be used for tuning. Source data, synthetic
assumptions, actor evidence and private evaluator artifacts remain distinct.

## Execution gates

The current VM is terminated. No GPU rental, paid inference, model download,
training job or publication is authorized by this code implementation. The pilot
must first meet valid-action and reward-diversity gates, then receive compute and
a spending limit; wall time is capped at two hours. Public publication additionally
requires code-license and asset-rights clearance. If gates cannot be met, preserve
the working recipe and measured blockers rather than claim learning success.
