# Private estimated-fuel fire experiment v1

This opt-in, unscored diagnostic is separate from the unchanged source-only and
v4 mixed contracts. It uses the v4 geographic reconstruction and independent
strict/v2 source gate, then accepts only the separately pinned, fully
source-reconstructed private mixed-fuel hypothesis. The v1 input contract
continues to contain only source fuel; the versioned backend keeps estimated
classes and separate source and estimated masks in private input state. Missing
fuel, ambiguous coast and water areas remain censored unless admitted by the
specific pinned exercise evidence. Confirmed sea remains nonburnable. The road
candidate does not establish access.

The selected low, nominal or high fuel assumption is part of the input and
checkpoint identity. The classes and fractional initial fuel are uncalibrated
hypotheses based on neighboring source samples, not field measurements. The
low/high labels order initial-fuel assumptions only; they do not order spread or
fire outcomes. Watercourse crossings use the separately declared v4 product
proxy, including diagonal corner cells. This is an exercise assumption, not
measured water permeability or access. The backend checks the entire burning
stencil before and after every step, supports exact private checkpoint replay,
and suppresses outcomes on censor. The weather driver uses the existing pinned
wind, temperature, humidity, rain and moisture proxy; resources, population,
routes and non-weather operational events are unmodeled. No rewards or scores
are produced.

Call `prepare_estimated_inputs` with the same explicit v4 reconstruction
arguments, the private pinned hypothesis, a `fuel_scenario` of `low`, `nominal`
or `high`, and `opt_in_estimated_fuel=True`. Construct
`EstimatedMixedFireV1` with a seed, containment configuration, wind, pressure
scale and the same opt-in. `EstimatedWeatherFireV1` additionally takes the pinned
drafts and an explicit weather opt-in. These APIs are trusted evaluator-side
interfaces; the private inputs, scenario catalog, masks, checkpoints and
reports must not be exported to actors or training data. Run the package bridge
before public package or adapter imports, as in
[the input adapter instructions](extended-inputs-adapter-v1.md).

`run_estimated_sensitivity_v1` selects two previously censored private draft
cases using their saved frontiers, keeps v4's nominal surface and crossing
assumptions fixed, and runs both fuel alternatives with each draft's original
seed. Use `python -m wildfire_expanded_incident_v1.run_estimated_sensitivity_v1
--help` for its explicit source pins and paths. The output must be a fresh
file in an owner-only private directory outside every Git worktree. It contains
case-specific information and checkpoints and must stay private. The bounded
run does not start a step after its deadline; validation, a step already in
progress and final I/O may finish later. This short paired runner writes its private checkpoints only in its final report;
an interrupt before that write loses its progress. The report records partial or
censored runs as such and is not an operational scenario outcome.

The fuel hypothesis is validated in the GIS environment using the separate
[source-reconstruction instructions](mixed-estimated-fuel-v1.md). The focused
synthetic test can be run with `python -m pytest
 tests/test_expanded_estimated_fuel_v1.py -q -p no:cacheprovider`; see
[the mixed backend runtime notes](expanded-incident-mixed-v2.md) for the
Shapely/pytest environment differences. The experiment does not validate
physical calibration, source rights, access, or operational readiness.
