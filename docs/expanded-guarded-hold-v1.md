# Guarded synthetic hold v1

`wildfire_expanded_guarded_v1.hold_v1.GuardedHoldV1` is an opt-in, unscored
composition over a fresh exact `GuardedIncidentV1`. It retains guarded ground
dispatch, return and synthetic vehicle evacuation, and adds crew and engine
hold work through the existing `MixedHoldFireV1`, containment kernel and
Operations. It does not implement aircraft, a full incident-day scorer or a
qualified twelve-case source runner.

A road is not evidence of work access. The caller must separately pin a
`wildfire-guarded-synthetic-hold-sites-v1` document with exactly `version`,
`role`, `inputs_sha256`, `graph_sha256` and `sites`. Its role must be
`synthetic_fixture_only`. Each site has exactly `node`, `edge`, `protected_cell`
and `kinds`. The node must be an exact unambiguous graph node in one endpoint
cell of the orthogonal edge; both edge cells must have independently supplied
synthetic accessible-land and supported fuel/elevation evidence. The protected
cell must be an endpoint. Kinds are `crew` and/or `engine`. These are explicit
synthetic work-location assumptions, not a claim that a road grants access to a
whole cell or that off-road walking is physically safe. No walking is modeled.
Nonempty synthetic work-site evidence is rejected with `source_unknown` route
access; actual v4 cell access remains unknown, so it cannot authorize a hold.
A syntactically valid request for an unsupported site consumes the normal
command tick and is declined through a delayed dispatch report, so the
private site list is not exposed by an immediate validation error.

```python
from wildfire_expanded_guarded_v1.hold_v1 import GuardedHoldV1

host = GuardedHoldV1(fresh_guarded_host, synthetic_sites,
    expected_work_sites_sha256=trusted_sites_sha256, opt_in=True)
option = host.route_options('crew-0', exact_work_node)['options'][0]
host.dispatch_hold('crew-0', exact_work_node, option['id'], edge, protected_cell)
```

The guarded component validates the selected whole-edge road journey using
reported information and again using current private hazards before dispatch.
Every traveled segment is rechecked, and both explicit release and Operations'
automatic return use the same exact-node guarded return planner. Mid-edge or
unsafe origins cannot create partial-edge returns; the unit needs assistance.
Operations handles mobilization, travel and setup; work cannot start early.
A working resource must remain at its declared exact node, pass the current
hazard check, have duty and engine water, and have a usable, nonconflicting
line. The existing mixed-fire wrapper validates the assignment and advances the
existing containment kernel exactly once.

Operations and its reports are staged. The host commits them only when the
physical fire tick returns exactly the same duty and water costs; no work cost
is charged on an unavailable assignment. An exception after a tick starts
poisons the host until a prior trusted checkpoint is restored. A post-fire censor
retains the matching private charges but suppresses reports and further actor
operations. Field `hold_status` observations are delayed through EventBus and
contain neither private line strength nor true fire state. The action
transcript and checkpoint are evaluator-private; replay checks independently
provided identity and transcript hashes and compares public action results.

The guarded host's source identity also binds the actual Operations, EventBus,
`wildfire_core.mobility_routing`, routing, road guard, weather and supporting
modules. It rejects dependencies loaded from unexpected repository paths and
checks key imported bindings. The hold wrapper checks the guarded identity, including the independently
pinned reviewed source/graph pair and verifier binding, and its own hold-fire
source. A changed dependency fails closed at a public boundary
or restore; old checkpoints require their original source identities. These
checks detect changes and accidental wrong imports; they are not an adversarial
Python sandbox or authentication for untrusted checkpoints.

The guarded road component now pins one independently reviewed complete
source/graph pair. `tests/test_expanded_guarded_hold_v1.py` uses an isolated,
test-only synthetic-pair trust seam to exercise real routing, operations,
weather and fire transitions with a small synthetic graph; it does not relax the
production reviewed-pair check. It tests engine and crew duty, water exhaustion,
no premature work, guarded and unsafe returns, an actual controlled blocked
fire crossing, cost-mismatch recovery, privacy, checkpoint/replay and physical
censoring. Dependency-mutation tests simulate changed bytes without modifying
files used by other running diagnostics, and also test incorrect module origins.
These results do not establish real-source access, physical calibration,
scoring, or the full active-case/policy matrix.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest
 tests/test_expanded_guarded_hold_v1.py -q -p no:cacheprovider` with project
dependencies installed. Checkpoints, roster, source evidence, work-site list and
Python objects are trusted-driver data; expose only a reviewed actor allowlist.
