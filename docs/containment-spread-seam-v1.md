# Experimental containment spread seam

`eval/containment_spread_seam_v1.py` separates candidate generation, one shared
containment tick and ignition application. It leaves the released fire step and
its results unchanged. This is an experimental evaluator-side seam, not yet a
vegetation/built-up model, a source-map adapter or a calibrated fire predictor.

The caller supplies a whole-stencil support guard and must independently pin its
implementation. Unsupported states censor the seam. Mixed-water factors come
from the validated backend and mixed checkpoints use that backend's snapshot and
restore contract. Assignments are checked before mutation and cleared after a
step. Checkpoints are only available at supported step boundaries.

Focused tests compare single-compartment and mixed-water behavior with the
existing step, including containment decisions, RNG ordering, consecutive
assignments, censoring and checkpoint replay. They do not establish mixed
urban-fire realism or test a full source incident.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_containment_spread_seam_v1.py tests/test_expanded_estimated_fuel_v1.py \
  -q -p no:cacheprovider
```
