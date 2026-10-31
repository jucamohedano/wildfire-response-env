# Experimental fixed-area scoring v1 with integrated hosts v2

`wildfire_containment_score_v1.integrated.IntegratedContainmentEnv` is a trusted,
opt-in containment episode with the fixed-area 80/20 reward from
[extended-scoring-v1.md](extended-scoring-v1.md). It preserves the existing
containment environment and standalone helpers. It is not a released benchmark,
a source-map approval, a calibration claim, or an integration of the separate
weather runtime by default. A separately versioned combined host is described
below. The containment adapter's input, runtime and uncertainty restrictions
remain in force.

## Construction and runner contract

Install the core and containment packages as described in
[containment-v1.md](containment-v1.md), plus `envs/wildfire_containment_score_v1`.
The trusted evaluator must load the inputs, network, scenario pins, scoring mask,
containment configuration and expected identity from an independently approved,
versioned configuration. The following assumes `release` is such a trusted
configuration; it is **not** produced from the episode being graded:

```python
from wildfire_containment_score_v1.integrated import (
    IntegratedContainmentEnv, verify_integrated_outcome,
)

env = IntegratedContainmentEnv(
    release.inputs, release.network,
    expected_inputs_sha256=release.inputs_sha256,
    expected_network_sha256=release.network_sha256,
    allow_unapproved_data=release.allow_unapproved_data,
    containment_config=release.containment_config,
    pressure_scale=release.pressure_scale,
    scoring_mask=release.scoring_mask,
)
observation, info = env.reset(release.scenario_id, seed=release.seed)
# Drive env.step(action), env.advance_time(minutes), and containment calls.
# Each returns the same shape as the containment adapter; mid-episode reward is 0.
# After the episode ends, or after a host interruption, apply the external gate:
grade = verify_integrated_outcome(env, expected_identity=release.expected_identity,
                       scoring_mask=release.scoring_mask,
                       expected_configuration_sha256=release.configuration_sha256,
                       infrastructure_timeout=False)
if grade['status'] == 'full_day' and grade['verified']:
    score = grade['score']
```

`release.expected_identity` must contain exactly `interface_version`, `task_id`,
`seed`, `inputs_sha256`, `network_sha256`, `scenario_sha256`,
`implementation_sha256`, and `scoring_mask_sha256`. The interface version is
`wildfire-containment-integrated-scoring-v2`. The mask hash is `mask_sha256`
of the sorted unique row-major IDs as described in the scoring contract. Pin
source identities only after reviewing and freezing core, containment, integrated
and scoring/verifier sources. The expected `implementation_sha256` binds the
separately pinned source identity and physics configuration, as described below.
`env.trajectory()` provides these fields for comparison, but reading the expected
value from that same run is not a trusted pin. The mask must be independently selected for the pinned grid,
fixed before reset, and a subset of cells burnable at handover; the runtime
cannot establish that it is representative. Empty masks are mathematically
allowed but require an explicit release decision.


The physics pin is mandatory for both integrated gates. Compute it from the
independently approved containment configuration and pressure scale with
`configuration_sha256(config, pressure_scale)`. It validates the settings,
normalizes numeric parameters using `ContainmentKernel` and the adapter's
pressure validation, and hashes compact, sorted-key UTF-8 JSON containing
`version` (`wildfire-containment-scoring-configuration-v1`),
`containment_config` (including defaults), and `pressure_scale`. Pin this value
before the episode. The eight-field verifier identity's `implementation_sha256`
is the result of `scoring_implementation_sha256` applied to the independently
pinned source identity and configuration hash. `trajectory()` additionally reports the independent
`source_implementation_sha256` and `configuration_sha256`; they are
trusted-host data. The source digest remains separate from, and does not replace,
the old core, containment, or weather source pins. The identity binding makes
the older generic verifier reject altered physics against an existing expected
identity, but production runners must use the integrated gate to require and
check the separate configuration pin before and after replay.

For runners that require the existing shape, `env.verify_episode()` returns
`passed`, `exact_replay`, `independent_reward_agreement`, and `outcomes` with
`reward_version` and `graded_score` on success. Its failed result contains
false flags and an empty outcomes mapping. This check alone does **not** compare
against an independently supplied identity or mask and does not decide whether
the episode is eligible. The expected reward version is
`wildfire-extended-incident-day-outcomes-v1`. Use the external gate and accept
only its verified `full_day` status for a full-day score. It returns a verified
zero for invalid abandonment; runners may record that separately or leave it
unscored. Censoring takes precedence over abandonment and is unscored; generic
truncation, unfinished runs and infrastructure timeout are unscored. Pass
`infrastructure_timeout=True` even if an in-flight action later terminates.

