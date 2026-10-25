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

## External context acquisition pilot

The [context source catalog](context_sources_v1.json) records official weather,
terrain, land-cover, population, road and response-capability sources, together
with access constraints and the next integration gates. This is a separate data
acquisition increment; it does not change simulator observations or scores.

On 2026-10-25, three bounded DERA WFS extracts downloaded 495,274 payload bytes:
20 public fire-station locations, two emergency coordination sites and 1,058 road
features. Eleven returned fire stations have Huelva province labels; the broad
selection also includes neighboring provinces. The coordination sites are 112
and maritime coordination, not identified CEDEFO bases or available fire crews.
Hashes, exact requests, counts, unique feature IDs, returned CRS and finite
coordinate bounds were checked. No contact details were requested or retained.
Two returned station points lie outside the requested bbox, so service spatial
selection must be checked and filtered locally before any exact geographic join.
Road geometries are not clipped and do not yet form a validated routing graph.

Raw GeoJSON and the acquisition manifest are retained locally under
`.runtime/context-acquisition-v1/`; they are not committed. The tracked catalog
records their hashes and request URLs. Current service snapshots do not establish
the same facilities or roads existed during a 2021 incident. No existing anchor
has a verified geographic join, and no new terrain or weather file was acquired.
Weather API access and historical hourly station coverage remain separate gates;
ERA5-Land is a possible hourly reanalysis source, not historically available
operational weather. Download small incident-area/window subsets once location
and timezone are verified, then cache derived packs for CPU-only episode runs.

## Sierra Bermeja, Málaga: aligned 2021 evidence bundle

The next pilot is one incident, not all Málaga fires: Sierra Bermeja, detected
8 September 2021. Its [source lock](../malaga_2021/sources.lock.json),
[curated claims](../malaga_2021/claims.json),
[population provenance](../malaga_2021/population_sources.json) and
[weather request](../malaga_2021/era5_request.json) keep different evidence roles
and clocks separate. No Huelva arena or 2026 roads are substituted as its history.

Implemented preparation:

- Download and pin five CEMS EMSR545 vector packages, the technical report,
  an institutional final-extinction update and supporting map metadata. Preserve
  source hashes and each feature's reference/damage-source identifiers. The
  satellite source tables provide explicit UTC acquisition clocks; webpage
  delivery times do not establish a verified timezone or actual actor receipt.
- Match exactly one existing EGIF record by Málaga province and detection time,
  then independently check its control and final-extinction milestones. Preserve
  its final forest area separately from the technical report's affected area.
- Export CRS84 map layers and selected reference context without contact fields.
  The revised grading product omits a CRS member: RFC7946 defaults are accepted
  only after independently checking its accompanying WGS84 PRJ. This is not
  implicit conversion of metre coordinates or a topology/routing certificate.
- Extract seven official municipal population totals from the January 2021 INE
  reference period. Their release followed the incident on 23 December 2021.
  They remain evaluator context, not historically available population telemetry,
  people present, at-risk counts or inferred evacuation demand.
- Acquire a 25 m terrain grid covering the union of the five mapped areas and
  fire polygons. Check every retained AOI/fire vertex against the grid in UTM30N,
  with explicit longitude/latitude axis order and an independently pinned grid.
  This checks horizontal coverage only: survey epoch, vertical units/datum and
  absent nodata declarations remain unresolved.
- Retain 18 structured retrospective claims with source locators, separate
  local-clock interpretations and null receipt times. A raw Red Cross HTTP
  download returned 403; its public chronology was read through web research,
  but these curated claims are explicitly not raw-snapshot verified.

Five discrepancies stay visible rather than being repaired: EGIF versus report
area definitions, conflicting responder extinction wording, the CEMS map's event
clock, the first daily-area table's date, and delivery/revision availability.
The technical PDF describes the 2021 incident but was created in 2022; creation
does not establish publication. Reference layers also carry their own vintages,
including CLC2018 and an OSM snapshot labelled 9 September 2021. No historical
outcome forces the future of a simulation, and no record is currently eligible
as an actor observation, optimal-action label or training demonstration.

The main builder uses the standard library. The optional cross-CRS check uses
the separate, tested data-only dependency; it is not added to the demo/core:

```sh
uv venv .runtime/malaga-data-venv
uv pip install --python .runtime/malaga-data-venv/bin/python -r data/malaga_2021/requirements.txt
python -m eval.prepare_malaga_2021 \
  --inputs .runtime/malaga-2021-inputs \
  --egif-zip .runtime/malaga-2021-inputs/egif-spain-2021.zip \
  --output .runtime/malaga-2021-prepared
.runtime/malaga-data-venv/bin/python -m eval.check_malaga_geography \
  .runtime/malaga-2021-prepared
PYTHONPATH=envs/wildfire_response/core/src python -m unittest discover -s tests -q
```

Inputs must have the exact locked filenames/hashes; preserve original bytes.
Use a fresh output directory. Reacquisition from a changed current service is a
new evidence collection, not an exact-byte rebuild. `audit.json` excludes timing
from the deterministic content fingerprint. Private source IDs and evaluator
artifacts are separate from the manifest; observation privacy is not an OS
sandbox. Raw evidence and prepared artifacts remain in ignored local storage.

Remaining sequence before simulator integration:

1. Acquire the small, prepared 8–15 September hourly ERA5-Land window after CDS
   access and dataset terms are confirmed. Retain reanalysis status, missingness,
   physical units and accumulation semantics; interpolation cannot create minute
   gust observations. Do not invent historical publication/receipt timestamps.
2. Resolve or explicitly bound clock/area conflicts, terrain metadata, actual
   exposed settlement populations, road accessibility, fuel assumptions and
   initial unit readiness. These are not supplied by municipal totals or a map.
3. Define bounded decision windows without compressing six days into an existing
   two-hour episode. Build separately versioned initial-state, observation and
   scoring adapters; final perimeters and later reports cannot be early evidence.
4. Run CPU replay, visibility/leakage checks and baseline comparisons before
   model training. Keep incident groups separate for future held-out evaluation;
   wording/weather variants of this one fire do not create independent incidents.

## Rights and retention

ICS-209-PLUS v3 metadata declares CC BY 4.0; retain dataset attribution and DOI.
No EGIF redistribution licence was verified in this pass. Both raw inputs and all
selected records remain local evaluator artifacts, not public dataset uploads.
The repository's existing root-code/asset licence restrictions remain unresolved.
Ignored `.runtime/` is not backed up by Git: retain the original archives and
private samples separately before cleaning a temporary checkout.
