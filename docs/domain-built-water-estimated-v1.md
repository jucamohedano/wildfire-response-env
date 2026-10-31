# Estimated mixed-cell fire/weather diagnostic

This private, separately versioned experiment combines source-backed and
neighbor-estimated fuel assumptions with classified vegetation, built-up and
bare-ground shares in the two-component fire/weather model. It starts fresh
from a pinned scenario seed; it does not split prior fire checkpoints. Values
for fuel, inert shares, water crossing and component transfer are synthetic
sensitivity assumptions, not measurements or calibration. Unselected or
unsupported cells remain unknown and censor the run.

A bounded private v1 run of one previously censored case reached 1,440 day
minutes after 60 warmup minutes. It was unscored and did not simulate operations,
population or evacuation. It cannot be combined with older single-terrain runs
as if they shared an engine identity. Prior versions and results are retained.

**Known limitation:** the v1 fixed policy omits low-water cells with retained
source fuel. It is kept only to reproduce that private result and must not be
used as a complete catalog policy. A separately versioned corrected policy and
fresh batch are required. Private source profiles, reports, assumptions and
checkpoints must stay outside the repository.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_domain_built_water_estimated_v1.py \
  tests/test_fresh_built_water_estimated_weather_v1.py -q -p no:cacheprovider
```
