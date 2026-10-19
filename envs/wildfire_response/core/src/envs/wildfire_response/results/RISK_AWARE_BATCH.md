# Risk-aware director batch — 19 October 2026

## Frozen setup

The free `nvidia/nemotron-3.5-super-vl:free` model uses the existing named-choice
interface, with a frozen `risk-aware` prompt. The prompt balances precautionary
warnings against verification, asks for parallel investigation and consideration
of competing reports, and explicitly discourages repeated notices. It does not
change reward weights, simulation events, legal action choices or model weights.

Planned cases, all training-only, with different locations from the first smoke
batch: `train-009` conflicting reports, `train-010` wind shifts, `train-007` false
alarm, `train-011` multiple fires, `train-006` smoke and `train-008` duplicates.
Each episode permits 32 decisions / 256 ticks. A shared safety cap permits at most
24 API requests across the entire batch. Partial episodes are retained, not scored
as completed episodes. Completed model trajectories replay and receive matched
deterministic comparisons at the same seeds and horizons.

The batch stops on the first provider limit or other transport error, with no
retry, model/provider switch or paid fallback. Provider HTTP 402/429, access/service
errors and the local request cap are distinct stop reasons. The local cap is not
evidence of an external quota rejection. No validation or test cases are used.

## CPU calibration of the planned cases

Twelve scripted full-budget rollouts compared passive and deterministic policies:

- Passive mean reward: **-8.523169044**; mean exposed population fraction
  **0.084059502**; **1/6** natural completions.
- Deterministic mean reward: **-0.036686721**; zero population exposure;
  **4/6** natural completions.
- In the conflicting-report case, passive behavior exposes **24.777748%** of the
  simulated population, while the deterministic baseline protects everyone.
- Multiple-fire and duplicate cases remain budget-truncated even with the
  deterministic policy, showing that successful command parsing is not resolution.

These cases offer greater outcome consequences than most of the original six
smoke configurations, but are still synthetic examples on one approximate map.
Some include a scripted later ignition at an occupied district: an explicit
scenario stressor, not a physics-derived forecast or real historical observation.

## First completed model episode

`train-009` finishes naturally after eight valid decisions with no invalid actions.
However, population exposure is **0.247777484** and reward is **-24.787759152**,
versus **-0.000009821** for the matched deterministic baseline. There are five
redundant messages. The model repeatedly informs `town` but sends no evacuation
order; completion therefore does not mean a successful response. Runtime:
**196.097 seconds**. Eight returned completions report zero cost.

The recorded trajectory replays exactly. This is an exploratory observation,
not evidence that the new prompt is better than the standard prompt, which has
not been run on this same scenario in a paired model comparison. The default
director profile and production runtime remain unchanged.

## Artifacts and checks

- `.runtime/risk-aware-calibration-2026-10-19/scores.json`: scripted calibration.
- `.runtime/director-risk-aware-batch-2026-10-19/`: batch status, completed episode
  outcomes, public trajectories, named-choice proposals and request usage metadata.
- **44 local tests pass**, including simulated first-limit stop, no retry,
  shared-cap accounting, access-versus-quota distinction and test-split exclusion.

The fake HTTP 429/403 printed by unit tests are simulated checks, not live quota
events. Actual provider status is recorded separately in the batch summary.

## Batch stopped; no automatic continuation

The live batch stopped on request attempt **16** during the wind-shift case.
Fifteen completions returned successfully and produced fifteen valid actions:
eight in the completed conflicting-report episode and seven in the incomplete
wind-shift episode. The latter is not assigned a completed score or used as a
demonstration/preference label. The other four planned cases were not run by the
model. Returned completions report zero cost; the failed attempt returned no usage.

The stop reason is **transport/runtime failure**, with no reported provider HTTP
status. It is **not confirmed quota exhaustion**: no HTTP 402/429 was observed,
and the local 24-request cap was not reached. The original saved error category
does not distinguish timeout from another transport/runtime failure, so the
report does not invent a more specific cause. Future run status now retains
exception type. No retries, further inference or automatic model switch followed
the stop. Completed request latencies ranged from **7.071 to 75.551 seconds**.

### Alternatives identified, not executed

- [Gemma 4 31B free on OpenRouter](https://openrouter.ai/google/gemma-4-31b-it:free)
  advertises function calling and is listed at zero prompt/completion price.
  It could test a different model/backend with the existing key. Actual access,
  task quality and reliability still need a small authorized smoke run; it is
  not a workaround for an account-wide OpenRouter quota.
- [Groq's OpenAI-compatible API](https://console.groq.com/docs/openai) could use
  a separate provider key. Its [free-tier limits](https://console.groq.com/docs/rate-limits)
  currently list GPT-OSS 20B/120B with 8,000 tokens/minute and 200,000 tokens/day.
  Our completed episode averaged roughly 7,700 prompt tokens per call before
  output, so prompt compaction and stricter pacing are prerequisites, not just
  swapping a base URL. This provider was not configured or called.
- CPU scripted-policy search and scenario calibration can continue without
  inference access. No weights or GPU compute are required for those checks.

The model also remains weak on prevention and repeats notices. A different
endpoint might address latency, but must not be presumed to fix decision quality.
Before another comparison, review whether static action effects and wind conventions
are stated explicitly enough in the actor contract. In particular, information-only
messages do not initiate evacuation; the benchmark's scripted ignitions also limit
real-world interpretations. The risk-aware profile is not promoted.

## Gemma follow-up: confirmed live limit

With separate user authorization, attempted `google/gemma-4-31b-it:free` on
`train-009`, retaining the same risk-aware prompt, forced named-choice interface,
seed, initial observation and 32-decision episode horizon. A separate request cap
of twelve bounded this single-case comparison. Initial observations and seeds
were checked against the completed Nemotron trajectory and match exactly.

**The first request returned HTTP 429.** Stopped immediately after that one attempt:
zero returned completions, zero simulation decisions and no model comparison score.
The failure returned no usage record. There was no retry, model/provider rotation
or paid fallback after the limit. This is an actual live rejection, unlike the
simulated 429 used in unit tests.

Artifacts: `.runtime/director-gemma-risk-aware-2026-10-19/`, including
`stop-status.json`, `status.json`, the empty request ledger and a zero-transition
partial trajectory. These are not demonstrations or evidence of Gemma performance.
HTTP 429 alone does not identify whether the exhausted allowance was account-wide,
provider-specific, per-minute or daily; no reset time is inferred.

Pause OpenRouter inference until the applicable limit resets or choose a separately
authorized provider. Groq remains a possible alternative, requiring its own key,
provider-specific parameter handling, smaller prompts and free-tier pacing. No
Groq calls or new credential provisioning were performed.

The fourteen director/batch regression tests pass after the attempted comparison;
no Python source or prompt changes were needed for this run.
