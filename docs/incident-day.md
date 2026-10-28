# Incident-day v2 CPU acceptance

Incident-day v2 is a synthetic 24-hour exercise with one-minute ticks. The core
combines fire, road and walking routing, finite resources and stocks, arrival and
setup, duty and rest, delayed communications, and independently recomputed
outcomes. The dispatcher baseline uses public observations; it is a reference,
not an optimal policy or historical operational record. One run first measures a
full 1,440-minute case, including verification, then runs the six-case batch
sequentially on one CPU worker. Each completed case must pass exact replay and
independent reward verification before a score is recorded.
The passive policy is a useful initial completion check; identical passive scores
do not establish that the benchmark is calibrated or discriminative.

The initial synthetic runs are archived in
`results/incident-day-v2-20261028.json` and `.html`; they are historical results,
not the latest implementation assessment. Later dispatcher fixes and the additive
shift scenarios are summarized in `results/estimated-fuel-shift-checks-20261028.json`
and `.html`. All six shift days complete, replay exactly and agree with independent
reward accounting. Their fourteen late ground recoveries led to an additive,
opt-in withdrawal profile; the old IDs retain their original behavior. These
development cases establish neither physical
calibration nor benchmark saturation.

The display-road regressions are fixed in merged PR #38. Its focused validation
passes 109 Python and 126 frontend tests plus a production build. Incident-day
tests include actual HTTP/OpenEnv, disk-checkpoint replay, finite resources,
source-input gates and independent reward accounting. Docker execution remains
unverified where Docker is unavailable. See PR #41 and issue #34 for current
acceptance evidence and outstanding gates.

## Agent contract

`wildfire_core.incident_day.IncidentDayEnv` exposes `reset(id, seed)` and
`step(action)`, returning observation, reward, terminated, truncated and public
diagnostics. `IncidentDayTools` supplies eight provider-independent named tools:
situation, report history, resource approaches, evacuation approaches, draft
checks, committed orders, centre status requests and bounded waiting. No API
key, model weights or GPU is required.

Committed orders take one minute. `advance_time(1..60)` stops for actionable
delivered information, allocation deadlines or an hourly checkpoint boundary.
Each minute applies environmental changes, due commands, movement/work, fire and
population effects, observations/deliveries, then audit recording. Routine
telemetry is retained without waking the policy. Reads do not consume time or
fire randomness. Reports distinguish observation, reporting and receipt times.
Allocation expiry is the local authorization-submission deadline; an order
submitted before expiry still takes its one-minute command latency. Actual
resource readiness and flight conditions are rechecked when it executes.

The six IDs `incident-day-dev-000` through `005` cover quiet handover, wind/access
loss, crew-only approaches, aircraft operating windows, a threatened evacuation
destination and regional shortages. They are development probes, not a held-out
training/evaluation catalog. Aged fire, existing assignments and delayed
handover notices initialize the day; that prehistory is not a demonstration.

Reward is zero until completion, then `0.8 × newly protected population fraction
+ 0.2 × fraction of remaining burnable cells not newly burned`. Inherited burned
area and inherited exposure are excluded. Abandoned or boundary-censored runs
cannot earn a successful day score. Independent accounting reads the private
minute audit rather than the production outcome counter. This does not validate
the exercise's numerical fire/safety assumptions. Reward follow-up #27 remains
open.

The fixture, capacities, weather and roster are synthetic. The profile's default
240 minutes of work and 60 minutes of rest are exercise assumptions, not labour
law. Fixed-wing aircraft are landloaded; no minimum fire-area rule is assumed.
Their finite fuel endurance and the bases' water stocks are exercise values.
Only a co-located, kind-compatible declared facility can refill a unit.
Communication delays depend on source and channel, with a synthetic maximum of
60 minutes. Road coverage is partial to the east, with only 62 truck-accessible
grid centres under the existing rule; a route gap does not prove a footpath.

