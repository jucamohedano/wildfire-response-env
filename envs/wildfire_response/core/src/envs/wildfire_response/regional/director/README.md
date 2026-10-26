# Regional director model adapter

## Current terrain/protection environment

The current adapter is `wildfire_core.ops_model.OpsModelPolicy`, contract
`wildfire-ops-model-tools-v1`. The regional adapter documented below stays available
unchanged for legacy episodes; its five-field contract is not sufficient for the
new protection protocol. The shared bounded policy loop now provides explicit
contract hooks, rather than duplicating provider/budget/error handling.

The new model still receives two tools, `check_action` and `submit_action`.
Situation and received report data are already included in each observation.
Both tools accept all nine core action lists: `requests`, `authorizations`,
`releases`, `messages`, `readback_confirmations`, `readback_corrections`, `triage`,
`clarifications`, and `protection_orders`. A short model-only `explanation` is
recorded but stripped before checks/commits. Empty lists express waiting; malformed
outputs remain invalid actions, not a tactical fallback. Checks do not advance
time; a submitted combined action advances two model minutes.

Public context now includes readbacks/corrections, report assessments, follow-up
requests, order sequences and delivery/acknowledgement/execution, smoke telemetry,
the80% safety reward weights, current wind and received station context. Static
fuel/height samples are supplied at public sectors rather than sending full raster
arrays on every provider request. There are no private labels/checkpoints, future
reports, evaluator endpoints, credentials, satellite pixels or recommended plans.
Known IDs are generated from that public context; the engine remains the authority
for current semantic validity and safety checks.

`eval/run_ops_model.py` runs exactly four development fixtures: false alarm,
urgent report, conflicting/delayed reports with multiple fires, and wind shift.
It runs paired passive/scripted-evacuation baselines on those same cases, records
canonical trajectories and model requests, separately stores private verification,
and exact-replays completed episodes. This is a small smoke evaluation on one
source region, not held-out generalization evidence or model training.

```sh
PYTHONPATH=envs/wildfire_response/core/src .runtime/ui-venv/bin/python eval/run_ops_model.py \
  --allow-provider --model thinkingmachines/inkling:free \
  --key-file ~/.config/claude-code/.env --output .runtime/ops-model-trial \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --max-requests 160 --max-tokens 2048 --seconds 1200 \
  --stop-preview-pid-file .runtime/ui-map-refinement/server.pid
```

The optional key file is parsed only for
one literal `OR_API_KEY` assignment, without shell execution, and never copied
to outputs. By default the key comes from the process environment. A live trial
requires explicit provider authorization and a currently listed zero-price,
tool-capable `:free` model. Routing requests zero-price prompt/completion and no
provider fallback. The global cap is160 attempts and20minutes, with90second request
deadlines and at least3.2seconds between calls. Any provider error, limit or local
budget stops the suite without retries, other-model fallback or paid routing.
Partial attempts are saved as unscored, never scored as failures or demonstrations.
The optional cleanup flag only stops a PID verified as this repository's local
UI server. No inference server, GPU job or background worker is started.

The current free-model request omits the optional `parallel_tool_calls` flag:
the published free endpoints do not advertise it, so `require_parameters=True`
can otherwise exclude them. The client still accepts exactly one complete named
tool call and never executes parallel actions. The legacy regional payload is
unchanged. Provider HTTP errors retain only a bounded, credential-redacted message
for diagnosis, not raw bodies or account identifiers.
The ops request also leaves reasoning at the provider default rather than forcing
it off: some free endpoints require reasoning. The completion token cap still
bounds its output; private reasoning text is not retained as a training label.

The old GRPO recipe remains regional; this change fixes current-contract model
evaluation, not the training algorithm or a trained-policy claim.

## Legacy regional adapter

### Explicit CloudRift continuation

`eval/run_cloudrift_ops.py` uses the same four-case runner with an explicitly
authorized `CloudRiftOpsPolicy` and the exact model
`deepseek-ai/DeepSeek-V4-Flash-0731`. The ordinary regional/ops model policies
still reject non-`:free` models; a separate `--allow-cloudrift` opt-in is required.
No VM, rental, GPU process, provider substitution or SDK retry is started.

```sh
PYTHONPATH=envs/wildfire_response/core/src .runtime/ui-venv/bin/python -m eval.run_cloudrift_ops \
  --allow-cloudrift --key-stdin --usd-cap 5 --output .runtime/cloudrift-ops-trial \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --max-requests 160 --max-tokens 4096 --seconds 1200 \
  --stop-preview-pid-file .runtime/ui-map-refinement/server.pid
```

The hidden terminal prompt keeps the temporary inference key out of command-line
arguments, files and request/result logs. Alternatively set the dedicated
`CLOUDRIFT_INFERENCE_KEY` in the process environment. The inference key is not a
`rift` CLI compute-login token, and the command does not change cached CLI login.
The REST client sends `stream=false`, `n=1`, temperature/top-p1 and zero
presence/frequency penalties to `https://inference.cloudrift.ai/v1`, with the
current named tools. No OpenRouter routing fields are forwarded, redirects are
rejected, and errors redact the key before retaining a bounded message.

The requested5USD cap is conservative **estimated** spend, not an account-side
billing cap. Public webpage prices and the API catalog disagreed by a factor100
when this path was added. The guard uses the larger raw API-advertised per-token
prices without assuming cache discounts. Before each call it reserves serialized
request bytes plus4096overhead units and the full completion-token allowance;
successful usage replaces that reservation with reported prompt/completion token
counts. Failed or missing-usage calls retain their reservation. This deliberately
may stop early. Report the price source, token counts and estimate separately from
actual provider billing. Request/token/time caps remain independent safeguards.

