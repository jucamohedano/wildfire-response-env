# Regional director model adapter

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
