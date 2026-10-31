# Fresh mixed-fire shared incident prototype

`wildfire_fresh_component_shared_v1` composes the fresh two-component
fire/weather owner with synthetic ground, aircraft and anchored population on
one clock. Its separate private scorer uses an 80/20 population/burned-area
formula and independently replays completed episodes. Its supported-area mask
and single-ground-candidate scope are not a full-domain benchmark reward.
Private checkpoints, source inputs, paths and evaluator state are not actor
observations; the Python facade is not a sandbox.

A bounded source-backed check constructed the host and scorer, submitted a
population order and reproduced the checkpoint on a fresh owner. The verifier
returned `unfinished`, as expected. Identities generated in that check are
review candidates, not independent approvals. The check did not establish a
full-day score, aircraft drop, crew arrival or line holding in this host. The
other eleven scenarios lack demonstrated ground access. Synthetic tests and
assumptions do not establish operational safety or physical calibration.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs/wildfire_containment_score_v1/src:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_fresh_two_component_operations_v1.py tests/test_fresh_component_shared_v1.py \
  tests/test_fresh_component_shared_scored_v1.py -q -p no:cacheprovider
```
