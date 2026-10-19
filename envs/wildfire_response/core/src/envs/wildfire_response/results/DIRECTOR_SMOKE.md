# OpenRouter director smoke test — 19 October 2026

## Inkling comparison blocked by endpoint access

Attempted `thinkingmachines/inkling:free` on the same `train-000` scenario with a
four-decision budget and named-choice interface. The public model listing reports
zero prompt/completion prices and tool support, but not forced `tool_choice`.
Added an explicit `--tool-mode auto` compatibility mode, retaining all client-side
choice validation and zero-price/provider-fallback restrictions.

OpenRouter returned **HTTP 403 before the first decision**. A single diagnostic
request confirmed the reason: this free endpoint is restricted to agentic harnesses
recognized by OpenRouter and directs users to its supported apps list. Our custom
runner was not accepted. The [official model page](https://openrouter.ai/thinkingmachines/inkling:free)
also documents the agentic-harness restriction and provider logging of inputs.

Two request attempts, both access-rejected; **zero model decisions, no reward,
no valid model comparison and no usage/cost record returned**. The failed runner
artifact is `.runtime/director-inkling-2026-10-19/`, marked incomplete. This is an
access restriction, not a failed wildfire policy. No paid endpoint was substituted,
and the application did not impersonate a supported app to bypass the restriction.
Use an authorized supported integration, obtain access for this harness, or select
another available free endpoint before rerunning the comparison.

## Named-choice rerun

After replacing raw engine orders with `wildfire-director-choices-v1`, reran the
same model, scenario `train-000`, seed and four-decision horizon:

- **4/4 valid actions, zero invalid actions**, versus 2/4 valid previously.
- Director reward **-0.006006947544642857**, versus **-0.10401570870535715** previously.
- Matched deterministic reward remains **-0.000007672991071428572**.
- Replay reproduces the director observations and reward exactly.
- Runtime **83.476 seconds**; four requests, **23,871 prompt tokens** and
  **1,240 completion tokens**; provider-reported cost **0**.
- Still budget-truncated at 32 ticks, not a naturally resolved fire.

The model selected a broadcast informing `town` on all four decisions, incurring
three redundant-message penalties. This is a remaining policy-quality issue, not
an action-format failure. Repeated messages are deliberately not silently removed.
The short single-seed comparison demonstrates improved command compliance, not
statistically established tactical superiority or generalization.

The translator offers per-vehicle named options derived only from public target
candidates and district status. Exact containment coordinates, role-specific order
fields and recipient/channel mapping are encoded mechanically. The model chooses
which option to use; unknown selections are rejected rather than repaired or
replaced by a deterministic decision. Single-waypoint patrol options narrow the
original action space; historical raw-order results are therefore not identical
action-space benchmarks. Proposals and the current catalog are recorded in
`requests.json` for auditing.

All **33 local tests pass**. Additionally validated **272** individual named fleet
options against engine validation at tick 16 across all six scenario families;
this is offline interface validation, not six model evaluations. Eventual delivery
is still revalidated by the engine as the world changes.

Local artifacts: `.runtime/director-openrouter-named-2026-10-19/`.
Implementation commit: `e2c0e50`. The sections below retain the earlier raw-order
attempts for comparison.

Model: `nvidia/nemotron-3.5-super-vl:free`, invoked with the user's existing
OpenRouter credential. No credential was copied into the repository or artifacts.
The final run uses strict role-specific function schemas, extended reasoning
disabled, public observations and previous-action validation feedback. Invalid
orders remain invalid; the adapter does not substitute a scripted policy.

## Final four-decision run

- Scenario `train-000`, four decisions / 32 simulation ticks, one CPU episode.
- Four model requests; two valid actions, two invalid actions.
- Rejections: inappropriate scout district ID; unsafe/ineffective containment position.
- Director reward: **-0.10401570870535715**.
- Matched deterministic reward: **-0.000007672991071428572**. Less negative is better.
- Recorded-action replay reproduces the director reward and observations exactly.
- Budget truncation, not natural termination; no claim that the incident was resolved.
- Elapsed director episode: **83.202 seconds**.
- Model usage: **16,249 prompt tokens**, **1,985 completion tokens**.
- Provider-reported cost for these four requests: **0**.
- Provider response times: 46.992, 7.870, 18.154 and 8.793 seconds.

Artifacts are local and ignored by Git:
`.runtime/director-openrouter-2026-10-19-strict/` contains `director.json.gz`,
`deterministic.json.gz`, `requests.json` and `summary.json`. The trajectory records
the source digest; the run began while the strict-schema patch was still being
committed, so its Git HEAD alone does not identify the whole working tree.

## Debugging history

The first attempt used two requests: one completion exhausted all 4,096 tokens
on reasoning without a usable action, then the provider returned HTTP 502. The
runner stopped and saved an incomplete artifact rather than retrying or claiming
a completed evaluation. Disabling extended reasoning avoided that failure mode.

The second attempt made three requests but all actions failed validation,
triggering the environment's three-invalid-action truncation. The model mixed
fleet roles and message recipients and added an unsupported root field. A tighter
schema constrains IDs, role arrays and communication recipient/channel combinations.

There were nine request attempts across these three debugging runs. The adapter
did not switch to another model or a paid provider. Reported costs for returned
completions were zero; the failed request did not supply a usage record.

## Interpretation and next work

This validates the model-to-environment connection and reproducible scoring, not
director competence. The selected model still performs worse than the scripted
baseline in this short scenario. All 31 core/data/director tests pass without
network access; the model itself is nondeterministic and provider availability varies.

Next evaluate command compliance across the six scenario families before spending
requests on full-length or held-out runs. Improve command-specific schema constraints
and candidate selection without silently repairing tactical choices. Then measure
population exposure, burned area, evacuation disruption and redundant notices over
matched seeds and horizons. The 2026 official evidence pack has not yet been converted
into playable historical scenarios. No model weights were trained in this test.
