# Sierra Bermeja evaluation candidates

These are synthetic **development-evaluation candidates** on the existing
source-map terrain. They are not historical dispatch records, independent
fires, a finalized held-out test set or a public release. The underlying pack
uses estimated inputs under explicit opt-in. Future training configurations
must be grouped separately from these cases; geographic generalization needs
other incidents and landscapes.

| Variable | Candidate variation |
| --- | --- |
| Fire at handover | Different 25-, 49- and 81-cell initial fronts, plus modeled warm-up. A grid-cell footprint is coarse, not a measured perimeter. |
| Pre-handover period | 60–240 model minutes after an assumed initial front; this is not a measured ignition-to-call delay. |
| Wind | Direction, strength and changes during the day; vectors are relative model units, not measured m/s. |
| Temperature and humidity | Synthetic temperature and relative-humidity trajectories; the bounded lagged moisture proxy is an uncalibrated exercise assumption. |
| Rain | Dry periods and synthetic rain fronts; rain affects the same uncalibrated proxy, not measured fuel moisture. |
| Crews and engines | Different finite shift slots, an additional crew reserve in one case, delayed availability and reduced initial water in another. Slots represent units/teams, not individual firefighters. |
| Aircraft and daylight | Day, night and scheduled aircraft unavailability; the model's gates are not aviation authorization. |
| Access and coordination | Wind-driven changes, engine access restrictions and temporary centre unavailability. |
| People and destinations | Existing synthetic cohorts, including a case where an occupied reception centre later closes. |
| Reports | Scheduled synthetic calls, including conflicting locations and a duplicate, delivered with stated delays. |

The catalog contains twelve different decision families, including a delayed
large-fire handover, wind-driven access loss, a rain front, aircraft limits,
relocation, resource shortage and conflicting calls. All run within the same
24-hour incident-day contract, which requires an already-large fire at handover.

Candidate compilation checks terrain and fuel support, initial occupied-post
safety, warm-up censoring, a geometric ground approach and return using complete
private fire state, and a wind counterfactual. A geometric route does not prove
the agent sees it, the unit is ready, or a shift can complete the assignment.
These checks alone do not prove each later event is relevant, the 24-hour episode
finishes, or the simulated dynamics are calibrated. Complete
episode, policy and causal-coverage results must be reported separately. Weather
signals and effects are synthetic; the coastal station is context, not a
calibrated mountain fire-front measurement.

The generated candidate IDs and profiles are reproducible from the public
generator. They are suitable for development evaluation, not a secret or
independent held-out test. Never expose the private runtime catalog, generator
source, schedules, evaluator annotations or checkpoints as agent tools. A later
blind benchmark needs privately sampled cases and isolation from their inputs.
