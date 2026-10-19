# Full-budget readiness audit — 19 October 2026

## Baselines and replay

Ran passive, random-valid and deterministic policies on one training configuration
from each of the six families: 18 episodes, each allowed the full default budget
of 32 decisions / 256 ticks. All baseline actions were valid. All 18 serialized
trajectories replayed with identical observations, lifecycle and rewards. Final
outcome components independently reconstruct the recorded episode rewards.

Thirteen episodes ended naturally. Five passive-policy episodes exhausted the
budget without resolving the incident; they are not successful fire responses.
Random-valid and deterministic each resolved all six selected cases naturally.

Mean rewards (closer to zero is better):

- Passive: **-4.971938505**.
- Random-valid: **-0.000030411**.
- Deterministic: **-0.003667688**.

The deterministic policy's false-alarm warning cost dominates its score here.
Random-valid is strong on these six easy configurations; this is a warning that
the smoke suite cannot establish a robust policy ranking. All but the multiple-fire
case have zero population exposure even under passive behavior. A larger, calibrated
training sample with urgent unconfirmed threats is needed before claiming a useful
training benchmark. No reward weights or scenario definitions were changed to make
this audit pass.

Baseline evaluation took **96.176 seconds**, with peak process RSS **56,688 KiB**.
The counterfactual audit took **29.446 seconds**, peak RSS **24,256 KiB**. Episodes
within each run were serial; the separate audit and evaluation briefly shared the
host, so these wall times are not isolated throughput measurements.

## Counterfactual reward checks

Eight additional synthetic fixture rollouts passed seven checks over their whole
episode budgets, rather than comparing only the first interval:

- Early farm warning beats a delayed warning and reduces population exposure.
  Reward: **-0.004918170** early versus **-0.885122377** delayed.
- Restraint beats indiscriminate evacuation on a false alarm:
  **0** versus **-0.024609375**.
- Repeating an information notice loses reward: **-0.014** versus **0** once,
  without changing burned terrain, population exposure or evacuation movement.
- Deliberately aborting with three invalid actions cannot beat passive behavior:
  **-103.030220697** versus **-0.885122377**.
- Other scripted audit actions have no unexpected validation failures.

These fixtures are evaluator checks, not real incident observations, demonstrations,
or held-out training examples. Passing them rules out those particular failures;
it does not prove the reward is immune to all exploitation.

## Scope of the readiness gate

The gate permits a small, bounded **policy-parameter** experiment only after the
reward checks, same-weight checks, six-family baseline matrix and all replays pass.
It does not authorize weight training, paid inference, publication or field use.
The first experiment compares the original scripted baseline with a single Boolean
neighbor: investigate first when no fresh shared-sensor fire is visible. This
neighbor observes only public reports, target candidates and sensor memory.

Selection uses six training configurations. The selected parameter is saved before
six different validation configurations are evaluated; validation cannot alter the
selection. Test scenarios are excluded from this experiment. Verification-first
is not presumed safe: delayed response to an urgent unconfirmed threat could be
worse, so population exposure must be inspected separately from aggregate reward.

## First bounded hill-climb result

Completed 24 serial policy evaluations: original and verification-first on six
training configurations, then the original and frozen training winner on six
separate validation configurations. Search time was **166.401 seconds**. No model
inference, weights, paid APIs or test-split evaluations were involved.

The single Boolean neighbor was selected on training reward:

- Original training mean: **-0.003667688**.
- Verification-first training mean: **-0.000008766**.
- Both had zero population exposure and zero invalid actions on those six cases.

The improvement **did not generalize**:

- Original validation mean: **-0.046319431**.
- Frozen winner validation mean: **-0.060525726** (worse).
- Mean burned-terrain component rose from **0.046283328** to **0.060525726**.
- Both retained zero population exposure and zero invalid actions in this sample.
- Natural completion fell from **4/6** to **3/6** validation episodes.

Verification-first eliminated the validation false-alarm warning cost but worsened
smoke, duplicate, conflicting-location and wind-shift outcomes; it modestly improved
the multiple-fire case. The conflicting-location regression was substantial:
**-0.000151728** to **-0.068822047**. The policy only investigates its first available
reconnaissance candidate when no fresh fire is seen; it has no robust mechanism for
revisiting competing reports or maintaining strategic coverage. These are limitations
of this particular scripted neighbor, not evidence that investigation is generally bad.

**Do not promote this change.** The runtime baseline and model director were not
modified by the experiment. The saved training winner remains frozen as an audit
artifact; validation results did not update its parameters. This demonstrates why
training improvement alone is insufficient. Next expand the training calibration
batch with harder urgent/unconfirmed and conflicting-report cases, and investigate
risk-aware verification rather than an unconditional wait-for-confirmation rule.
Keep numerical reward weights fixed while doing so. Weight training remains premature.

## Artifacts

Local, ignored outputs:

- `.runtime/wildfire-full-episode-audit-2026-10-19/`: baseline trajectories, private
  scoring artifacts, coverage summary and per-trajectory replay readiness results.
- `.runtime/wildfire-reward-audit-2026-10-19/`: eight counterfactual action/reward
  sequences, component scores and seven reward checks.
- `.runtime/wildfire-readiness-2026-10-19.json`: combined gate results.
- `.runtime/wildfire-parameter-search-2026-10-19/`: frozen parameter selection,
  public trajectories and separate training/validation scores.

Reproduction commands are in the environment README under CPU readiness checks.
No model API requests or GPU compute are needed for these runs.

All **38 core/data/director/readiness tests pass**. The full baseline matrix's
18 trajectories replay exactly, as do separately checked training and validation
trajectories saved by the parameter-search runner. No production policy was changed.
