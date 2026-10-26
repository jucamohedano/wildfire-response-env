# Reactive incident exercises

`wildfire_core.incident.ReactiveOpsEnv` reuses the existing complete terrain,
fire, population-protection, finite-resource and readback engines. Six new
`incident-dev-*` tasks add operational calls and radio updates to the corresponding
`topo-dev-*` development configurations. These are synthetic exercises on one
Málaga source region, not new independent training/test incidents.

## AirOps implementation findings

Inspected through a delegated source review: AirOps plugin v0.5.0 at
`b6b71d867661b410d06e1c06af7c46c1c8985501`. It bundles the core; FineEnvs commit
`53541fa1d385a9361530f2a6506f45cb5186268b` separates core/adapter packages at
version 0.4.2. Package releases differ from environment generations: v2 adds the
crew desk, v3 adds the customer desk. No AirOps source code was copied.

- [Crew shortage detection and message deduplication](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/src/agentenv_airops/programs/crewsim.py#L837):
  crew availability produces structured hold/cancel requests; changed state
  produces new messages. This motivates our locally detected support requests.
- [Turn pages and outstanding requests](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/src/agentenv_airops/occ/pages.py#L159):
  new messages and persistent requests are distinct. Our reads are deliberately
  nondestructive so inspecting or replaying a frame never consumes messages.
- [Scheduled advisories](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/src/agentenv_airops/occ/day.py#L92)
  remain exogenous, distinct from state-triggered messages. A gateway trigger
  labelled `action` does not mean an agent caused the weather.
- [Dispatch event queue](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/src/agentenv_airops/programs/sim.py#L232)
  models readiness/departure/arrival separately. We retain minute-by-minute
  mobilization, travel, suppression, return and refill transitions.
- [Acknowledgement versus execution test](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/tests/occ/test_occ3.py#L89)
  reinforces keeping received/accepted orders distinct from physical effects.
- [Replay digests](https://github.com/earakely-scale/agentenv-airops-plugin/blob/b6b71d867661b410d06e1c06af7c46c1c8985501/src/agentenv_airops/occ/replay.py#L23)
  motivate recording every delivered update and checking the entire replay.

AirOps uses desk turns and hourly watches. This environment retains two-minute
commits and processes each intervening minute. The reference Space is a recorded
episode viewer; a polished viewer does not itself establish simulation fidelity.

## Clock, detection and delivery

The default horizon is 120 exercise minutes. Each commit, including an invalid
one, advances two minutes; reads/checks advance none. The inherited invalid-action
streak, end conditions and safety-gated 80% population objective are unchanged.
API latency and UI playback cadence do not alter simulation time.

At reset, current smoke can queue a call observed at minute zero. On each tick:

1. Apply scheduled ignition, wind and availability events.
2. Deliver due incident updates, protection orders and other queued messages.
3. Advance resource mobilization/travel/refill and apply eligible on-scene effects.
4. Advance fire, evacuation and abstract smoke-dose physics.
5. Update local sensor memory and detect operational conditions.
6. Queue newly triggered updates for later delivery. Present scheduled reports
   and the delivered inbox at the next decision boundary.

Resident calls arrive after two minutes; radio updates after one. Initial calls,
escalation, local clearance and executed protection change the resident stream.
Unanswered conditions may repeat after eight minutes. Only a delivered resolution
removes an outstanding request. A protection update closes a request for
instructions, not a fire incident or the population-safety objective.

On-scene units request support when locally observed burning cells exceed the
available suppression coverage. Each loaded engine has three exercise coverage
slots; each crew has one. Slots match distinct visible cells within the actual
three-cell range. Recon, en-route, empty-water and warning-duty units supply no
suppression capacity. This is an explicitly uncalibrated communication heuristic,
not a new suppression equation or a claim about real firefighter productivity.
Departure withdraws the reporting unit's request without claiming the fire is out.

The 64-update episode budget reserves an escalation and closure for each normal
open request, and a closure for each urgent request. Routine updates/follow-ups
are dropped before consuming those reserved slots. Updates due beyond the horizon
are not queued; any last delivered unresolved request remains visibly outstanding.

## Public contract and reproducibility

`incident_events` is the delivered ledger; `active_incidents` contains its latest
open records. IDs are assigned in delivery order. Each record carries source,
channel, observed/received minutes, local location, kind, priority and status.
`event_cursor` is the delivered count. Pending incoming payloads and their delivery
times are private. A received location becomes an `event:<id>` reconnaissance
target. Operational updates are claims, not automatically confirmed fire reports;
the existing report-triage objective continues to use its evidence ledger.

Resource `arrival_at` and `arrival_in_minutes` distinguish arrival from the next
phase deadline `due_at`. Arrival after authorization includes mobilization and
the existing assumed transit calculation. Displayed roads are not routes. Vehicle
speed, road closures and firefighter readiness are not historically calibrated.

Templates select wording from the episode seed without consuming the fire RNG.
Repeated reads, checks and UI playback do not mutate the event ledger. Full
trajectories pin the event rules, source inputs and a conservative implementation
fingerprint covering regional and simulator Python modules. Exact replay verifies
every delivered update, action, reward and terminal flag. Legacy task IDs and the
v1 model contract remain available.

## Focused model tools

`wildfire_core.compact_ops_model.CompactOpsModelPolicy` exposes v2 tools including
`request_resources`, `authorize_dispatch`, `protect_population`, `assess_reports`,
`clarify_reports`, `release_resources`, `send_updates`, `correct_readbacks` and
`advance_time`. Sparse `submit_action` combines compatible orders; `check_action`
checks a sparse draft. Omitted lists mean no new order by contract. Inner order
fields, IDs, readbacks, safety checks and invalid-action penalties still apply.

Both provider runners accept `--reactive` to select these tasks/tools. Provider
authorization and spending guards remain mandatory. No live model evaluation was
performed for this change. `requests.json` retains bounded failure diagnostics.

## CPU and UI use

Select **Reactive incidents · calls & crew updates** in the existing UI. The
Reports panel shows outstanding calls and received history; Resources shows ETAs.
Import/export uses the same API and exact replay, including incident events.

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_incident_smoke \
  --prepared-inputs PATH/inputs.json --prepared-sha256 PREPARED_CONTENT_HASH \
  --topographic-inputs PATH/domain.json --topographic-sha256 DOMAIN_CONTENT_HASH \
  --output .runtime/reactive-incident-smoke
PYTHONPATH=envs/wildfire_response/core/src python -m unittest discover \
  -s tests -p 'test_incident_events.py' -v
```

The smoke compares passive, immediate and twelve-minute-delayed protection on
three development fixtures, plus a reconnaissance-only shortage probe. It records
exposure, support requests, outcomes, elapsed time and memory with one CPU worker.
Compressed public trajectories and separate private verifier artifacts stay in
ignored runtime storage. These checks validate mechanics, not model competence.

Remaining work includes calibration of travel/spread/exposure scales, additional
resource-scarcity scenarios and a separately versioned two-hour handover objective
that can credit controlled residual fire. This change does not silently redefine
extinction or regrade the earlier hosted evaluation.