The real-data source-pack gate remains **BLOCKED**: the cropped Málaga inputs
contain 80 unknown fuel cells. Source zero is NoData, not verified water, bare
ground or a firebreak. Synthetic values do not resolve or approve those cells.
See `data/incident_day_v2/README.md` for provenance and the 32 flagged earlier
variants' structural dispositions. Existing benchmark IDs, splits and artifacts
remain unchanged.

Run only after the incident-day core and its verification are ready:

```bash
python eval/run_incident_day.py --output /tmp/incident-day-acceptance --policy dispatcher --wall-budget 600
python eval/run_incident_day.py --output /tmp/incident-day-acceptance --policy dispatcher --wall-budget 600 --resume /tmp/incident-day-acceptance/private/checkpoint.json.gz
```

The output directory must be new on the first invocation. Resume uses the same
fixture, policy, output directory and implementation; a checkpoint and its
sidecar contain private simulator and dispatcher state. The wall budget applies
per invocation and is checked between environment steps; reset, a step and
verification cannot be preempted mid-operation. SIGINT and SIGTERM request a
checkpoint at the next safe boundary. An interrupted or censored episode is not
a successful score. The JSON and HTML reports are updated after each case;
canonical gzip trajectories, private audits and checkpoints stay in the private
subdirectory. Do not publish that directory. Elapsed time uses a monotonic clock;
peak RSS is the process high-water mark, not per-case allocated memory.

## External client and storage

Install the separately pinned adapter (`openenv==0.7.0`) with
`pip install -e envs/wildfire_response/core -e envs/wildfire_response/openenv`.
Run `python -m eval.incident_day_server --fixture`, then
`python -m wildfire_openenv.incident_day_rollout`. The localhost viewer at
`http://127.0.0.1:8005/` has a read-only trajectory slider and JSON order form.
The dedicated REST client includes non-advancing route/report queries; typed
OpenEnv reset/step/state compatibility is tested separately. One active episode
is supported. No checkpoint or private evaluator endpoint is exposed.

For a Linux-host-network local container, build with
`docker build -f envs/wildfire_response/openenv/Dockerfile.incident-day -t wildfire-day .`
and run `docker run --rm --network host wildfire-day`.
`python eval/check_incident_day_container.py --output /tmp/NEW-CONTAINER.json`
builds a digest-pinned base revision and checks the installed image through an
external Python client. Both coverage variants complete a day with exact public
step/reward parity against independently verified local episodes. The bounded
GitHub job runs as a non-root user with a read-only filesystem and resource limits,
then removes the test container. See `results/incident-day-container-20261028.json`
for the actual image identity and CI link. This verifies the synthetic fixture,
not real-source approval, other platforms, the legacy adapter or publication.

Passing a fresh `artifact_dir` to the core streams public observation/action and
query JSONL alongside a separate restricted `private/audit.jsonl`. Hourly gzip
checkpoints pin implementation, input, scenario and road identities. Resume into
a new artifact segment; never concatenate private audit into actor observations.
Malformed committed actions retain rejected arguments and validation errors in
the exported trajectory. The acceptance runner's compact canonical trajectories
are deliberately kept with private artifacts until inspected for publication.
The report counts cases with actual fire growth and new population exposure from
the private audit; a zero count is an observed gap, not a successful coverage
claim. A completed suite requires fire growth in at least one batch case; a
passive suite also requires new population exposure in at least one batch case.
These checks make no claim that every named scenario exercised its intended
phenomenon. When present, the resource audit counts observed state changes,
refill, rest and blocked entries, stock increases and applied suppression.
Minute snapshots can miss transient states; stock increases alone do not prove a
refill succeeded, and missing resource audit data is reported explicitly. These
counts do not replace behavioral tests. A change to the implementation invalidates
a run.

## Quality follow-up

`eval/check_incident_day_calibration.py` runs bounded, paired development cases
and single-mechanic probes. The reference dispatcher compares public route
options, respects reported capacity (including blocked reservations), avoids
duplicate orders and reacts to newly reported destination closures. Resident
condition reports distinguish current danger from cumulative exposure; reaching
a clear destination does not erase past exposure or trigger endless relocation.
These are causal-behaviour checks, not a claim of calibrated wildfire physics.

