# Experimental expanded population protection v1

`wildfire_expanded_full_v1.population.ExpandedPopulationV1` is a private,
opt-in synthetic exercise composition over an exact, fresh
`WeatherMixedHoldV1`. It adds cohort shelter and road-vehicle evacuation using a
separate existing `Operations` instance, while the unchanged hold host continues
to own weather, mixed fire, ground resources and its `EventBus`. It is not a
full incident-day API, a qualified source scenario, an outcome model or a score.
The original hold and movement source files and their pins are unchanged.

Supply an independently pinned synthetic-only population catalog with exactly
`version`, `role`, `case_id`, `inputs_sha256`, `network_sha256`, `groups`, and
`destinations`. Its version is `wildfire-expanded-synthetic-population-v1`, its
role is `synthetic_fixture_only`, and its case and input/network pins must match
the hold host. Each group has exactly `id`, positive integer `count`, and
projected metre `position`; each destination has exactly `id`, nonnegative
integer `capacity`, and projected metre `position`. The existing Operations
constructor checks duplicate IDs and capacity. All supplied positions must be
in confirmed accessible land cells of the synthetic contract. The caller must
independently trust the catalog digest and its fixture provenance; a digest and a
synthetic label do not authenticate inputs.

```python
from wildfire_expanded_full_v1.population import ExpandedPopulationV1

env = ExpandedPopulationV1(fresh_hold_host, synthetic_population_catalog,
    expected_catalog_sha256=trusted_population_catalog_sha256, opt_in=True)
# Delivered-only public calls: situation, reports, route_options,
# evacuation_options, protect, dispatch_hold, and advance.
option = env.evacuation_options('cohort-a', 'destination-a')['options'][0]
env.protect('cohort-a', 'evacuate', destination_id='destination-a',
            option_id=option['id'])
# Or env.protect('cohort-a', 'shelter'). The result confirms submission only;
# capacity acceptance or refusal is delivered later on the event bus.
```

Evacuation uses only road legs whose exact edges explicitly allow `engine` mode,
with an exact destination and the parent host's conservative cell exclusions.
Known fire and initial handover wind provide a stale-knowledge plan; every
movement segment and projected position is rechecked against the current true
fire, wind and exclusions. Unknown access is not made accessible by the
presence of a road. This is the existing synthetic routing contract, not the
separate source-backed road seam and not an inference of access on v4's unknown
cells. There is no off-road walking or emergency egress from an already unsafe
origin. If no supported route is available, the operation remains unexecuted.

Orders consume one simulated minute and use the existing Operations delivery,
acknowledgment and execution lifecycle with fixed two-minute exercise delays at
each stage. Reservations enforce finite destination capacity, and route origin
is checked again at activation. Movement takes real route time and may block;
there is no teleportation. The population Operations copy is advanced once per
host tick and committed alongside it. A failed operation poisons the composition
until a trusted checkpoint is restored. A post-step fire censor keeps the private
population tick but suppresses population observations and any score.

After each safe fire step, exposure follows the existing incident-day synthetic
proxy: fire within 1.5 grid cells adds 10 dose units per minute, or 2 while
sheltering; a burning occupied cell or accumulated dose of 180 marks the whole
cohort exposed. This is simulated exposure, not mortality or calibrated smoke
physics. The actor sees initial synthetic handover data and later population
snapshots only when their reports are delivered; the dose, true position,
reservations, queued reports, future weather and fire state remain private.
Reporting itself assumes synthetic resident/radio observations and is not an
independently validated communications model. Exposure knowledge is monotonic:
a later-delivered false snapshot cannot erase delivered exposure evidence, even
if it has the same observation minute. For position and status, the post-fire
resident update takes precedence over earlier command snapshots from that same
minute; the first snapshot wins ties within the same phase. Exposure evidence
and position/status freshness retain separate private observation timestamps.

Private checkpoints contain the unchanged host checkpoint, population
Operations state, exposure dose, delivered knowledge and communication cursor.
Restore checks the catalog and source identities, clocks and checkpoint
integrity, and reproduces the complete checkpoint, including the separate
exposure timestamp and snapshot phase. Digests detect accidental
changes; they do not authenticate untrusted artifacts. A fresh-instance
continuation is tested, but there is no public-transcript verifier, fixed-mask
reward, external audit or complete active-case/policy matrix for this slice.
The Python instance and its checkpoint must remain trusted-driver data; expose
only the documented actor calls through a reviewed allowlist.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_population_v1.py
-q -p no:cacheprovider`. The isolated test uses synthetic access and a test-only
mixed-input trust anchor. It compares shelter with a passive cohort, exercises
finite-capacity evacuation and delayed reports, checks real movement and a
fire-blocked route, verifies unknown access remains unmapped, runs a concurrent
ground hold, and checks checkpoint continuation and a controlled physical
boundary censor. It does not validate any of the twelve source scenarios.

Malformed or stale public hold options are rejected before either host or
population advances. An exception after a state-changing tick starts poisons
the composition until a previously trusted checkpoint is restored.
