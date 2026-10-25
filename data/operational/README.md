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

## Opt-in Málaga initial-response bridge

The episode compiler now creates a small, separate configuration from an
independently retained prepared-bundle fingerprint. It hashes every prepared
content file, not the mutable timing/audit report, before copying only the study
envelope, detection clock and an explicitly assumed transit decomposition. It
does not copy historical fire polygons, later reports, population totals or final
outcomes. Changes to even excluded private evidence still invalidate the trusted
bundle fingerprint. Keep the compiled configuration outside the evidence bundle.

The new mode requires `allow_synthetic_conditions=True`. Missing historical
weather is not silently replaced under a historical-replay label. Three variants
share one incident group and a 120-model-minute horizon, starting at the source
local detection clock. They are initial-response contract tests, not a compressed
six-day replay, held-out set, imitation demonstrations or calibrated fire model.

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.prepare_malaga_episode \
  --bundle .runtime/malaga-2021-prepared \
  --expected-sha256 05fb6bce8b91a1c6e10d09328ae5548c9160e937053483873f6d7595748afe0f \
  --output .runtime/malaga-episode/episode.json
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.malaga.run \
  --pack .runtime/malaga-episode/episode.json \
  --output .runtime/malaga-rollouts --allow-synthetic-conditions --repeat
```

The fingerprint identifies the previously prepared exact-byte bundle, not a
signature or permission to trust arbitrary caller-supplied configurations. An
updated evidence collection requires its own reviewed fingerprint. The compiler
is a data-preparation script; the environment itself is available from an
installed core wheel with no data/GPU/model dependencies:

```python
from wildfire_core.malaga import MalagaDevelopmentEnv, ProtectionBaseline

env = MalagaDevelopmentEnv("episode.json", allow_synthetic_conditions=True)
observation, info = env.reset("malaga-dev-v0")
policy = ProtectionBaseline(prefer_shelter=False)
while True:
    observation, reward, terminated, truncated, info = env.step(policy(observation))
    if terminated or truncated:
        break
