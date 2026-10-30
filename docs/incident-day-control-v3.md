# Incident-day control interface v3 (experimental)

This package contains an additive, single-controller interface and an opt-in
population-knowledge engine. The new engine uses the v2 fire physics, resources
and outcome rules but has a separately pinned observation/validation identity.
The v2 engine, existing scenario IDs, v2 implementation digest and archived runs
remain unchanged. This is **not** yet the hosted AgentEnv/OpenEnv day interface
or an approved training benchmark.

Install the existing core and the new package from a checkout, then create a
dedicated environment instance for each controller:

```sh
python -m pip install -e envs/wildfire_response/core -e envs/wildfire_control_v3
python - <<'PY'
from wildfire_core.incident_day import fixture_inputs
from wildfire_control_v3.control import create_population_control

inputs, network = fixture_inputs()
control = create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                    expected_network_sha256=network['sha256'])
observation, info = control.reset('incident-day-egress-v1-000')
print(control.execute('get_briefing', {})['page'])
result = control.execute('advance', {'max_minutes': 5})
print(result['observation']['minute'], result['info']['wake_reason'])
PY
```

`tool_definitions_v3()` returns the named public tools. The additive
`get_briefing` page is a bounded projection; it is **not** the complete world
or complete delivered record. `get_situation` still provides the full v2
public observation. `get_report_page` supplies explicit cursor, count and
continuation fields; `get_reports` remains available for existing queries.
Report content and role are untrusted data. An observation is what has been
delivered, not a guarantee of current conditions or knowledge of future events.

The reset metadata and action results include the briefing when available.
`create_population_control(...)` opts into `PopulationEngineV3`, which captures
population snapshots at the time of an event and updates its known state only
when those reports are delivered. Its observation, evacuation routing and order
validation use the same last-reported position. A conflicting or unknown
position returns `position_unknown` and no route options; a route based on a
delivered blocked-position report is explicitly labelled as last reported and
may be declined later if the group has moved. This is not live tracking or a
guarantee of current physical position.

For archived behavior, `ControlCenterV3(IncidentDayEnv(...))` still wraps the
unchanged v2 engine. Its separate briefing overlay can identify delivered
arrival evidence, but its underlying v2 router/validator can still use the
older population view. Do not mistake that legacy mode for the opt-in fix.

`check_action` returns its selected engine's authoritative validity plus v3 diagnostic codes,
affected fields and suggested lookups where known. A commit remains atomic and
consumes the same simulated time as v2. An exception after dispatch returns an
indeterminate execution status: inspect the public situation before retrying.
The wrapper blocks the fourth consecutive identical failed draft in an
unchanged situation; it neither chooses an action nor advances time. Valid
orders and reads remain available. An external driver still needs its own
bounded call/time budget; a model can ignore the guard and keep calling it.

`advance` is a versioned alias for the engine's `advance_time`: it defaults to a
60-minute maximum, accepts a shorter limit, and stops earlier when the engine
delivers actionable information or reaches an operational boundary. Commands
and the fire continue to use the existing one-minute engine ticks. Returned
actions include the selected engine's full public observation plus a separately labelled
briefing, so no observation fields are replaced by a summary.

Terminal action results deliberately withhold the raw score until a trusted
driver calls `finalize(expected_identity=..., expected_interface_sha256=...,
infrastructure_timeout=...)`.
The caller must supply predeclared task, seed, engine, input, network and
scenario identity pins and the v3 interface code digest from its evaluation
configuration; never take its
expected pins from the run being evaluated. The gate invokes the selected engine's replay and
independent reward calculation, rejects mismatched identity, and distinguishes
completed days, invalid abandonment, censoring, incomplete runs and timeouts.
It does not prove field realism. The caller must report timeouts accurately.
`wildfire_control_v3.control.interface_digest()` and
`wildfire_control_v3.population_engine_v3.population_engine_identity()` can be
used when preparing and
recording an evaluation configuration before the run starts. Use a fresh
process and immutable source files for evaluation. The interface records source
fingerprints when its modules load, checks source against the loader's code,
and checks them around verification. The source/bytecode comparison is tested
on source-installed CPython 3.12; unsupported loaders fail closed. These are
consistency checks, not a security attestation of an untrusted process or a
substitute for immutable deployments.

`private_checkpoint()` and `restore_private_checkpoint(...)` are **trusted
driver methods**, not model tools. Their envelope contains the v2 checkpoint,
including hidden future events and evaluator state; store it privately. The
v3 envelope pins its own code and check-guard state separately from the v2
engine, so a resumed wrapper retains the guard. Do not include it in prompts,
public trajectories or datasets. Restore validates in a separate engine and,
on success, replaces `control.environment` with that engine. It currently
requires an environment without an active artifact directory; segmented
artifact continuation is not yet supported by this wrapper.

## Paired development checks

With the prepared dataset already downloaded and `pyarrow` installed, the
following runs a single paired real-map exercise on one CPU worker:

```sh
PYTHONPATH=envs/wildfire_control_v3/src:envs/wildfire_response/core/src:. \
python -m eval.check_control_v3_parity \
  --data-dir .runtime/wildfire-response-spain --runtime es-ma-2021 \
  --allow-unapproved-data --policy dispatcher --limit 1 --wall-seconds 240 \
  --output /tmp/new-control-parity.json
```

The reference and v3 wrapper **over the v2 engine** run the same policy and scenario on separate
engine instances. The check compares public observations, actions, timing and
replayed terminal reward; it does not establish physical calibration or compare
every hidden state. `subset_passed` is a successful selected-case smoke check,
not a passed suite. `incomplete` and `failed` exit nonzero. Budgets are checked
after atomic simulator and verifier calls and after staging the final report,
before its atomic rename. The rename itself cannot be preempted. Interrupted
work is never scored.
