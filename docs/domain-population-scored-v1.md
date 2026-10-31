# Synthetic domain-ground-population scoring v1

This is a trusted-evaluator wrapper for `DomainPopulationGroundV1`. It has its
own `synthetic_single_candidate_domain_ground_population` scope and
`wildfire-domain-population-synthetic-80-20-v1` reward identity. It is not the
unrestricted source qualification gate, does not establish access for eleven
other cases, and does not make a score available to the actor. Its public
facade exposes only observations and commands; private truth, masks, outcome,
identities, checkpoints and audit must remain with the trusted evaluator.

`fixed_mask(owner)` proposes a fixed row-major mask at the fresh handover,
using only domain land cells with source-supported terrain and initial fuel,
excluding mixed, estimated and unknown cells. The identity binds that mask, the
pre-episode synthetic public roster, the owner and handover, static simulator
configuration, terrain profiles and scoring dependencies. A digest generated
in the evaluation run is a candidate for review, not independent approval.
The roster is the owner's disclosed synthetic handover, not a live-position
feed. Exposure and fire truth are used only in the private audit.

The core outcome counter and independent recomputation apply 80% to protection
from new exposure and 20% to the retained eligible fixed-mask area. The
pre-handover footprint and pre-existing exposures are excluded from those
respective denominators. Each successful host minute contributes one private
audit row; unsupported or censored minutes cannot produce a completed-day
score. Restore requires a trusted checkpoint digest and checks the independent
recomputation against the counter. The verifier accepts only a complete active
1440-minute synthetic exercise, reconstructs a fresh owner, replays the commands,
compares the full private checkpoint, and independently recomputes its audit.
Its success status is `synthetic_domain_population_full_day`, not a source
qualification status. Scorer results and diagnostics remain private.

Focused synthetic tests cover fixed-mask exclusions, roster aliasing, rejected
actions, checkpoint continuation and the 80/20 accounting formula. They do not
exercise a complete 1440-minute run of this composition. A separate fake-clock
test exercises full-day verifier replay and tamper rejection without running the
incident model. No real-source full-day outcome has been established.

A bounded source-backed probe constructed the scorer with the pinned domain,
ground, population-anchor and segment components, submitted one shelter tick,
and checked the current modeled hazard guard. The verifier returned
`unfinished` for that partial episode. The mask and scorer identity were
generated in the same run and are not independently approved pins.