verification = env.verify_episode()
```

`get_situation`, report queries, action checks, requests, offer/readback handling,
authorization, triage, releases and versioned protection orders use the existing
public interface. Public trajectories bind both the episode configuration and
active runtime pack and support `verify_malaga_trajectory(trace, episode_pack)`.
The old protection/Spanish modes retain their default pack and exact replay.

Only study-envelope georeferencing is connected from the new geographic data.
Measured terrain, fuel, road routing, exposed settlement populations and actual
initial readiness are still not in the physical model. Base names are fictional
exercise desks; inherited resource/base IDs are compatibility handles, not actual
Málaga deployments. The original synthetic 200/150-person groups are not INE
municipal populations. Grid kilometre labels do not calibrate spread or travel.
The inherited protection safety-gated reward is unchanged and is not a medically
calibrated utility. Historical end states never force simulated resolution.

The [weather access audit](../malaga_2021/weather_access_status.json) distinguishes
public metadata from actual weather acquisition. Public global ERA5 chunks were
identified, but no suitable small anonymous local window was acquired; this is
not a claim that every public route is inaccessible. The prepared CDS request
still requires user access and terms. No token or API-key values were read.

An optional local hardware probe is separate from the simulator:

```sh
python eval/check_local_gpu_determinism.py
```

It runs a bounded disposable process and compares repeated integer CUDA results
to a Python CPU reference. It does not accelerate the simulator or prove model,
floating-point or cross-hardware determinism. Missing CUDA-enabled PyTorch
returns an explicit unavailable status, not a pass; it never installs drivers or
downloads model weights. No core dependencies are added for this probe.

## Additional missing-data discoveries

The [half-hourly weather acquisition](../malaga_2021/halfhour_weather_v1.json)
records 384 measured SiAR rows for MA04 Estepona, requested for 8–15 September
2021: temperature, relative humidity, wind speed/direction and rain. An anonymous
read-only public form query worked without CDS or SiAR API credentials. The
[daily IFAPA audit](../malaga_2021/station_weather_search_v1.json) separately
retains eight daily records and station metadata. The station is a 185 m
coastal/agricultural site, not a mountain fire-front measurement. Manual pages
22/25 establish UTC for post-April2014 subdaily queries. Eight table midnight
labels combine a next-day date and24:00. The subsequent normalization below
resolves them using independently matching source-chart labels and the official
calendar parser; original labels remain intact. Receipt times remain unknown.
Mean wind is not gust data, and the station-specific measurement height and
directional convention remain separate checks.

The [terrain/fuel audit](../malaga_2021/terrain_fuel_search_v1.json) adds an official
PNOA2020–21 terrain candidate whose collection predates the fire, unlike the
previous WCS grid's unknown epoch. Only a small25m window was saved from the
range-readable regional COG. It is quarantined: band nodata is255, while extreme
negative sentinel-like values survive in the derived raster. Do not interpret
those as real height, erase them with zeros, or use the raster in physics until
mask/vertical metadata and resampling are reviewed. This discovery does not
change the metadata or hashes of earlier terrain artifacts.

An actual EFFIS NFFL fuel-type map and native250m Málaga excerpt were acquired,
with numeric class codes and the publisher's legend. The map is cited as2017,
but its vegetation-observation epoch remains unverified and its filename contains
2000; it is not a measured2021 fuel/moisture inventory. There are850 nodata cells
in the7470-cell rectangle. No filling or land-cover-to-fuel conversion was applied.
The official UCO40 manual is a useful parameter-identification reference, not
proof of2021 field conditions.

The [operational evidence audit](../malaga_2021/operations_search_v1.json) retains
34 retrospective fact records and14 operational-plan entries, including a
12-code first-attack assignment list. Assignment, planned allocation, deployment,
arrival, availability and message receipt stay different evidence types. A
timestamped initial dispatchable roster, code-to-unit dictionary, unit arrival/
release timelines and command delivery/readback logs were not found in the
reviewed public sources. No agency was contacted or information request sent.

These are new evaluator-side candidates, not changes to the locked evidence
bundle, episode configuration, synthetic-condition runtime or reward. Raw source
files and derived rasters/weather remain in ignored local storage; public metadata
does not establish redistribution clearance or real operational effectiveness.

## Canonical data provenance and prepared inputs

Start with [the provenance JSON](../malaga_2021/provenance.json). It records source
URLs, input/output byte hashes, units/CRS, transformations, quality limitations,
runtime use and field-level origins for the mixed episode configuration. Its
SHA-256 detects edits; it does not prove source authenticity or physical validity.
`measured`, `derived`, `retrospective` and `synthetic` are distinct evidence classes.
Missing local artifacts are explicitly marked rather than claimed downloaded.
The document is an operator/evaluator reference, never an actor observation.

The [weather normalization audit](../malaga_2021/weather_normalization_v1.json)
contains exact offline reproduction commands. All384 source-clock labels now
normalize to UTC with no imputation or gaps. The eight midnight corrections are
supported by all five matching measurements, preceding-day chart `fechaStr`
labels and the pinned official `parseFechaHora` rollover. The chart's serialized
`+02:00` is retained but is not treated as the displayed measurement timezone.
Interval semantics and publication/receipt times remain unknown; normalized
weather is not automatically released to the actor or mapped to engine wind.
Sanitized chart evidence excludes forms, cookies and CSRF tokens.

The [landscape preparation audit](../malaga_2021/landscape_preparation_v1.json)
pins six small rasters on a common37×41,500m EPSG:25830 grid. Fuel codes use
nearest-neighbour categorical sampling:1,354 recognized samples and163 unknown
cells, with separate source-coverage and study-envelope masks. This is a baseline
map, not measured2021 fuel, fuel moisture or subcell composition. The study grid
is not the existing runtime's approximate36×39 grid; integration needs a separate
versioned adapter and tests. Terrain remains fully quarantined: the validity mask
is all zero, the suspect mask is diagnostic only, and no elevation/slope is emitted.
Apparently plausible elevations are not silently approved or filled with zero.

[Generated runtime inputs](../malaga_2021/synthetic_runtime_inputs_v1.json)
document the existing fictional roster/readiness,200/150-person groups, fixed
communication delays, seeded variants, normalized fuel and uncalibrated fire/
suppression rules, with their code hashes. These make exercises runnable without
pretending to reconstruct dispatch/readback logs. Physical fuel moisture remains
unimplemented and null, not an invented historical percentage. This increment
does not change simulator physics, rewards or the frozen evidence bundle.

To regenerate the reference after intentionally changing its inputs, run from the
repository root with the core source on the Python path:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.build_malaga_provenance \
  --snapshot-date YYYY-MM-DD --output .runtime/provenance-refresh
```

The output directory must be new. Required episode/evidence and cached raw inputs
must be prepared or restored separately. A changed generated profile must be
inspected and deliberately updated before its pinned canonical reference can be
rebuilt; a mismatch fails closed. Commit the reviewed JSON snapshots, not raw
CSRF-bearing HTML or uncleared source files. Keep ignored artifacts in a durable
local backup. Weather normalization uses only the standard library; landscape
preparation uses the isolated data environment's NumPy/rasterio, not new demo/core
dependencies. Run `python -m unittest discover -s tests -v` with the core source
on `PYTHONPATH`; raster tests require those optional data dependencies. Install
the tested pins from `data/malaga_2021/requirements.txt` in a separate virtual
environment; do not add these packages to the demo or dependency-free core.

