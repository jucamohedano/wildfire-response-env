# Broader control development smoke

Run on 2026-10-23, one CPU worker, six control fixtures and five policies, without
model inference or external APIs. This checks the versioned programmatic rubric;
it does not measure generalisation or establish an optimal firefighting policy.

## Reproduce

Use Python 3.12 with the core installed, or set its source path:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --output .runtime/control-objectives-smoke-v3
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.ops.run \
  --replay .runtime/control-objectives-smoke-v3/control-001/control/trajectory.json.gz
```

The output directory must be fresh. Default policies are passive, constrained
random-valid, arrival-only dispatch, full control, and blanket warning. The
default task set is `control`; `--task-set dispatch --policy dispatch` runs the
separate arrival practice fixtures.

Contract: `wildfire-ops-control-center-v3`, `wildfire-ops-task-pack-v0.3.0`,
`wildfire-ops-evidence-v1`, `wildfire-ops-readback-v1`, and
`wildfire-ops-objective-fraction-v2`. Simulator: `regional-response-v2-alpha.2`.
Canonical scenario seeds are 30018, 30001, 30020, 30003, 30022 and 30023.

## Results and interpretation

- All 30 episodes passed public replay and core outcome/accounting verification.
  No policy produced an invalid action.
- **Control:** all four objectives passed in all six cases; all episodes naturally
  terminated, all fires resolved, and no population was exposed. Total 145 decisions.
- **Arrival-only dispatch:** no full-control passes. Five cases still had active
  fire at the horizon, none satisfied evidence triage, and the urgent growing-fire
  case exposed population. Scores were 0.50, 0.25, 0.75, 0.50, 0.50 and 0.50.
- **Passive:** the same scores as arrival-only dispatch. Only the false alarm
  naturally terminated; it earned safety, resolution and communication credit
  but failed triage. Five of six cases remained unexposed despite waiting, showing
  the limited urgency coverage of this small development set.
- **Blanket warning:** population safety passed all six cases, but communication
  discipline failed all six because unrelated districts were also evacuated.
  Scores were 0.25, 0.25, 0.50, 0.25, 0.25 and 0.25.
- **Random-valid:** all four objectives passed all six cases, using 177 decisions.
  This policy samples valid subsets of the informed control baseline's candidates;
  it already has that baseline's investigation, suppression and notice heuristics.
  Its result shows tolerance of action delays, not uninformed random competence.

The run took 14.853527 seconds including replay, accounting and artifact writes,
with 1,279 decisions total and 36,646,912 bytes (34.9 MiB) process peak resident
memory. This is a local process high-water mark including sequential episodes
and verification, not an inference or GPU memory measurement.

Regression validation ran 171 core tests successfully with 9 skipped, plus
3 root extraction tests. The core wheel built and installed into an isolated
directory. An external client replayed the recorded urgent case and completed
a new episode with all four objectives met and no active fire remaining.

## Sensitivity and acceptance checks

The automated control checks additionally establish:

- Early evacuation in the urgent case avoids population exposure that occurs
  when waiting; the population-safety component changes accordingly.
- Investigating the false alarm without evacuation earns more than blanket
  evacuation, while reasonable warnings based on a still-unconfirmed local
  report are not retroactively penalised for the report proving unfounded.
- Repeated notices cannot increase reward and fail communication discipline.
- Stale sensor readings, future/unreceived report references and contrary
  assessments are rejected; invalid combined actions cannot partially save triage.
- Linked duplicates require matching location and observation time.
- Control outcomes earn no intermediate credit or repeated completion credit;
  early invalid-action abandonment cannot collect quiet-outcome points.
- Altering assessment evidence times, public observations, or recorded rewards
  fails replay verification. Hidden fire counts and future schedules are absent
  from the agent's initial observation.

## Remaining gates

These fixtures reuse existing training configurations. Keep them as development
checks; create new incident-grouped held-out cases before model selection. The
constrained random policy's perfect score and five quiet-at-home cases mean this
set is still insufficient to test priority-setting under pressure. Add varying
resource shortages, deadlines, delivery failures and noise, then test multiple
seeds without selecting them merely to defeat a baseline.

The equal four-goal fraction is a transparent coverage rubric, not a calibrated
safety utility. Always report population safety and every other component;
partial credit can coexist with an unsafe outcome. Domain calibration and tested
safety constraints are needed before using the scalar alone for policy selection.
Burned terrain and resource effort remain diagnostics. See [OBJECTIVES.md](OBJECTIVES.md).

## Recorded hashes

- Task manifest: `de6ff8e83389c3ee48189575b6d1bad6ea3eddc65ba7367d1dda661b85aebe63`.
- Core pack: `db20dc7806eac7d5b0b07cffd06931c9d40120f0813bd8b6fb80ff1d13f6bcf0`.
- Source: `1e4017a404c15ddcac4eda1b95f6bc8401bd99df23f18c0fb40bdec31a52dc60`.
