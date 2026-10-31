# Private 12-case mixed-fire diagnostic

`eval/run_component_12case_v3.py` runs the twelve source drafts round-robin on
one CPU using the fixed synthetic mixed-component policy. It models fire and
weather only: it is unscored and does not evaluate resource or population
operations. Unsupported cells remain censored; a budget stop is incomplete.

The runner writes immutable authenticated progress generations and holds an
exclusive lock. It checks source identities before promoting new checkpoints
and keeps prior generations on failure. An independently retained key digest
is required; retain a trusted head digest as well to detect rollback to an
older valid generation. An authenticated checkpoint does not encrypt private
map, scenario or audit data, so keep the key and outputs in a private directory.

V3 can import a v2 progress snapshot only with its independently retained digest
and copies it to a separate output directory. The v2 runner is retained solely
to reproduce earlier private results: it lacks the v3 interruption, source-change
and concurrent-resume guarantees. Do not use v2 for further evaluation runs.
Use v3's `--help` for private paths, key, import, resume and time-budget options.
The recorded assumptions are uncalibrated exercises, not historical evidence.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:tests:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_run_component_12case_v3.py tests/test_domain_built_water_estimated_policy_v2.py \
  -q -p no:cacheprovider
```
