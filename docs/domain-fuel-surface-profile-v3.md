# Domain-wide synthetic fuel and surface profile

`eval/domain_fuel_surface_profile_v3.py` applies one rule to the entire expanded
500 m grid, independently of scenario outcomes. It retains mapped land, sea,
and v4 mixed cells; it also identifies inland cells where source-only neighboring
fuel samples and terrain, land-cover and mapped-water screens support an explicit
synthetic estimate. Water evidence and source dates are recorded in the private
profile. The method and its source-reconstruction pins are stored in the profile
JSON; it is not a historical measurement, a calibrated fire map, or an approved
operational-access layer.

Weak votes are uncertain and tied votes retain separate alternatives. The
separate `eval/domain_profile_weather_v2.py` experiment chooses a reproducible
synthetic class and factor; the choice is an exercise assumption, not evidence of
actual fuel. Unsupported or ambiguous surface and terrain remain unknown, and
runs that reach them are censored. No unknown cell becomes an artificial
firebreak. The fixed profile, geographic inputs, per-case outputs and checkpoints
remain private outside the repository; the source profile is independently
reconstructed before use.

`eval/run_domain_profile_weather_v4.py` runs the fire-and-weather layer only.
It does not model resource operations or population and does not produce reward.
See each script's `--help` for its explicit input paths, pins and opt-in flags.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_extended_inputs_v1:envs/wildfire_containment_v1/src:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_domain_fuel_surface_profile_v3.py tests/test_domain_profile_weather_v2.py \
  -q -p no:cacheprovider
```
