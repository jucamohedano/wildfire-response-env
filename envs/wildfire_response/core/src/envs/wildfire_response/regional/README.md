# Regional response v2 — Huelva pilot (alpha)

A separate CPU-only environment for one simulated provincial director coordinating
two ground bases and a shared aircraft. No model API key is needed. The original
`WildfireResponseEnv`, drone demo, scenario splits and reward version remain unchanged.
There is no live dispatch, HappyRobot call or real communication integration here.
See [acceptance results and limitations](RESULTS.md) for measured checks and rollouts.
The [frozen benchmark protocol](BENCHMARK.md), [held-out results](HELDOUT.md) and
[v2 director tools](director/README.md) document the next stage. Model-provider
requests remain paused; the director has been exercised with scripted transports.
A separate [local Qwen3 training pilot](training/RESULTS.md) now exercises real
LoRA/GRPO on the supplied GPU. It verifies the training path but does not yet
demonstrate a useful learned director; no model checkpoint has been published.

## Evidence, not invented operational records

`../data/huelva_regional_v2.json` separates three categories:

- **Reviewed source facts:** INFOCA's 2026 catalogue lists the Valverde del Camino
  and Cabezudos CEDEFOs and Las Arenas airstrip in Huelva. Published capability
  classes include heavy engines with 3,000–4,000 litre tanks and ground-loaded
  aircraft with 3,100 litre capacity. MITECO describes aerial roles and reinforcement
  coordination. These facts are not a verified assignment of an aircraft or crews
  to these facilities on a particular day. Aggregate catalogue totals disagree
  in places; this pack does not silently reconcile or use them as a roster.
- **Derived geography:** the Niebla AOI in CEMS EMSR913 supplies a rectangular
  coverage envelope. A local equirectangular approximation produces a padded
  53×41 grid at nominal 1 km spacing. It is not a burnt perimeter, surveyed map,
  administrative boundary or historical ignition location. Coverage can cross
  provincial boundaries. No real base coordinates were verified: bases stay
  off-map with `coordinates: null`, rather than being placed at guessed locations.
- **Scenario assumptions:** one engine and one crew at each ground base, one
  shared ground-loaded aircraft at Las Arenas, readiness, ETA/mobilization tables,
  refill delays, effectiveness, daylight/wind gates, uniform scrub terrain,
  synthetic settlements/populations/refuges and incident timing. All are explicitly
  fictional operational allocations or uncalibrated model parameters. No measured
  road network, sorties, dispatch transcripts or historical readiness records are used.

Sources and reviewed SHA256 digests are in the pack. Raw snapshots and derived
`pack.json`/`provenance.json` are local under
`.runtime/regional-v2-sources-2026-10-19/`. Raw INFOCA pages include public contact
information; none is included in actor observations. Reuse/publication rights
and the root code licence still require clearance before a public release.

## Prepare the evidence

```sh
python -m envs.wildfire_response.regional.data --output .runtime/huelva-v2-evidence
python -m envs.wildfire_response.regional.data --output .runtime/huelva-v2-evidence --offline
```

Live preparation requires a fresh directory. HTML prose must match reviewed text
digests (whitespace normalized, scripts/styles and changing element IDs excluded);
raw bytes are independently fingerprinted. A changed source text fails for review
instead of silently updating facts. CEMS JSON is pinned to reviewed raw bytes. Its code,
year, AOI identity and bbox are checked separately. Offline preparation checks the
same cached hashes. A hash confirms content, not an official publication timestamp;
these current/retrospective sources do not certify what was available in a past incident.

## Run locally

```sh
python -m unittest envs.wildfire_response.tests.test_regional
python -m envs.wildfire_response.regional.run --output .runtime/regional-demo
python -m envs.wildfire_response.regional.run \
  --replay .runtime/regional-demo/trajectory.json.gz
```

```python
from envs.wildfire_response.regional import RegionalResponseEnv

env = RegionalResponseEnv()
observation, info = env.reset('regional-000', seed=20000)
observation, reward, terminated, truncated, info = env.step({
    'requests': [{'resource_id': 'engine-valverde',
                  'sector_id': 'report:report-0', 'mission': 'suppress'}]
})
offer = next(item for item in observation['offers'] if item['status'] == 'offered')
observation, reward, terminated, truncated, info = env.step({
    'authorizations': [offer['id']]
})
```

