# Experimental two-component fire

`eval/two_component_fire_v1.py` is a separately versioned synthetic prototype
that tracks vegetation and built-up fuel, heat, age and wetness independently
within a cell. It reuses the experimental shared spread seam and makes one
physical-edge containment decision. It does not alter the released simulator,
map inputs, scenarios, rewards or previous results.

Component fractions and the four transfer factors are explicit exercise
assumptions, not measured burnability or calibrated fire behavior. An absent
component must be explicitly known absent; unrepresented components remain
unknown and censor affected fire or assignments. A checkpoint restore requires
an independently retained digest. The model is not a validated urban-fire
predictor and is not yet connected to the source map, operations host, reward,
or 12-case evaluation.

Focused synthetic tests cover one-component parity, cross-component transfer,
zero-transfer RNG and containment pressure, unknown-cell handling, assignments,
and seeded checkpoint replay:

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_two_component_fire_v1.py tests/test_containment_spread_seam_v1.py \
  -q -p no:cacheprovider
```
