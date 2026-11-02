# Observer-only evaluation revalidation

`eval/revalidate_evaluation.py` is a separate, private recorded-call replay tool.
It does not invoke a scripted policy, change scenario definitions or update the
source evaluation. Have the code and observer correction reviewed before running
it. The running baseline may continue under its original frozen validator.

The tool requires the original report, source freeze, unchanged package, and the
explicitly approved SHA256 of the corrected `eval/incident_day_movement_checks.py`.
The source freeze supplies the full source Git commit. Tracked production files
must match that commit except for the observer; the new revalidator and tests/docs
are permitted additions. The source validator digest is reconstructed using the
original observer bytes and must match the freeze. Engine, lifecycle, reward,
package-manifest and frozen evaluation-row identities must also match.

## Snapshot and replay

On the first invocation, choose a new output directory outside the source report
and package directories. The tool copies the exact report and source freeze,
and every finished cell's private gzip evidence referenced by that report, into
`source/`. `source_manifest.json` records all their SHA256 hashes, both validator
identities, the reviewed observer delta, and the revalidator hash. Files are
private (0600) and directories 0700. Live checkpoints are not consumed.

A report snapshot taken before the baseline ends contains only the cells already
finished at that generation. A later resume uses that same snapshot, even if the
live report advances; create a separate output for a newer source snapshot.
A partial snapshot cannot produce a complete 48-cell acceptance report.

For each captured finished cell, the tool:

1. Resets the explicit task with its frozen seed and binds the complete scenario
   digest and evaluation configuration to the frozen task row and saved evidence.
2. Executes every journal call in order through `PublicFacade` / `IncidentDayTools`,
   comparing the before/after clocks, decision counters and retained result fields.
3. Compares the complete initial observation, every committed transition and its
   observation digest, the entire query/rejected-call log, minute audit, reports,
   orders and production outcome with the original evidence. Missing or changed
   evidence is a replay failure.
4. Independently recomputes reward and compares it with the original independent
   result and the production outcome using the existing reward tolerances.
5. Calls the existing runner's `finish_cell` with the fresh corrected observer and
   a verification provider backed by the successful comparisons above. This avoids
   `verify_episode` doing a redundant second full-day replay. The existing strict
   invariant, coverage, recovery and scoring gates remain in force.

The journal retains canonical hashes for committed observations and route-query
results; it does not retain every read response in full. The report states this
comparison limit. Original successful exact-policy continuation and query-
independence checks are linked by source snapshot and evidence hashes and marked
as inherited, not rerun. No policy-based continuation helper is called. Fresh
recorded-call checkpoint checks are not claimed by this version.

## Run after review

```bash
# Record and review this exact corrected observer hash first.
sha256sum eval/incident_day_movement_checks.py
python -m eval.revalidate_evaluation \
  --source-report /var/tmp/wildfire-evaluation-baselines-v2/report.json \
  --source-freeze /var/tmp/wildfire-evaluation-freeze-v2.json \
  --data-dir /var/tmp/wildfire-evaluation-pack-v1 \
  --reviewed-observer-sha256 APPROVED_OBSERVER_SHA256 \
  --output /var/tmp/my-evaluation-revalidation --wall-seconds 1800
```

One CPU worker executes the replay. `--max-cells` bounds newly replayed cells;
`--max-calls` bounds replayed journal calls in an invocation. Wall time and
SIGINT/SIGTERM are checked between atomic operations, including calls and final
verification/audit work. An operation in progress may exceed the requested wall
budget. An interrupted cell restarts from its recorded reset on resume; durable
finished cells are reused without running them again:

```bash
python -m eval.revalidate_evaluation \
  --source-report /var/tmp/wildfire-evaluation-baselines-v2/report.json \
  --source-freeze /var/tmp/wildfire-evaluation-freeze-v2.json \
  --data-dir /var/tmp/wildfire-evaluation-pack-v1 \
  --reviewed-observer-sha256 APPROVED_OBSERVER_SHA256 \
  --output /var/tmp/my-evaluation-revalidation --wall-seconds 1800 --resume
```

`cells/` contains immutable, checksummed per-cell comparison results, original
and corrected findings, and corrected observer evidence. The new `report.json`
records those artifact hashes and links the source snapshot. Original execution
time is labeled separately from replay cost. Existing source artifacts are never
rewritten, and a new invocation refuses an existing output unless `--resume` is
explicit. A source mismatch stops revalidation; no result is silently accepted.

The default `--adverse-outcome-policy withhold` preserves strict score withholding.
Opt in with `--adverse-outcome-policy score` on the first invocation and every
resume, using a new output directory for that policy choice. It affects only the
separate reporting layer: `reported_score` can retain the unchanged raw reward with
`operational_status: adverse`. The strict `status`, `score` and findings produced
by `finish_cell` remain unchanged; source status/score and original findings are
also retained. No reward formula or source validator is changed.

This reporting exception is limited to detailed findings whose sole code is
`recovery_after_shift_end_egress_unavailable`, all categorized unsupported. It
requires exact replay, independent reward agreement, complete invariant/coverage
evidence and all observed ticks, a resource-specific pre-deadline unavailable-
egress report, a stationary evidenced wait (including current-tick preflights),
no work/reassignment after shift end, and recorded arrival at base off duty by
the horizon. Other unsupported findings, contradictions, missing coverage or
unrecovered resources cannot receive a reported score through this option.

The aggregate `status` remains the strict validator status. A separate
`reporting_status` may be `complete_with_adverse_outcomes` only when the refreshed
strict aggregate is exactly `blocked`, every cell is either strict-complete or
has the exact permitted adverse proof, and aggregate required coverage is
satisfied. A strict `failed` or `incomplete` result is never relabeled, including
when a stored `complete` cell fails verification during aggregate refresh. Resume requires the original reporting-policy
choice; use a new output to choose differently.

These remain evaluation-only synthetic configurations sharing development
landscapes and parent seeds; revalidation establishes neither independent
held-out incidents nor calibration.