## Director contract

An action can contain `requests`, `authorizations`, `releases`, `messages` and
`explanation`. Omitted lists are empty. Lists have at most twelve entries and the
whole action is at most 64 KiB. Reasons do not earn reward.

- **Requests:** select a resource ID, a public sector ID and `recon` or `suppress`.
  A request is not a dispatch. The simulated owning base can offer or deny it.
  The request/offer round trip takes one assumed model minute. Busy, unavailable
  or currently grounded resources are denied, not silently replaced.
- **Action checks:** `check_action(action)` performs at most three non-advancing
  contract checks per decision. It does not mutate missions, spend resource time,
  predict a base's reply or inspect future fire. Checks reset after advancing time.
  A valid draft is not a promise of successful dispatch or suppression.
- **Targets:** report and protection sectors are supplemented by named sectors
  from received sensor fire sightings. Their IDs remain available after a sighting
  clears, so standing missions do not lose their target identity. Hidden fire never
  creates a target. This prevents expanding fire edges becoming unaddressable.
- **Offers:** reserve exactly one resource for eight model minutes. Their assumed
  lead time includes mobilization and travel after authorization. A live offer
  must be explicitly authorized; expired/committed offers cannot dispatch again.
- **Cooperation:** allocations from different bases to the same sector generate
  delayed mutual-aid updates between those bases, relayed via the director.
  This is a deliberately simplified protocol, not official command procedure.
- **Ground resources:** mobilize, travel to the selected report/protection sector,
  sense locally and operate if fire is observed. Engines have finite water and
  return/refill when empty. Crews use an abstract containment effect. A director
  must release on-scene units; there is no mid-mission retasking in this alpha.
- **Aircraft:** mobilize and travel, observe on arrival, make at most one finite
  water drop for that sortie, then return and refill/turn around. Reconnaissance
  does not consume water. The aircraft never hovers and suppresses every tick.
  If conditions change before takeoff, the sortie is cancelled; if airborne, it
  returns without a drop. Safety gates are scenario assumptions, not flight advice.
- **Population messages:** `inform` preserves movement status. Only a delivered
  `evacuate` instruction initiates relocation, after two model minutes. Base
  `status` messages return status but never allocate resources.
- **Release:** reserved offers can be cancelled; mobilized or deployed resources
  return over time, not instantly. Resource identity/home-base ownership is conserved.

Each decision advances two assumed model minutes, default sixty decisions (120
minutes). At each minute: scheduled wind/ignition/readiness, queued deliveries,
resource phase transitions/observations/operations, then fire/population physics
and refreshed sensors. Due reports enter the public ledger at the decision boundary;
observation, receipt and actual availability times are distinct. Future events,
scenario-family labels, complete fire state and future reports stay evaluator-only.
Wind points **toward** spread, positive X east and Y south, in a synthetic index
rather than measured m/s. Invalid actions are rejected atomically, preserve standing
missions and advance time; three consecutive invalid actions truncate with a penalty.

The fire kernel is reused from `simulator.engine.Simulation`, with the demo's
vehicle controllers, routes and completion disabled. Spread timing is scaled by
an explicit assumed factor. Population relocation is separate and synthetic. Grid
heat/wetting and unit effectiveness are coarse proxies: litres do not imply verified
hectares treated, and no flight paths, hydraulics, smoke exposure or crew safety
model is claimed. A source-based place name does not validate these dynamics.

## Tasks, reward and acceptance

`regional-000` to `regional-005` cover local smoke, a growing fire, a false alarm,
competing incidents, temporarily unavailable aircraft and a wind shift. Ignitions
are synthetic and spread toward nearby settlements through the kernel; there are
no scripted later ignitions directly inside occupied settlements. These are six
acceptance configurations, not a frozen train/validation/test benchmark.

