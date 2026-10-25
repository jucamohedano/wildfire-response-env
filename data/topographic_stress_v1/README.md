# Topographic stress catalog v1

This is a separate Python API and CPU evaluation mode. It reuses the complete
height surface and existing fire, fuel, weather-context, communication, resource
and population-protection mechanics without rewriting the six development cases
or their replay pins. The existing OpenEnv topographic server still exposes those
six cases; hosting the stress catalog is a separate adapter step.

`catalog_lock.json` freezes120 configuration groups before policy evaluation:
80 training,20 validation and20 test. Each has two seed/wording variants, making
240 episodes. All six families appear in every split. Families include uncertain
smoke, false alarms, duplicate bursts, conflicting locations, wind changes and
multiple fires; resource holds, request delays, readback mismatches and failed or
slow population broadcasts combine with them. Ignitions use supported interior
fuel cells. All operations are synthetic, on one source-informed landscape.

Opaque task IDs do not name incident truth. Both variants remain in the same
group; configurations and seeds are disjoint across splits. These are **not**
independent real incidents, held-out regions or established firefighting doctrine.
Initial night conditions are retained; aircraft readiness never bypasses daylight
or wind safety gates. The synthetic wind still differs from measured coastal
weather context. Native terrain metadata and physical calibration limitations in
`../malaga_2021/SIMULATION_ACCEPTANCE.md` still apply.

## First smoke batch

The registered smoke selection is12 training groups, variant0 only: the first
six groups plus groups18–23. This includes each family with ordinary and delayed,
resource-constrained communications. Selection is fixed before seeing rewards;
failed and censored outcomes are kept. No validation/test policy outcomes are
consumed. Static validation of their schemas/split membership is not policy eval.

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_topographic_stress \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --catalog-sha256 a38970528b7119d598bee4a51038f01e9e4f6ec7948a8c7e55b3ec1bf2743444 \
  --slope-enabled true --repeat --policies passive evacuate shelter \
  --output .runtime/topographic-stress-new
```

Use a fresh destination. The CLI defaults to passive and evacuation baselines;
the command above also tests shelter. The optional blanket baseline tests
indiscriminate warnings. `--full` evaluates both variants of all groups in the
chosen split. Validation/test additionally require `--consume-held-out`; do not
tune the catalog, policy or reward using test outcomes. Begin with the small CPU
batch; a full sweep and model training are not automatic.

Each primary episode receives independent public-trace/reward replay through
`verify_episode`. Its canonical compressed export must equal that verified trace.
`--repeat` performs another rollout and compares public and physical-state hashes;
it does not redundantly re-verify the identical repeated trace. Failures leave a
partial manifest marked failed, not a falsely complete report.

For an external Python client, construct `wildfire_core.stress.StressEnv` with the
prepared bundle, domain, compiled private catalog and their three caller pins.
Use `reset(task_id)` / `step(action)`. To reproduce rewards from a saved public
trajectory, use `verify_stress_trajectory(trace, prepared, domain, catalog)`.
Existing observation/action and safety-gated reward contracts are inherited.

## First recorded result

The source-pinned training smoke run at revision857f740 completed36 primary
rollouts and36 exact repetitions, with no invalid actions or boundary censoring.
All primary rewards independently replayed, and canonical exports matched those
verified traces. Runtime was199.823s on one CPU worker, with83,382,272 bytes peak
process RSS. The full regression suites passed282 core methods and201 root
methods;9 and16 optional adapter tests respectively were skipped in these base
environments. Prior adapter evidence is not a new stress-server validation.

Passive mean reward was0.5, with200 abstract exposed people across the12 cases.
Evacuation and shelter baselines each scored1.0 with no exposure. This initial
batch therefore does not establish a difficult benchmark for those scripted
policies. All validation/test policy outcomes remain unconsumed.

Training-only exploratory reward controls are retained alongside the main run.
Two false alarms scored1.0 for the response baseline versus0.75 for blanket
warnings. With no fleet actions, immediate population protection scored0.5 and
avoided exposure; waiting until model minute110 scored0 and exposed200 people.
Neither resolves the fire or performs evidence triage, so the timely case does
not earn full credit just for communicating.

One initial control expected warning delay40 to lower reward while active fleet
response continued. It did not: both policies scored1.0 with no exposure. That
strict ordering expectation was inappropriate when the realized outcome was safe;
we did not invent a delay penalty. A population-only delay80 case scored0 because
evacuation remained unresolved at the horizon, not because anyone was recorded
exposed. These intermediate controls and their full action traces are kept, not
hidden as successful tests. They are synthetic diagnostics, not optimal-policy
labels or evidence of a universal safe/unsafe warning delay. Reproduce any stored
diagnostic outcome with `verify_stress_trajectory` using its recorded actions.

The36 episodes contain1,302 observations. Every compact observation reconstructed
its canonical counterpart exactly. Compressed canonical trajectories occupy
16,874,446 bytes; compact transitions463,337 bytes plus one24,842-byte shared
landscape. Both forms are retained. `provenance.json` records artifact/code hashes,
source context, measured results, exploratory controls and unresolved gates.

See `../../DATA_STORAGE.md` for artifact layout and separation rules. Catalog
configuration, verification and private scoring evidence must not enter actor
observations or retrieval banks. The frozen split is not yet a training release;
broader calibration, package/adapter validation and redistribution rights remain.
