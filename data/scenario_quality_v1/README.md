# Synthetic scenario quality rules v1

**A replayable simulator is not necessarily a realistic exercise.** These checks
separate internal consistency from plausibility and empirical calibration. They
are evaluator-side checks, not extra observations or hints for the agent.

## Three kinds of result

- **Error / reject:** contradictory or unsupported configuration. Do not export
  runnable expansion tasks until fixed in a separately versioned dataset.
- **Warning / review:** a possible situation that may fail its intended training
  objective or needs explicit assumptions. Do not silently delete it, relabel it,
  count it as realistic, or tune it using held-out policy performance.
- **Information:** deliberate constraints such as night-grounded aircraft.
  Readiness is not permission to fly and is not extra usable capability.

`contract_passed` only means no hard error was found. `review_required` remains
true while warnings are unresolved. `empirically_validated` is always false in
this static audit: a code check cannot establish real wildfire accuracy.

## What every concrete scenario is checked for

1. **Identity and provenance:** matching task/scenario IDs and nonnegative seeds,
   explicit synthetic generation, valid report identities and readback settings.
2. **Causal clocks:** integer event minutes inside the response window;
   observation no later than receipt; no negative dispatch or communication delay.
   Daylight and weather/incident clocks must agree; contradictory daylight events
   cannot pretend to override the grounded clock.
3. **Geography and terrain:** representable locations; ignitions on recognized,
   supported fuel and height, outside the two-cell censor guard. Population homes
   and refuges must exist and differ. Unknown elevation is never flattened.
4. **Operational parameters:** engine-supported finite winds (each component
   between -3 and 3, in model units—not m/s); positive travel/mobilization,
   meaningful crew/water inventories, known bases/resources, valid clarification
   references and internally consistent shelter-dose parameters.
5. **Family intent:** false alarms have no scheduled ignition; multiple fires
   have distinct sites; conflicting locations really differ; wind-shift and
   delivery-failure families contain those events. Delayed/held-resource families
   contain actual delays/holds, not just a label.
   Standalone checks need the declared family metadata; they do not infer intent
   or truth from free-text reports or a generic internal scenario label.
6. **Duplicate lineage:** forwards from the frozen generators retain their
   original source, role, observation time and location; wording and channel may
   change. This uses generator conventions, not an LLM judging text. New generator
   formats need an explicit lineage contract before joining the registry.
7. **Integrity across variants:** canonical hashes, unique IDs/seeds/groups,
   variant and split counts. Expansion layouts cannot cross splits. Older catalogs
   retain their declared configuration-only split contract; they do not gain a
   geographic holdout claim retroactively.
8. **Decision opportunity:** flag pre-ignition-only wind changes in wind-shift
   exercises, fires within six minutes of cutoff, ground arrivals outside the
   horizon even under optimistic timing, and settlements/refuges burning at time
   zero. These are warnings, not proofs that a rescue/protection task is unsolvable.

Incorrect reports, conflicting witnesses, false alarms, scarce resources and bad
weather are allowed. A resident's role does not prove correctness; the validator
does **not** require every claim to match the evaluator's hidden fire state.
Six minutes is an explicit exercise-review heuristic, not a firefighting standard.

## Scope and use

The registry checks **all 2,412 concrete cases**: benchmark v1 (252 including
development), expansion v1 (1,920), and topographic stress v1 (240). It does not
claim to inspect every archived API development mode or verify arbitrary action
trajectories. Existing seeded replay, privacy and reward tests remain necessary.
The audit compiles inputs only: **zero policy rollouts and no held-out outcomes**.

```bash
export PYTHONPATH=envs/wildfire_response/core/src
python -m eval.validate_scenarios \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --output .runtime/scenario-quality-v1
```

Use a fresh directory. `--catalog` selects a registered catalog;
`--fail-on-warnings` makes unresolved review items fail the CLI as well as errors.
Expansion compilation now runs the hard-error gate before writing its runnable
manifest; warnings stay visible in its quality summary and provenance. Historical
catalogs, rewards, scores and engine/replay identities are not rewritten.

`public/summary.json` and `public/report.html` contain aggregate counts, limitations,
input/catalog/rule hashes and measurements. `private/<catalog>/findings.json`
contains per-case evaluator findings and must not enter prompts or retrieval
banks. Only serve the public directory, never the whole audit root.

## First source-grounded audit, 27 October 2026

All 2,412 registered cases passed the hard checks. **32 variants / 16
configuration groups need intent review:** 28 wind-shift variants change wind at
or before ignition (26 at, 2 before), and four variants ignite a refuge at time
zero. A threatened refuge can be an intentional challenge; it is not automatically
an impossible or incorrect scenario.

The same landscape also has **80 cells using assumed fuel fallback**. That shared
warning appears once per catalog, so the report totals 35 warning instances—not
35 distinct bad scenarios. Night-grounded aircraft are informational, not errors.
The CPU-only audit took 12.732 seconds and peaked at 35.22 MiB RSS. No scenario,
seed, split, reward or existing replay identity was changed.

See the [HTML report](../../results/scenario-quality-20261027.html) and
[machine-readable audit](../../results/scenario-quality-20261027.json). All
plausibility warnings remain unresolved until explicitly reviewed or addressed
in a new dataset version; static acceptance is not a realistic-release approval.

## Acting on findings

Review against the declared objective first. A refuge threatened at incident
start may be a deliberate unsafe-evacuation exercise; a pre-ignition weather shift
may be a valid weather scenario but not an in-progress adaptation test. Record
the rule/hash, case or shared-input identifier, reviewer, rationale and disposition
in a private curation ledger: retain as an explicit corner case, redesign in the
next dataset version, or defer. Do not fabricate a human review or automatically
force every task to be easy or winnable by one baseline.

Real-world fidelity still needs representative weather/fuel evidence, realistic
roads/dispatch/capacity assumptions and domain review. Unknown fuel fallback,
coastal weather proxy and uncalibrated suppression/dose models remain declared
limits; more random seeds do not fix them. These checks do not replace issue #27's
reward analysis or issue #34's longer-session acceptance work.

This separation follows the exercise-design principle of testing declared
capabilities with credible scenarios, not mistaking a simulation for an incident
reconstruction. See [FEMA HSEEP](https://training.fema.gov/programs/nsec/hseep/)
and [UK government exercise guidance](https://www.gov.uk/government/publications/exercising-best-practice-guidance/exercising-best-practice-guidance-html).
They motivate the process; our code thresholds are not certified operational rules.
