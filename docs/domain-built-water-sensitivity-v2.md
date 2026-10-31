# Built-up and watercourse sensitivity

The private domain-wide built-water profile identifies cells with source fuel,
corroborated inland terrain, vegetation and built-up WorldCover samples, and a
mapped watercourse. It does not select cells using scenario outcomes. Classified
samples are not surveyed building footprints, and watercourse crossings, fuel,
inert shares and component transfer are explicit uncalibrated assumptions.
Unsupported cells stay unknown and can censor a run.

The v1 compiler, policy, adapter and weather owner are retained to reproduce a
private unscored source check. That run completed warmup and censored at day
minute 1,380; it did not evaluate population, resources or reward. V2 checks
inherited compiler and source hashes before and after reconstruction and requires
fuel choices to match the declared sensitivity alternatives. V2 input requires
its own owner, scenario choices and fresh run; it cannot reuse v1 checkpoints.

Private profiles, source data, case outputs and checkpoints must remain outside
the repository. Neither version is a calibrated urban-fire model or a complete
incident environment.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_domain_built_water_profile_v1.py tests/test_domain_built_water_profile_v2.py \
  tests/test_domain_built_water_policy_v1.py tests/test_source_built_water_adapter_v1.py \
  tests/test_source_built_water_adapter_v2.py tests/test_fresh_built_water_weather_v1.py \
  -q -p no:cacheprovider
```
