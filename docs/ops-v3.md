# Opt-in Ops v3 observations

Ops v3 is a separate addon exposing `wildfire_ops_v3.IncidentDayEnvV3` and
`observation_identity()`. It keeps the existing physical engine, scenario IDs,
input/network checks and reward formula, while correcting delivered population
knowledge and public action-validation feedback. A different observation contract
can change policy decisions and outcomes; use fresh run artifacts.

Install from the repository root in the same Python 3.12+ environment:

```bash
python -m pip install -e envs/wildfire_response/core
python -m pip install -e envs/wildfire_ops_v3
python -m pip install -e 'envs/wildfire_response/openenv[dev]'
```

## Explicit selection

The default remains `observation_version='v2'` and does not import the addon.
Both the typed OpenEnv adapter and packaged-runtime factory accept an explicit
v3 selection. Unsupported values fail; requesting v3 without the addon gives an
installation error instead of falling back to v2.

```python
from eval.hub_runtime import make_environment, run_episode

environment, entry = make_environment('/path/to/package', runtime='es-ma-2021',
    allow_unapproved_data=True, observation_version='v3')
# When ready to execute a fresh bounded run:
# result = run_episode(environment, entry, policy='dispatcher', wall_seconds=300)
```

For the typed adapter, pass the same `observation_version='v3'` keyword to
`wildfire_openenv.incident_day.IncidentDayEnvironment` alongside its existing
input/network paths and expected hashes. Split discovery, explicit task selection,
archival opt-in and the default development case retain their existing rules.

The loopback server selects the contract once at startup. A fixture example is:

```bash
python -m eval.incident_day_server --fixture --observation-version v3
```

For source inputs, use its existing `--inputs`, `--network`, `--inputs-sha256`,
`--network-sha256` and `--allow-unapproved-data` flags with the explicit version.
The client cannot switch observation contracts by changing a reset request.
A bounded packaged run is:

```bash
python -m eval.hub_runtime --data-dir /path/to/package --runtime es-ma-2021 \
  --allow-unapproved-data --observation-version v3 --policy dispatcher \
  --wall-seconds 300 --output /tmp/fresh-ops-v3-result.json
```

## Identity and compatibility

Fresh v3 run reports include `observation_version: v3` and `observation_identity`,
the addon's digest of its observation sources and the underlying core identity.
The digest is checked again before accepting a completed run. V3 evaluator
`/tasks` and `/state` responses also identify the selected contract; actor
observations identify their public `interface_version`. Trusted implementation
metadata is not added to the actor observation or info allowlists.

V2 defaults retain their existing response/report shapes. Package verification
still checks the original core and lifecycle pins and inventory; selecting v3
does not rewrite or relax a package manifest. `--verify-only` verifies the data
package, not an executed v3 episode. Existing frozen core digests, old artifacts
and their reported contracts remain unchanged.

V3 population positions may be unknown while movement is unresolved; field
provenance reflects delivered evidence rather than future or hidden state.
Validation feedback names the public field and the next appropriate lookup.
These corrections do not establish physical calibration or held-out independence.

V3 checkpoints have their own nested format. The evaluation runner supports
explicit v3 selection, pins the observation identity, forks v3 for continuation
checks, and restores the environment, policy and observer together:

```bash
python -m eval.evaluate_packaged_runtime --data-dir /path/to/package \
  --output /tmp/fresh-ops-v3-matrix --observation-version v3 --wall-seconds 1800
# Resume with exactly the same version, code, inputs and output directory:
python -m eval.evaluate_packaged_runtime --data-dir /path/to/package \
  --output /tmp/fresh-ops-v3-matrix --observation-version v3 --wall-seconds 1800 --resume
```

The older source/development matrix drivers remain v2-only. Never pass a v3
checkpoint to v2 or resume frozen v2 results as v3. Keep new results separate;
corrected delivered knowledge can legitimately change subsequent policy actions.
