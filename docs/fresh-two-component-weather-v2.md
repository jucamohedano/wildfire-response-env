# Fresh mixed-component fire and weather

`eval/fresh_two_component_weather_v2.py` starts a separate, unscored synthetic
fire/weather experiment from a pinned expanded draft's seed, staged source
components and explicit ignition assumptions. It builds its own warmup and day
history; it does not split a legacy single-state checkpoint into invented
component histories. Unknown support remains censored and private source/audit
state is not an actor observation.

The v3 component model and v2 owner support an explicit warmup plus up to 1,440
day minutes. A bounded private smoke used the 84×59 source grid and reached
three of 60 warmup steps, then reproduced one checkpoint continuation. It did
not reach the incident horizon or evaluate a policy. Its assumption and input
fingerprints were assembled in the same run, not independently approved.

The earlier v1 owner is retained for its private smoke, but its initial wind
rounding can make a pre-first-step checkpoint with a non-rounded wind fail to
restore. V2 retains the exact validated initial vector and tests both warmup
and day-start checkpoints. Existing releases and results are unchanged.

This is not a full operations host, a reward, a realistic urban-fire model or
a calibrated source reconstruction. Components, crossing and transfer
parameters are synthetic assumptions; an independently reviewed source input
and full-day replay remain necessary.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_two_component_fire_v3.py tests/test_fresh_two_component_weather_v2.py \
  tests/test_fresh_two_component_weather_v1.py -q -p no:cacheprovider
```