`regional-task-pack-v0.2` additionally defines 36 synthetic alpha configurations:
24 training, 6 validation and 6 test, all families in every split. These existing
configurations are now frozen by `regional-benchmark-v0.2.0`, not regenerated
after held-out evaluation. Spatial/configuration
groups and seeds are disjoint; this is not a held-out-real-incident or held-out-map
claim. Reports include delayed duplicates, conflicting claims and locationless noise;
generation annotations stay private. Synthetic wind is aligned with the displaced
report location to preserve meaningful risk variation, not taken from weather data.

```sh
python -m envs.wildfire_response.regional.evaluate \
  --output .runtime/regional-task-pack --generate-only
python -m envs.wildfire_response.regional.evaluate \
  --output .runtime/regional-training-calibration --split train
```

Exports first verify the committed lock and separate public task metadata from private full configurations. Training
calibration compares passive, seeded random-valid, blanket-warning and cooperative
policies, independently verifies every trajectory and replays it. Weak-policy flags
are diagnostics, not an automatic selection/culling rule. Validation/test outcomes
are not consulted for that training calibration. No benchmark readiness or optimum
is claimed just because a scripted reference beats weak policies.

Reward is negative incremental outcome cost under `regional-outcome-cost-v2`:
population exposure fraction ×100, burned-terrain fraction ×1, unnecessary evacuation
person-time/(population×120) ×0.1 in no-ignition tasks, committed resource-minutes
×0.00005, redundant notices ×0.002, invalid actions ×0.05 and invalid-action
abandonment ×102, plus a terminal cost of 2 if active or scheduled fire remains
unresolved at the horizon. The versioned terminal term closes the "warn everyone,
leave the fire burning" shortcut. Old v1 alpha scores must be replayed on their
recorded revision, not mixed with v2. Offers, requests and messages never earn positive reward by volume.
Resource commitment costs discourage reserving or deploying every unit for free,
but need calibration across broader cases before any RL claim. Scores are not
comparable with v1. Explain exposure and resource cost separately from total reward.

The cooperative baseline deliberately over-requests and warns on nearby reports.
It demonstrates the protocol, not optimal emergency management. Restraint can score
better on false alarms and small non-threatening fires. Natural termination requires
all scheduled incidents/reports resolved, no active fire or relocating population,
and all resources returned; reaching the budget is not resolution.

The runner saves public trajectories, separate private evaluator state, outcome
components, pack/source digests and exact-replay checks. A separately implemented
verifier reconstructs resource-minutes, notice duplication, water/lifecycle rules,
population/terrain components and reward from raw minute records and checkpoints.
It never calls the simulator's `_components()` or `_cost()`. This checks accounting
and logical invariants, not independent wildfire physics or global optimality.

## Dedicated OpenEnv server and external client

The optional OpenEnv 0.7.0 adapter is separate from the v1 server. It supports one
active session, opaque task discovery and typed bounded actions. No private scoring
or checkpoint API is exposed. Install the existing `[openenv,dev]` extras, then:

```sh
python -m envs.wildfire_response.regional.openenv.server --port 8001
python -m envs.wildfire_response.regional.openenv.rollout --url http://127.0.0.1:8001
REGIONAL_TEST_URL=http://127.0.0.1:8001 python -m unittest \
  envs.wildfire_response.tests.test_regional_openenv
```

Use `RegionalClient` and `RegionalAction` from `regional.openenv.client` and
`regional.openenv.models`. `operation='check'` returns a zero-reward, non-advancing
check in observation metadata. The default `operation='advance'` applies the action
and advances time. Malformed wire requests are rejected before stepping; external
harnesses must bound their retry/turn budget too. The Docker specification builds
from the repository root with `-f envs/wildfire_response/regional/openenv/Dockerfile`;
it is supplied but has not been built here because Docker is unavailable.

The existing v1 model director does not control v2; the separate
[v2 director adapter](director/README.md) now implements that interface and has
offline/local-server acceptance tests. The separate optional training pilot now
runs local-weight inference and bounded LoRA/GRPO; external model-provider calls,
public deployment and publication remain paused. Next steps include a verified
training-only multi-turn warm start, sourced base geocoding, road/land-cover data,
measured operational timing where available and wider scenario validation.