Slow non-streaming responses may use `--request-timeout 180` (maximum180seconds).
An interrupted current-contract episode can be continued with
`--resume-from <previous-output>/<task-id>` in a fresh output directory. The
runner replays and compares every saved canonical transition before calling the
model, rejects changed or finished traces, and reconstructs short-term memory
only from replay-verified public actions, not an untrusted request log. No scored outcome is fabricated from a
partial run. Always subtract all earlier conservative usage/reservations from
the original authorized spending cap and reduce the remaining request/time caps
before a manual continuation; the new run does not silently reset that allowance.

### Original regional interface

`RegionalDirectorPolicy` replaces neither the simulator nor the director's choices.
It projects public observations into a model context and accepts two named tools:

- `check_action`: up to three drafts per decision, no time advance. Reports only
  current contract validity, not allocation success or future conditions.
- `submit_action`: exactly one final action per decision, applying the engine's
  requests, authorizations, releases and structured messages. Empty lists explicitly
  wait. No tactical choice is filled in by a fallback policy.

The schema uses current public resource, sector, recipient and live offer IDs.
All five action fields are required for the model tools, including a short unscored
`explanation`. Requesting resources does not authorize offers automatically.
The model must prioritize reports, investigate uncertainty, coordinate bases,
authorize offers, respect aircraft/water constraints, communicate and release units.
This implements the existing regional contract; it does not change frozen physics
or rewards, add a training algorithm, or turn the synthetic dynamics into reality.

## Privacy, memory and failure behavior

Context projection whitelists both top-level fields and nested records. All received
reports and all public clear/fire sensor records retain their observation times;
future events, truth labels, checkpoints and credentials are excluded. Report text
is untrusted evidence, not instructions. Context includes the last three submitted
actions, previous public validation error and configured decision/request budgets.
The default contract advances two minutes per action and assumes 60 decisions;
the local runner supplies its actual shorter decision limit when requested.

Malformed, truncated, parallel or unknown tool responses are not repaired into a
legal wait. The local core records an invalid action, advances time and applies its
existing penalties. Well-formed but illegal IDs are likewise rejected by the core.
The typed OpenEnv wire rejects structurally malformed actions before simulation;
the session raises instead of silently waiting, so that failure must be recorded
as an incomplete remote episode, not a comparable penalized rollout.

Only explicit `:free` model IDs are allowed. The reused OpenRouter transport caps
response bytes/deadline; this adapter caps total requests, output tokens and draft
checks, spaces requests and disables parallel tool calls. It requests zero-price
prompt/completion routing and disables provider fallbacks. It stops on HTTP limits,
transport failure or exhausted budget: no retries or paid/model fallback.
Provider compatibility still needs a separately authorized live trial. Parameter
semantics are documented in OpenRouter's [API parameters](https://openrouter.ai/docs/api/reference/parameters)
and [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

## Run without a provider

```sh
python -m envs.wildfire_response.regional.director.run \
  --output .runtime/regional-director-scripted \
  --scripted --max-decisions 60 --max-requests 120
python -m envs.wildfire_response.regional.run \
  --replay .runtime/regional-director-scripted/trajectory.json.gz
python -m unittest envs.wildfire_response.tests.test_regional_director
```

`ScriptedTransport` passes the frozen cooperative baseline through the actual
check/submit tool exchange. It is explicitly **not a model**, and its scores must
never be reported as model performance. An injected transport supports unit tests
and alternate compatible backends without model weights or a provider dependency.

The runner uses training tasks only, verifies the benchmark lock, saves public
trajectories and request records, puts evaluator state in `private/`, independently
reconstructs reward and checks exact replay. It also runs the frozen cooperative
baseline at the same decision budget. Shorter horizons are labelled smoke tests,
not full-budget benchmark scores. Interrupted attempts save `status.json` and a
partial trajectory, with `scored: false`; they do not receive demonstration labels.

## Connect an existing OpenEnv client

With the optional SDK installed and the v2 server running:

```python
from envs.wildfire_response.regional.director.openenv import OpenEnvDirectorSession
from envs.wildfire_response.regional.director.policy import RegionalDirectorPolicy, ScriptedTransport
from envs.wildfire_response.regional.openenv.client import RegionalClient

actor = RegionalDirectorPolicy(transport=ScriptedTransport(), interval=0, max_calls=120)
with RegionalClient(base_url='http://127.0.0.1:8001').sync() as client:
    session = OpenEnvDirectorSession(client, actor)
    result = session.reset('regional-train-001', seed=30001)
    while not result.done:
        result = session.step()
    print(session.total_reward)
```

This session needs no checkpoint, scenario generator or evaluator endpoint. Configure
the policy's decision limit to match a non-default server. Session reset also clears
the policy's per-episode call budget, request records and prior-action history.
For direct core use, construct a fresh policy or call `policy.reset()` on each reset;
keep any cross-episode aggregate provider budget in a separate experiment harness.

## Live trial remains paused

No provider requests are made by default, including when `OR_API_KEY` exists.
Constructing a policy without an injected transport requires `allow_provider=True`;
the CLI requires an explicit mutually exclusive `--scripted` or `--allow-provider`
choice. The latter reads `OR_API_KEY` only from the process environment. No secrets
are loaded from files or copied to traces. A live model ID and tool mode must be
chosen and verified separately; forced and auto modes never silently retry each other.
The initial live budget should be small (the runner defaults to eight decisions,
sixteen requests), with immediate stop on provider limits. Live inference, training
and publication have not been executed by the offline acceptance tests.
