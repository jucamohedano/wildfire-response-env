# Broader control objectives

Ops interface v4, task pack v0.4.0, evidence contract v2, reward
`wildfire-ops-objective-fraction-v3`. The four numerical fractions are unchanged;
acknowledged failed-delivery retries are now distinguished from duplicate notices.

`control-000` through `control-005` assess the control center over an episode.
Their public briefs specify the following four objectives, without prescribing
report locations, dispatch targets, a particular resource, or an evacuation plan.

## Verifiable conditions

- **Evidence triage:** every received report with a location has a conclusive
  assessment grounded in public sensor evidence. `triage_reports` takes records
  containing `report_ids` and `assessment`. `fire_observed` and
  `no_fire_observed` must match a sensor reading at that location, observed no
  earlier than the report's observation time and at most four model minutes ago.
  `defer` needs no sensor reading and earns no resolution credit. Unlocated
  messages do not count as resolved fire claims; they remain available for later
  handling. Obtaining locations through follow-up calls is outside this release.
  Reports can be linked as duplicates only when their locations and
  observation times match. Source role or private generation labels cannot
  establish correctness.
- **Population safety:** no district has any exposed people and no population
  movement remains evacuating or blocked at episode end. Protection through
  early containment or staying safely at home is allowed as an alternative to
  evacuation. Sending or delivering an instruction is insufficient by itself.
- **Incident resolution:** no fire is active after every scheduled incident and
  incoming report has occurred. The verifier replays the seeded simulator and
  inspects its actual state; the agent does not receive hidden fire counts or
  future ignition schedules. Dispatch, arrival, and water-drop counts cannot
  substitute for resolution.
- **Communication discipline:** no redundant notice with the same recipient and
  purpose, and no unsupported evacuation order. A retry after an acknowledged
  failed delivery is allowed; repeating while pending or after delivery is redundant.
  Support is assessed from public
  information when the instruction was issued: fresh observed fire or a report
  not cleared by fresh sensors within six grid cells of the district home.
  A reasonable warning based on an uncertain report remains supported if the
  report later proves unfounded. Informing a district does not initiate movement.

The four-minute freshness and six-cell support margins are versioned synthetic
exercise assumptions, not measured operational thresholds or official doctrine.
Assessing `no_fire_observed` describes the sensor reading at its observation time;
it does not label the original report as false or guarantee that no fire exists
elsewhere.

## Reward and completion

For these control tasks all four conditions are evaluated only at episode end.
Every scheduled incident/report and queued core delivery must have occurred;
three-invalid-action abandonment cannot collect quiet-outcome credit. Each
objective contributes exactly one quarter of the total reward and counts once.

`episode reward = completed objectives / 4`

Intermediate control-task reward is zero. Public trajectories record rewards and
completed objective IDs; verification results and episode summaries report the
component flags. Separate evaluator artifacts contain hidden-state checks.
Replay reconstructs the public assessments and notice audit before
recomputing the score from simulator state. Altered observations or rewards are
rejected. The original weighted regional benchmark remains separately available.

This is an explicit, equal-coverage engineering rubric. A score of 0.75 can still
include a failed population-safety objective; it must not be interpreted as a
calibrated safety utility. Evaluation reports must show each component and
`all_objectives_met`, with population exposure and burned terrain separately.
Before real policy selection, calibrate outcome priorities and hard safety
constraints with domain input rather than treating the fraction as sufficient.

## Task coverage and controls

The six control fixtures cover local smoke, growing fire, false alarm, multiple
incidents, unavailable aircraft, and a wind shift. Urgent families use the existing
training configurations `regional-train-001` and `regional-train-003`, where
waiting can expose population. Other families reuse noisy training configurations
`regional-train-018`, `020`, `022`, and `023`, with duplicate, conflicting, delayed,
and unlocated reports. These are development fixtures drawn from existing
training configurations, not a new held-out split or historical incident replay.

The six `ops-*` arrival-only fixtures remain in the `dispatch` task set for tool
and protocol practice. Their score cannot be compared directly with control-task
scores. The API and rollout CLI default to the broader control task set.
The [generated dataset](DATASET.md) adds 120 grouped configurations and 360 seed
variants with separate train/validation/test membership and a pinned catalog.

The deterministic control baseline uses public observations only: it requests
resources for reported/observed sectors, corrects and confirms readbacks, lets
on-scene sensors verify fire before suppression, records assessments, sends
targeted warnings, and releases units after local fire clears. Its heuristic
warns immediately on unconfirmed reports within three cells, or fresh observed
fire within six; otherwise it investigates first. These choices are reproducible
baseline assumptions, not an optimal policy. The random-valid control samples
subsets of this informed baseline's candidate actions.

Required regression checks include timely warning versus waiting in an urgent
case; restraint versus blanket evacuation in a false alarm; no reward gain from
duplicate notices; no full control credit for arrival-only dispatch; atomic
invalid triage; stale evidence rejection; and replay/tamper checks.

This release does not certify a real-world strategy, minimum fire damage, an
optimal allocation, or robustness to unseen incidents. Burned terrain and effort
remain explicit diagnostics. New incident-grouped held-out tasks, broader noise
and delivery failures, and calibration across multiple seeds remain prerequisites
for model selection or a training claim. Terminal rewards may also need a tested
training curriculum; the environment does not silently add process rewards.
