# Experimental two-component fire v2

`eval/two_component_fire_v2.py` is a separately versioned synthetic prototype.
It tracks vegetation and built-up material independently, with an explicitly
modeled synthetic inert share or unknown share. Per-cell crossing and transfer
factors are caller-supplied assumptions. Unknown or omitted areas do not become
inert automatically; unsupported fire and assignments are censored or rejected.
One shared physical containment tick applies to both components.

The version supports up to 16,384 cells and has been tested on a synthetic 84×59
grid. Focused tests cover one-component parity, explicit inert versus unknown,
crossing factors, boundary censoring, resource assignments and seeded checkpoint
replay. Fractions, transfer and crossing factors are not measured burnability or
calibrated urban-fire behavior.

This prototype uses a synthetic fire fixture. It is not yet connected to the
source fire/weather backend, the operations host or rewards, and no source case
or full evaluation has been run with it. V1 and all prior results remain unchanged.

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_containment_v1/src:envs/wildfire_extended_inputs_v1:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_two_component_fire_v2.py tests/test_two_component_fire_v1.py \
  tests/test_containment_spread_seam_v1.py tests/test_source_two_component_adapter_v1.py \
  -q -p no:cacheprovider
```
