# Wildfire benchmark v1

The benchmark has 120 synthetic configuration groups on one prepared Málaga
landscape: 80 train, 20 validation and 20 test. Two seeds/wording variants stay
together in each group. Twelve development probes are separate. Families cover
uncertain smoke, false alarms, conflicting/forwarded reports, wind changes,
overlapping fires, reinforcement delays, unavailable ground resources and failed
protection delivery. Some conflicting-report cases put fire on an evacuation
path, so blanket evacuation is not a universally safe reference.

`manifest.json` lists only public task IDs and splits. `catalog_lock.json` pins
the private compiler output and prepared inputs. `calibration.json` records
declared profiles and source limitations. Full factors, future events and
evaluators belong in ignored runtime storage, not actor prompts or retrieval.

The central profile keeps existing assumptions. Faster/stronger and slower/weaker
profiles multiply the assumed ground-transit base by0.75/1.5 and suppression
probability by1.5/0.5 (clipped at one). Mobilization and base offsets remain
separate. These are sensitivity experiments, not fitted uncertainty intervals.
The current source clock represents night response; no aircraft-effectiveness
claim follows from this catalog.

## Evaluation

`python -m eval.run_benchmark` runs sequential CPU references and writes canonical
compressed trajectories, private minute audits, independent verification,
`summary.json` and `report.html`. Start with `--split development`; optional
`--communications-ablation` compares the same dispatcher with incoming calls,
reports and population smoke telemetry removed. Sensor evidence and operational
acknowledgements remain. This is an explicit information ablation, not a second
environment reward.

For validation/test, first use `--register-only --output experiment.json`, then
run the identical selection with `--registration experiment.json`. Registration
pins task IDs, profile, catalog, implementation and policy/runner source hashes.
Any changed policy requires a new registered experiment. Test outcomes must not
be used to tune policies or training. A limited split, incomplete matrix,
censored case or abandoned policy cannot qualify for a full benchmark ranking.
Ranked runs require60two-minute decisions; the120one-minute diagnostic mode
cannot enter a ranked experiment. The frozen evaluation's428trajectory headers
were checked against this cadence. Its earlier recorded code hashes remain the
source of truth; this later guard does not change or regrade those outcomes.

Headline ranking prioritizes zero-exposure completion, then protected population,
then lower additional burned footprint. The separate training score is80%
unexposed population and20% unburned footprint. Whole-cell footprint is measured
once, relative to the initially burnable and unburned domain. It is not fuel
consumption, a measured fire perimeter, or a mortality model.

References: passive; indiscriminate evacuation; cooperative protection/dispatch;
and a public dispatcher using route sightings, ETAs and support requests. They
use delivered evidence only. They are heuristics, not proven optimal planners.
Bootstrap intervals resample configuration groups, keeping sibling seeds together.

## Reproducible commands

Provide the prepared-input and topographic paths and their canonical content
hashes using `--prepared-inputs`, `--prepared-sha256`, `--topographic-inputs`, and
`--topographic-sha256`. These four arguments are abbreviated as INPUT_ARGS below.

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_benchmark INPUT_ARGS \
  --split development --communications-ablation --output .runtime/benchmark-dev
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_benchmark INPUT_ARGS \
  --split validation --register-only --output .runtime/validation-experiment.json
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_benchmark INPUT_ARGS \
  --split validation --registration .runtime/validation-experiment.json \
  --output .runtime/benchmark-validation
```

The default CPU wall budget is30minutes, adjustable up to four hours. Stopping
preserves completed results and a separately labelled partial trajectory; it does
not fabricate a complete leaderboard. No inference key, GPU or network is needed.
