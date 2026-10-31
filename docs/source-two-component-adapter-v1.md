# Source-to-component staging adapter

`eval/source_two_component_adapter_v1.py` prepares evaluator-private synthetic
component inputs from the pinned map profiles and WorldCover sample counts. It
requires explicit per-cell fuel, transfer, crossing, component-absence and
inert-share assumptions. Classified samples are not surveyed building footprints
or measured fuel. Unsupported or unselected cells remain unknown; mapped sea is
only treated as synthetic inert with an explicit choice.

This is a staging adapter, not a runnable source episode. The v1 component model
cannot represent a third inert share or non-neutral per-cell crossing effects and
has a 4,096-cell limit, smaller than the 4,956-cell source grid. The previously
censored mixed built-up/vegetation frontier therefore remains unknown. A source
fire/weather owner, explicit boundary censoring and an independently reviewed
input contract are still needed. No source fire steps or scores were produced by
the staging check.

`eval/source_two_component_adapter_v2.py` stages the explicit assumptions in
the v2 component format, including per-cell crossing and inert shares. A
private 84×59 staging check selected only two cells; the previously censored
frontier remained unknown. It did not run fire or weather. A fresh-from-seed
source owner is still required; an old single-state checkpoint cannot be split
into component histories.

`eval/domain_component_policy_v2.py` defines a fixed, domain-wide synthetic
selection for source fuel and eligible documented estimates. It does not use
case frontiers or outcomes. Unique estimates use the documented nominal class;
tied alternatives use a reproducible lowest-class choice. The fuel, crossing,
inert-share and transfer values are uncalibrated assumptions, and unsupported
cells remain unknown. A private fresh-from-seed fire/weather check using this
policy censored at day minute 1,260; it was not a full operations evaluation.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_source_two_component_adapter_v1.py tests/test_source_two_component_adapter_v2.py \
  -q -p no:cacheprovider
```
