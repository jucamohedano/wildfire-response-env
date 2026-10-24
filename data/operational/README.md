# Operational source audit v1

The agreed comparison now uses actual downloaded records, not just catalog
descriptions. See the [HTML findings](../../results/operational-source-audit-v1.html)
and [versioned evidence manifest](source_audit_v1.json).

## Inputs and selection

- EGIF public XML export: Spain, 2021, 8,206 returned records, 2.1 MB compressed.
  Select six Almería, eight Huelva and six Madrid incidents by lowest SHA256 ID
  rank. The sample proved to contain only small fires, so inspect all 18 exported
  records with final forest area over 500 ha as a separate evaluator stratum.
  This is not a claim that only 18 large fires occurred nationally.
- ICS-209-PLUS v3 wildfire archive: 48.7 MB compressed. Select five 2020 wildfire
  incidents each in CA, CO, OR and WA by lowest SHA256 ID rank. Extract all 213
  associated reports and check counts against the published incident summaries.
  Complete extraction does not establish complete or accurate incident reporting.

The EGIF XML is streamed; the ICS ZIP is not extracted to disk. Only explicitly
allowlisted fields enter the local selected JSONL files. Names, cause details,
commander narratives and free-form observations are not exported. Missing values
stay null, not zero. No records enter agent observations, demonstration labels or
train/validation/test sets. Final-area selection remains evaluator-only.

The first geographic request failed to constrain the intended province; it was
not used for a provincial coverage claim. EGIF uses its own community codes:
Andalucía is 4, not the INE community code. Province IDs were checked against the
official dropdowns; the successful export was deliberately nationwide for 2021.

## Reproduce offline

Place the two original ZIPs in `.runtime/operational-source-inputs/`. Their exact
byte hashes and source URLs are in the evidence manifest. EGIF exports are
generated dynamically: preserve original bytes, since a future export or data
revision can change the archive hash. The US publisher's MD5 was also checked.

```sh
python eval/audit_operational_data.py \
  --ics-zip .runtime/operational-source-inputs/ics209plus-wildfire.zip \
  --egif-zip .runtime/operational-source-inputs/egif-spain-2021.zip \
  --output .runtime/operational-source-audit-new
PYTHONPATH=envs/wildfire_response/core/src python -m unittest discover -s tests -q
```

Use a fresh output directory. `audit.json` records input hashes, per-field
presence/missing/zero counts, measured elapsed time and selected-file hashes.
Selected records are in `private/`. Their current combined size is 150,602 bytes.
The measured audit took 5.601 seconds and 22,940 KiB peak RSS on one CPU process;
this excludes network downloads and does not measure simulator decision speed.

To reacquire EGIF, use its public search for year 2021 through 2021, then XML
export. The chapter mask in the evidence manifest selects location, times,
detection, conditions, propagation, resources, techniques, losses and incidents;
it omits causes, observations and annexes. Confirm the returned year/count before
using it. This audit does not claim 2026 consolidated coverage. Reacquiring a
changed source is a new evidence collection, not an exact-byte replay.

## Decision and next scope

Keep Spain as the primary setting. EGIF supports more than our press-release pack:
first-response milestones and aggregate resource categories are concrete inputs
for scenario calibration. Use the large-fire stratum before fitting large-incident
assumptions; do not calibrate those from the tiny-fire geographic sample.

Use ICS-209 as a separate reference for repeated situation-update structure and
missing-data tests, not as Spanish dispatch doctrine or a source of individual
conversations. Its report cadence cannot supply minute-scale receipt delays.

Next, decode EGIF resource/incidence code lists and validate temporal order and
coordinate systems. Join independently timed weather only where justified.
Then build a small new development pack covering intake, arrival monitoring,
escalation and resource/protection coordination. All missing dispatch queues,
readbacks, movement and suppression effects must remain explicit simulator
assumptions with tested state effects. Historical final outcomes must never force
the future of a counterfactual rollout. The audit sampler itself creates no tasks.
The follow-on [Spanish development bridge](../../envs/wildfire_response/core/src/envs/wildfire_response/regional/grounded/README.md)
now implements a small opt-in pack; it does not replace this source audit or claim
to have completed the remaining geographic, weather and protection-mechanic gates.

## Rights and retention

ICS-209-PLUS v3 metadata declares CC BY 4.0; retain dataset attribution and DOI.
No EGIF redistribution licence was verified in this pass. Both raw inputs and all
selected records remain local evaluator artifacts, not public dataset uploads.
The repository's existing root-code/asset licence restrictions remain unresolved.
Ignored `.runtime/` is not backed up by Git: retain the original archives and
private samples separately before cleaning a temporary checkout.