A trusted host can call `env.truncate()` for a non-timeout interruption, without
advancing simulation time. It is recorded for deterministic replay. This is not
an actor tool. Do not translate a timeout into an eligible score. `trajectory()`
is evaluator-private identity, while `public_transcript()` returns the
containment transcript. It may include actor inputs and should be handled
accordingly. The evaluator can call `env.outcomes()` after termination for a
detached diagnostics mapping; those reads are not transcript calls and do not
change the private checkpoint. Terminal step results retain their normal shape
and terminal reward, but their `info` no longer includes outcome diagnostics.
Expose only the existing actor tool allowlist, never the Python episode,
replay/audit, scoring mask, pins or private checkpoint.


## Pinned dataset and runner integration

The supplied package at `/tmp/wildfire-qwen3-data/` has a `manifest.json` and
both `runtime/fixture/` and `runtime/es-ma-2021/` inputs and network files.
Its manifest matches `data/hub_runtime_v1/release.json`, and
`python -m eval.hub_runtime --data-dir /tmp/wildfire-qwen3-data --verify-only`
validates the package inventory, engine and active catalog pins. Verify the
manifest bytes against the release lock as a separate step: `verify_package()`
checks the package's internal consistency but does not authenticate a manifest
against that lock. Manifest file SHA-256 entries hash file bytes; the runtime
entry's `inputs_sha256` and `network_sha256` are the canonical document pins
accepted by the environment. Do not interchange them.

The fixture runtime constructs with the containment adapter. The `es-ma-2021`
input has 27 unresolved land cells; the current containment adapter rejects it
at construction, including when `allow_unapproved_data=True`. Report these
cases as blocked or unsupported and unscored. Do not remove cells, weaken the
unknown-land guard, substitute a fixture, or change the input or source pins to
make them pass. The supplied manifest has neither an integrated fixed mask nor
integrated scenario/implementation pins; it is not by itself an integrated
scoring release.

A trusted runner can reuse the existing package loader to obtain validated
runtime documents while continuing to enforce the published dataset lock. For
example, with `release` an independently approved integrated configuration and
`case_id` chosen from that configuration:

```python
import hashlib
import json
from pathlib import Path
from eval.hub_runtime import make_environment
from wildfire_containment_score_v1 import REWARD_VERSION, mask_sha256
from wildfire_containment_score_v1.integrated import (
    IntegratedContainmentEnv, verify_integrated_outcome,
)

root = Path('/tmp/wildfire-qwen3-data')
lock = json.loads(Path('data/hub_runtime_v1/release.json').read_text())
if hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest() != lock['manifest_sha256']:
    raise ValueError('Dataset manifest differs from the trusted release lock')
base, entry = make_environment(root, release.runtime_id,
                               allow_unapproved_data=release.allow_unapproved_data)
expected = release.case_identities[case_id]  # independently pinned before this run
if (case_id not in entry['scenario_ids'] or expected['task_id'] != case_id
        or expected['inputs_sha256'] != entry['inputs_sha256']
        or expected['network_sha256'] != entry['network_sha256']
        or expected['scoring_mask_sha256'] != mask_sha256(release.scoring_mask)
        or REWARD_VERSION != 'wildfire-extended-incident-day-outcomes-v1'):
    raise ValueError('Integrated release and runtime pins disagree')
env = IntegratedContainmentEnv(base.inputs, base.network.document,
    expected_inputs_sha256=entry['inputs_sha256'],
    expected_network_sha256=entry['network_sha256'],
    allow_unapproved_data=release.allow_unapproved_data,
    containment_config=release.containment_config,
    pressure_scale=release.pressure_scale, scoring_mask=release.scoring_mask)
observation, info = env.reset(case_id, seed=expected['seed'])
actual = env.trajectory()
if any(type(actual.get(key)) is not type(value) or actual.get(key) != value
       for key, value in expected.items()):
    raise ValueError('Episode identity differs from the trusted case pins')
if actual['configuration_sha256'] != release.configuration_sha256:
    raise ValueError('Containment physics differs from the trusted configuration')
# Drive the policy and keep a separate infrastructure timeout flag.
# After it stops, regardless of any later in-flight terminal result:
result = verify_integrated_outcome(env, expected_identity=expected,
                        scoring_mask=release.scoring_mask,
                        expected_configuration_sha256=release.configuration_sha256,
                        infrastructure_timeout=timed_out)
# Accept result['score'] only if result['status'] == 'full_day'
# and result['verified'] is True. Record other statuses without a day score.
```