## Prepared-data simulation mode

The new `wildfire_core.prepared.PreparedDataEnv` is separate from the original
Málaga exercise. Its compiler reads the exact pinned source/preparation audits
and artifacts, checks all six raster grids/masks and384 weather rows, and emits
a127KB dependency-free JSON configuration. No raw HTML, elevations, later fire
perimeters, operational labels or final incident outcomes are copied into it.

All positions use the common37×41 EPSG:25830 grid. `cell_lon_lat(column, row)`
returns precompiled cell-centre coordinates without requiring runtime PROJ.
Synthetic homes, refuge locations, reports and ignitions are generated inside the
study mask; reported fuel unknowns remain visible. The default mode is unchanged.

The mapping `wildfire-nffl-exercise-mapping-v1` assigns classes1–3 to the existing
field profile with0.75 normalized initial fuel,4–7 to scrub with1.0, and8–10 to
woodland with0.9. These are **assumed exercise parameters**, not calibrated NFFL
physics. Existing profile spread/burn-duration rules remain synthetic. Unknown
fuel uses explicitly labelled synthetic scrub, not an invented measured class or
non-burnable barrier. Padding outside the study mask is excluded. The inherited
`burned_terrain` diagnostic measures normalized kernel consumption per grid cell,
not independently validated hectares. Moisture is unimplemented; slope remains
disabled and pending authentic terrain, vertical metadata and mask validation.

Weather is measured **context only**. The source local detection clock is
interpreted in Europe/Madrid, giving2021-09-08T19:35:00Z. Received context exposes
only observations whose source clock plus an assumed five-minute delivery delay
has elapsed, retaining up to one hour of recent context. Receipt is synthetic,
not recovered publication history. No interpolation, gust reconstruction or
coastal-to-mountain wind correction is performed. `weather_context.physics_coupled`
is false: engine wind and the wind-shift smoke case remain explicitly synthetic
until source direction and a defensible conversion are verified.

With the ignored source/prepared caches restored, compile and run on one CPU:

```sh
export PYTHONPATH=envs/wildfire_response/core/src
.runtime/malaga-data-venv/bin/python -m eval.compile_malaga_runtime_data
python -m envs.wildfire_response.regional.prepared.run \
  --inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --expected-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --allow-synthetic-conditions --output .runtime/prepared-smoke-new --repeat
```

Both destinations must be fresh. The compiler digest pins the complete logical
payload, while provenance also records the different encoded-file byte digest.
Use the compiler's newly inspected digest if intentionally changing pinned data.
At runtime no NumPy/rasterio, keys, model weights, GPU or network access are needed.

Python usage:

```python
from wildfire_core.prepared import PreparedDataEnv, ProtectionBaseline

environment = PreparedDataEnv(
    '.runtime/malaga-prepared-inputs-v1/inputs.json',
    expected_sha256='ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108',
    allow_synthetic_conditions=True,
)
observation, info = environment.reset()
policy = ProtectionBaseline(prefer_shelter=False)
while True:
    observation, reward, terminated, truncated, info = environment.step(policy(observation))
    if terminated or truncated:
        break
verification = environment.verify_episode()
```

Six cases check urgent fire, false alarms, duplicates, conflicting/delayed reports,
synthetic wind changes and failed delivery. They are one-incident development
checks, **not held-out validation or training splits**. Passive, evacuation and
shelter policies use the same API. Traces record input/mapping/implementation
hashes and objective rewards; verification replays exact actions and independently
checks underlying population/resource/cost accounting. Complete exported JSON
must also survive exact replay. Safety-gated objective weights are unchanged;
this does not establish a medically or operationally validated utility.

The optional OpenEnv package additionally provides
`wildfire_openenv.prepared` with bounded typed actions, a single active hosted
episode and development-only discovery. Follow its README for isolated installs,
localhost startup and the real client/server comparison runner. The original nine
optional checks are exercised against their own legacy servers, not silently
substituted with the new adapter tests. No HappyRobot connection is required.

## Rights and retention

ICS-209-PLUS v3 metadata declares CC BY 4.0; retain dataset attribution and DOI.
No EGIF redistribution licence was verified in this pass. Both raw inputs and all
selected records remain local evaluator artifacts, not public dataset uploads.
The repository's existing root-code/asset licence restrictions remain unresolved.
Ignored `.runtime/` is not backed up by Git: retain the original archives and
private samples separately before cleaning a temporary checkout.