The reproducible 80-cell evidence ledger and the official-source investigation
are in `data/incident_day_v2/fuel_review/`. Optional real-geography runs vary
explicit synthetic fuel assumptions without changing the unknown mask or
approving the source pack. They are ungraded sensitivity exercises, not training
labels. The low-cost synthetic development fixture remains the default.

The road overlay now has a portable, pinned source bundle and an archived prior
revision; see `data/ui_v2/snapshots/README.md`. New clones no longer depend on the
lost ignored road cache. This repairs display reproducibility, not routing
coverage or verified access for fire engines.

## Estimated-map and shift exercises

`data/incident_day_v2/fuel_estimation/` now supplies categorical, land-cover-aware
estimates for the 80 missing fuel values without changing their source-known
mask. The three urban centres use explicitly assumed mixed built/garden fuel,
not neighbouring forest or a zero-fuel barrier. The 27 centres with unresolved
land/water context remain flagged; a run is censored before fire advances near
those cells. Missing land cover is never interpreted as sea or as a firebreak.
The spatial holdout is exploratory, not a probability of correctness.

Compile with `eval/prepare_incident_day.py --fuel-estimates FILE
--fuel-estimates-sha256 HASH` and the usual pinned prepared/domain/network
arguments. `--allow-unapproved` is required because estimated exercise inputs
are not verified historical measurements. Do not combine this with the older
uniform synthetic-fuel comparison option.

The additive IDs `incident-day-shift-v1-000` through `005` use valid inherited
staging or disclosed recovery, finite named eight-hour ground shifts, two
daylight-gated aircraft slots, and return-time reserves. They preserve the older
development IDs and the two-hour benchmark. Run
`python eval/check_incident_day_plausibility.py --output /tmp/NEW --wall-budget 600`
for all six verified days and readable timelines. Passing the automatic
consistency screen is not physical calibration or expert human approval.

## Emergency withdrawal exercises

`incident-day-egress-v1-000` through `005` pair the same seeds, events and roster
with `return-only-buffer-exit-v1`. All previous IDs and their scenario identities
remain available. This is a synthetic emergency-withdrawal assumption, not an
operationally certified escape rule.

Only a trusted return-to-base journey may leave a non-burning origin inside the
protective fire/wind buffer. It must follow existing directed roads or supported
crew walking terrain, exit the initial buffered section, and never re-enter.
Burning cells, explicit access restrictions and vehicle-mode prohibitions remain
forbidden. Each minute rechecks the complete swept segment against current
conditions. Once outside, ordinary buffer and endpoint checks apply. The bounded
search may fail to establish a route; `unmapped` does not prove physical
impossibility. No route is invented to make a scenario pass.

Delivered resource status identifies the withdrawal policy and whether the buffer
has been exited. Initial buffer geometry stays in private evaluator records.
Disk checkpoints preserve this state, including a checkpoint taken before exit.

The final six-case fixture batch completes all days with exact replay and
independent reward agreement, and records zero late-recovery exceptions (the
older shift profile has fourteen). The final suite passes 243 tests without
skips, including HTTP parity and frozen extraction checks. See
`results/incident-day-withdrawal-20261028.html` and `.json`. This does not resolve
physical calibration, real-source input approval, or the absence of observed
crew-only-access and destination-threat complications in these policy runs.

## Current access and destination coverage

The default six-family acceptance suite now uses two additive variants:
`incident-day-coverage-v1-002` and `incident-day-coverage-v1-004`. The other four
IDs come from the egress suite. All previous IDs and scenario hashes remain
available; earlier reports keep their original interpretation.

In the access variant, a private same-state route comparison records the actual
effect of the vehicle restriction. Coverage additionally requires received public
route refusals and applied crew work inside the restricted sector, with no engine
work there. A report appearing near the event is insufficient. The private
comparison never enters actor observations or route previews.