The published fixture's active scenarios differ from the synthetic development
case in the focused tests; each active scenario needs its own separately pinned
scenario hash and seed. The evaluator must also pin the exact configuration,
mask, reward and integrated implementation after code review. The integrated
implementation identity composes the existing core and containment source
digests with the scoring source digests; it is a **new** identity, not a
replacement for the historical core implementation pin or containment version.
The containment source digest covers its Python package files, so adding or
editing files in that package changes the old pin. The scoring integration lives
in its separate package and does not modify those files. Do not update any
existing lock to accommodate integrated code changes.

`eval.qualify_extended_day_v1.qualify` currently calls `env.reset(case_id)` and
`env.verify_episode()` and validates the resulting reward version and terminal
flags. It does **not** call `verify_integrated_outcome` with separately trusted pins, so a
factory alone cannot supply the full scoring gate. To integrate it, preserve a
trusted per-case identity and mask alongside each environment, compare the
identity immediately after reset, and replace or supplement its terminal
verification with `verify_integrated_outcome` using those pins. Preserve the existing
post-action and post-verification wall-budget checks: a timeout is unscored even
if the in-flight action subsequently terminated. Keep censoring, abandonment,
truncation, setup rejection and failed verification distinct in private runner
results as needed; only verified `full_day` is eligible for the full-day score.
Do not write private audit data or exception details to the public report.

The qualification runner's current public facade exposes `propose_hold` but its
policy loop commits only `step` actions; it does not dispatch `authorize_hold`.
A containment policy that uses holds therefore needs an explicit allowlisted
host dispatch for that operation and an appropriately versioned runner/test.
The old runtime matrix also constructs core `IncidentDayEnv` instances directly
for its continuation and query-independence checks. Its prior validation results
cannot be reused as evidence for this wrapper; a new matrix integration must
construct and restore `IntegratedContainmentEnv` with the same independent pins.


## Weather and containment with the fixed mask

`wildfire_containment_score_v1.integrated_weather.IntegratedWeatherContainmentEnv`
is a separate opt-in combined host. It uses the weather/containment lifecycle
from [containment-weather-v1.md](containment-weather-v1.md), the same fixed-mask
counter and audit recomputation described here, and a distinct interface and
checkpoint identity. Add `envs/wildfire_containment_weather_v1/src` and the
repository root to `PYTHONPATH` as well as the core, containment and score
package source paths; the weather host currently depends on the repository's
`eval` weather and catalog modules. The runner must supply an independently
pinned compiled weather catalog, its input/network pins, and the separate
integrated mask and implementation pins:

```python
from wildfire_containment_score_v1.integrated_weather import (
    IntegratedWeatherContainmentEnv, verify_weather_outcome,
)

env = IntegratedWeatherContainmentEnv(
    release.inputs, release.network,
    expected_inputs_sha256=release.inputs_sha256,
    expected_network_sha256=release.network_sha256,
    allow_unapproved_data=release.allow_unapproved_data,
    containment_config=release.containment_config,
    pressure_scale=release.pressure_scale,
    scoring_mask=release.scoring_mask,
    weather_catalog=release.weather_catalog,
    expected_catalog_sha256=release.catalog_sha256,
)
observation, info = env.reset(case_id, seed=release.case_identity[case_id]['seed'])
expected = release.case_identity[case_id]  # the eight independently pinned fields
actual = env.trajectory()  # trusted host only; includes additional configuration/source pins
if any(type(actual.get(key)) is not type(value) or actual.get(key) != value
       for key, value in expected.items()):
    raise ValueError('Integrated case identity differs')
# Drive step/advance_time and host-dispatched containment hold operations.
# On terminal, truncation, or timeout, use the separate weather gate:
result = verify_weather_outcome(
    env, expected_identity=expected, scoring_mask=release.scoring_mask,
    expected_configuration_sha256=release.configuration_sha256,
    expected_catalog_sha256=release.catalog_sha256,
    expected_weather_source_sha256=release.weather_source_sha256,
    expected_containment_source_sha256=release.containment_source_sha256,
    expected_core_source_sha256=release.core_source_sha256,
    infrastructure_timeout=timed_out,
)
# Only a verified full_day result supplies a full-day score.
```

