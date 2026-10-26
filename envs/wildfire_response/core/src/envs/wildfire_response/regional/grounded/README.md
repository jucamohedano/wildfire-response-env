# Spanish source-grounded development bridge

This is an opt-in Python environment, separate from the locked 360-case generator
and regional benchmark. It joins a private EGIF anchor pack to the existing ops
controller, fire kernel, resource/readback lifecycle and four objective checks.
It is **source-informed synthetic simulation**, not a historical reconstruction,
real dispatch guidance, held-out evaluation or a calibrated safety model.

## What is connected

- A pinned 2021 EGIF ZIP supplies four incident anchors: small Almería, small
  Madrid, large Huelva and a large incident elsewhere. Selection uses incident-ID
  hashes within explicit strata and requires consistent source chronology and a
  0–120-minute detection-to-first-ground-arrival local-clock interval.
- Each anchor produces three predefined wind variants, for **12 development
  cases**. Variant IDs are opaque; all variants share the anchor's seed and group.
  Incoming reports and scheduled changes are withheld until their receipt/tick.
- Actual source detection time sets the relative exercise clock. The initial
  burning cell is hypothetical; detection is not claimed to be ignition. A
  07:00–20:00 local-clock daylight gate is assumed and advances with model time;
  it is not a solar calculation, verified aviation limit or UTC reconstruction.
- Half the source ground-response interval, rounded up and clipped to 1–60, is
  used as an **assumed ground-transit component**. Cabezudos adds six assumed
  minutes; mobilisation and grid-distance increments remain synthetic. This
  decomposition is not identified by EGIF and must not be described as measured
  road travel. Dispatch delay still delays actual arrival; no arrival is forced
  at the recorded historical time. Aircraft transit remains wholly assumed.
- Requests, offers, readback corrections/confirmations, authorisation, sensor
  evidence, suppression, population messages and failed-delivery retries use the
  existing ops lifecycle. There is no automatic commander deciding for the agent.

## The new missing-component bridge: location follow-up

`clarify_reports([report_id])`, or action field `clarifications`, accepts up to
four received unlocated reports, once each. Like other committed tools it advances
time. The assumed response delay is two model minutes. A reply may create a new
located claim referencing the original observation time, or acknowledge that the
location remains unresolved. It never creates sensor confirmation or a truth label.

An approximate reply comes from the scenario's synthetic caller claim, not from a
query of hidden fire state. In the large exercises it points near a separate,
assumed fire front. The controller must investigate and verify it. Receiving a
reply, calling the tool or sending more messages earns no direct reward.

Validation is atomic across clarification, mission and communication fields. An
invalid combined action applies none of them, retains prior missions, advances
time and uses the existing invalid-action limit. A repeated clarification is
invalid; `check_orders` can validate one without creating a request or advancing.
Delivery, readback, arrival and physical effects remain different events.

## Data-quality and privacy boundaries

The builder checks the exact archive SHA256 and streams a bounded XML member.
The private pack has strict field sets, chronology, four selection strata and a
canonical digest; it is written exclusively with mode 0600 and never overwrites.
Cause fields, identities, resource totals and narratives are not imported.

Source weather is deliberately **not imported**. Some source `hora` fields have
calendar wrappers in different years from the incident; others are absent or
accompany zero temperature/wind and missing humidity. This is unresolved source
semantics, not proof that every original weather value is wrong. Wind variants
use the engine's relative index, never a fabricated conversion from km/h to m/s.

Historical IDs, source dates, response references, quality flags, final burned
area and recorded control/extinction times stay outside actor observations.
Recorded final outcomes never schedule containment or extinction. Final-area
strata are evaluator case-selection metadata, not an initial fire-size observation.
All cases use the common approximate synthetic arena and hypothetical five-unit
fleet; Madrid/Almería cases are not geographically reconstructed in Huelva.

The environment publishes capability flags. Shelter-in-place, in-person warning
teams, incident-specific roads, historical weather replay and live messaging are
**unsupported**. In particular, shelter actions are rejected, not silently
translated to evacuation. New OpenEnv/model-server wiring is not added here.

## Build and run on CPU

From the repository root, Python 3.12+, using the preserved source ZIP:

```sh
mkdir -p .runtime/spanish-development/private
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.grounded.anchors \
  --egif-zip /path/to/preserved/egif-spain-2021.zip \
  --output .runtime/spanish-development/private/anchors.json
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.grounded.run \
  --pack .runtime/spanish-development/private/anchors.json \
  --output .runtime/spanish-development-smoke --smoke
```

Omit `--smoke` for all 12 cases and the passive, control and clarifying baselines.
Use a fresh output directory. Public traces go in case/policy directories; private
verification artifacts go under `private/`. `summary.json` reports each objective,
invalid actions, clarification answers, termination and truncation. No provider,
API key, live message or GPU is used. These cases have no held-out split.

Installed Python API:

```python
from wildfire_core.spanish import SpanishDevelopmentEnv, ClarifyingControlBaseline

env = SpanishDevelopmentEnv(".runtime/spanish-development/private/anchors.json")
observation, info = env.reset("es-dev-002-v1")
policy = ClarifyingControlBaseline()
while True:
    observation, reward, terminated, truncated, info = env.step(policy(observation))
    if terminated or truncated:
        break
result = env.verify_episode()
```

`submit_action` and all existing ops tools remain available. Independent public
replay requires `verify_spanish_trajectory(trace, trusted_anchor_pack)` or:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.grounded.run \
  --pack .runtime/spanish-development/private/anchors.json \
  --replay .runtime/spanish-development-smoke/es-dev-002-v1/clarifying/trajectory.json.gz
```

Replay binds the trusted pack digest, canonical task/manifest/scenario, versions,
seed, limits, observations, actions, rewards and completion flags. It re-executes
the simulator, not just a scoring formula. A pack digest detects changes; it does
not authenticate an arbitrary pack from an untrusted author. Retain the checked
pack hash separately. Observation privacy is not an OS sandbox against code/data
inspection. The old ops verifier intentionally rejects this interface.

## Remaining gates

Before expanding or training for generalisation: decode official resource and
incidence categories; verify geography/road and weather joins with availability
times; implement location-specific shelter and warning fallback with state effects;
calibrate safety priorities and fire/population dynamics with domain input. Keep
all variants of each source incident in one future split. Do not update training
retrieval memory with validation/test outcomes. Source summaries are not optimal
demonstrations and a best-found baseline is not a proven optimum.

The existing equal-quarter objective rubric is unchanged; always report its four
flags separately, since a partial score can still contain a safety failure. Raw
EGIF/selected anchors remain local, ignored evaluator artifacts. Reuse rights and
the repository's publication/licence restrictions remain unresolved.