The destination variant starts with a declared, synthetic handover cohort of 150
people already at the reception centre that later closes. Closure marks those
occupants blocked and sends a delayed relocation warning. Coverage requires a new
order after a delivered warning and actual arrival at another open destination.
Ignoring the warning leaves an unresolved cohort. An unused centre, a warning
alone, or an unrelated movement cannot satisfy this check. The pre-handover
occupancy is initialization, not a historical journey or expert demonstration.

The current six-case batch completes all 1,440-minute days with exact replay and
independent reward agreement, with both targeted effects observed and no audit
contradictions or blocking findings. Validation covers 255 distinct tests without
skips (253 in the primary run, two checkpoint-boundary follow-ups). The test audit
removed one redundant completed-episode replay while preserving all supported
legacy and safety regressions. See `results/incident-day-coverage-20261028.html`
and `.json` for evidence and remaining calibration gates.

Run the current acceptance suite with
`python eval/check_incident_day_plausibility.py --output /tmp/NEW-COVERAGE-RUN --wall-budget 600`.
The `--cases` option still selects explicit earlier suites. Targeted partial
selections remain incomplete suite reports even when their selected episodes
finish and verify.

## Calibration and source-pack readiness

The [evidence ledger](../data/incident_day_v2/calibration/operational-evidence.md)
and [data audit](../data/incident_day_v2/readiness/README.md) separate source
identity, opt-in estimated exercises and operational validation. The cached
weather clock is resolved; it is not a conversion from station m/s to model wind
units. Detection-to-arrival records and aircraft specifications provide limited
context, not setup, duty, suppression or flight-safety calibration.

The actual estimated-map six-case run completes and replays, but has no applied
ground work and lacks several intended family effects. Its current ground bases
and bounded route candidates do not establish usable access. Retained display
roads omit node-access metadata and cannot simply be relabeled as routing data.
The smaller 20-by-26 candidate with fewer uncertain cells fails the existing
minimum 24-by-24 scenario contract. The 80 estimates and 27 unresolved land/water
centres remain visible; default source rejection and the censor guard stay active.

Runtime and the corrected refill screen now both use `air_refill_minutes`.
`aircraft_refill_minutes` remains a legacy compatibility field; neither setting
is empirically calibrated. The 80/20 reward and existing scenario identities are
unchanged.

## Source-specific ground exercises

The six additive `incident-day-realmap-v1-000` through `005` cases require the
estimated pack `50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d`
and road graph `12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16`.
They retain the full map, source masks, uncertain-land guard and existing speeds.

These are new synthetic exercise placements. The incident uses source-known fuel
around sector cell `[16, 28]`; temporary posts use actual mapped nodes
`osm:n321315386` and `osm:n6119295366`. Posts sit within the active domain and
outside the vehicle-exclusion radius. Source-pinned initial approaches and return
routes are checked before compilation. Crews start at their posts and must
travel and set up before work. Distinct synthetic population homes are retained;
case 004 explicitly starts a cohort at the reception centre that later closes.

No road connectors, new map data or historical bases are invented. Short walking
legs still rely on declared terrain assumptions, and the existing graph does not
enforce all turn restrictions globally. These cases do not approve historical
fuel, field access or physical suppression. The earlier midpoint cases and their
zero-ground-work reports remain unchanged rather than being silently resited.
Fixture-only clients omit these map-specific IDs from task discovery.

```sh
python eval/check_incident_day_plausibility.py \
  --inputs .runtime/estimated-incident-day-inputs-final.json \
  --network data/coastal_mobility_v1/network.json --allow-unapproved-data \
  --output /tmp/NEW-GROUND-RUN --wall-budget 600 \
  --cases incident-day-realmap-v1-000 incident-day-realmap-v1-001 \
          incident-day-realmap-v1-002 incident-day-realmap-v1-003 \
          incident-day-realmap-v1-004 incident-day-realmap-v1-005
```

Run the separate six-case suite with:

```sh
python eval/check_incident_day_plausibility.py --output /tmp/NEW-EGRESS-RUN --wall-budget 600 \
  --cases incident-day-egress-v1-000 incident-day-egress-v1-001 incident-day-egress-v1-002 \
          incident-day-egress-v1-003 incident-day-egress-v1-004 incident-day-egress-v1-005
```