The standard eight-field `expected_identity` uses interface version
`wildfire-containment-weather-integrated-scoring-v2`; its
`implementation_sha256` binds the combined source identity and the separately
pinned containment physics hash, without replacing old core or containment pins. The
additional `trajectory()` fields include `configuration_sha256`,
`source_implementation_sha256`, `weather_catalog_sha256`,
`weather_source_sha256`, `containment_source_sha256` and `core_source_sha256`.
`verify_weather_outcome` compares all four and the configuration pin separately
to trusted pins both before and after the standard scoring gate. Use it rather
than the generic `verify_outcome` for this host. `verify_episode()` retains the requested
runner-facing replay and independent-accounting fields but does not by itself
check independently supplied pins or establish score eligibility.

The base weather host's `replay_transcript()` intentionally requires its exact
class, so the combined host performs its own replay using a fresh exact combined
instance and compares the complete joint checkpoint, including weather profile,
moisture, fire, communications, containment and minute audit. The inherited
weather `private_replay_pins()` and `replay_transcript()` methods are not the
combined replay interface; use the combined verifier and `public_transcript()`.
The verifier independently recomputes the fixed-mask outcome from that audit. Its checkpoint wraps the
weather and integrated checkpoints with a distinct version and catalog/source
pins. The catalog and all source pins must come from the evaluator, never from
the run being graded. The same timeout, censor, abandonment and truncation
rules apply.

This implementation does not resolve the source-map guard: the combined host
also rejects the supplied `es-ma-2021` inputs with unresolved land, even though
the pinned files are available. The weather catalog compiler uses separately
versioned compiled case IDs; the manifest's legacy active IDs alone are not a
weather catalog or a set of combined release pins. Do not claim a 12-case
qualification or replace unavailable map cases with fixture cases. Weather is
an uncalibrated proxy, and compatibility with process-wide weather/bridge hooks,
unknown-land censoring, typed transport and the full runtime matrix remains
unestablished.

## Accounting, replay and checkpoints

At reset, the existing production outcome counter is initialized with the fixed
mask and inherited footprint. Each minute updates it from newly burned cells;
population comes from the operational state. `verify_extended_episode()`
reconstructs a fresh integrated adapter using the same pinned inputs and
configuration, replays each recorded public call and compares its public result,
then compares the entire private containment checkpoint, including the audit,
fire/RNG, operations and counter. `verify_episode()` separately recomputes
from the replayed minute audit and checks all outcome fields and reward. The
external gate performs the same independent comparison after checking trusted
identity and verifies that exposed state is unchanged. Neither the counter nor
the recomputation establishes audit authenticity or physical calibration.

`checkpoint()` adds the fixed mask, identity and integrity digest around the
existing containment checkpoint; `restore()` requires the same independently
supplied mask and implementation. Checkpoints and transcript digests protect
against accidental changes, not malicious replacement. Keep these artifacts
private, and discard or restore after any interrupted state-changing call. The
base containment adapter's `replay_transcript()` and `private_replay_pins()`
contract is for its exact base class; use the integrated verifier for this
version. The non-weather host does not run the weather lifecycle; use the
combined host for weather-coupled episodes.

## Validation scope

The focused tests run a synthetic full day with an authorized hold, compare a
checkpoint continuation, replay and independently account for the score. They
also cover invalid abandonment, host truncation, timeout, mask/identity and
audit corruption, and censor precedence. A combined-host full-day test exercises
a weather change, an authorized hold, checkpoint continuation and fresh replay;
its source-change check fails closed at an action boundary. The censor test injects boundary
contact into a containment fire step to exercise that branch deterministically;
it is not an observed physical spread to the boundary. Unknown-land censoring
is still rejected by the underlying containment adapter. The complete active
case/policy matrix in [runtime-validation-plan.md](runtime-validation-plan.md)
is required before release and has not been established for this integration.
The dataset is available at the path above, but the source-map cases cannot run
until the unresolved-land limitation is implemented and reviewed.
