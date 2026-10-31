# Estimated-fuel frontier diagnostics

The two private follow-up drivers in `eval/estimated_fuel_frontier_followup_v1.py`
and `eval/estimated_fuel_frontier_followup_v2.py` investigate fire-and-weather
runs that previously reached unsupported fuel. They require independently pinned
source inputs, the prior private checkpoints, and an explicit opt-in. They do
not alter the released maps, original scenarios, or their results.

Fuel classes are low-confidence synthetic hypotheses generated from neighboring
source samples. A water conflict, ambiguous surface, unsupported terrain or
unresolved fuel remains censored; the tools do not turn unknown cells into
firebreaks. Different hypothesis sets have different identities, so a changed
input requires a new simulation rather than reusing the old physical state.
Their reports and checkpoints contain private scenario and geographic details and
must remain in a private directory outside the repository. Neither driver models
operations, population or reward, and a complete fire-only run does not qualify an
incident-day episode.

Use each driver's `--help` for required input paths, pins, and time bounds.
Targeted tests are:

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_extended_inputs_v1:envs/wildfire_containment_v1/src:envs:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_estimated_fuel_frontier_followup_v1.py tests/test_expanded_estimated_fuel_v1.py \
  -q -p no:cacheprovider
```

See [estimated-fuel fire experiment v1](expanded-estimated-fuel-v1.md) for the
underlying source and uncertainty assumptions.
