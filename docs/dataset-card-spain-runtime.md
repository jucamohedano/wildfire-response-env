---
pretty_name: Wildfire Response Spain
license: other
language:
  - en
  - es
tags:
  - wildfire
  - simulation
  - reinforcement-learning
  - programmatic-environment
  - spain
configs:
  - config_name: tasks
    default: true
    data_files:
      - split: development
        path: tasks/development.parquet
  - config_name: landscapes
    data_files:
      - split: records
        path: landscapes/records.parquet
  - config_name: station_weather
    data_files:
      - split: records
        path: weather/records.parquet
  - config_name: incident_claims
    data_files:
      - split: records
        path: incident_claims/records.parquet
  - config_name: validation_cells
    data_files:
      - split: development
        path: validation/cells.parquet
  - config_name: municipal_population
    data_files:
      - split: records
        path: municipal_population/records.parquet
---

# Wildfire Response Spain

Prepared data for the CPU wildfire operations-control-centre environment. Download
this release to start episodes without rebuilding terrain, fuel or road data.
The repository name covers Spain so additional regions and incidents can be added
in later versioned releases. **The current real-world context is one landscape:
Sierra Bermeja, Málaga, September 2021.** Synthetic scenarios are not historical
reconstructions or additional observed incidents.

This is a **private research release, v0.3.0**. Mixed source terms and public
redistribution clearance remain unresolved. `license: other` does not grant a
blanket license or override the source notices in `provenance/`.

## Contents

- **12 development tasks:** six corrected synthetic-fixture exercises and six
  source-map v2 exercises. These are related development probes, not independent
  historical incidents or train/validation/test splits.
- **Two prepared runtime bundles:** input grids plus directed road graphs. Each
  file and canonical input object is hash-pinned in `manifest.json`.
- **384 normalized station-weather records:** MA04 Estepona, requested dates
  8–15 September 2021; temperature, humidity, wind and rainfall. Source clocks are
  normalized to UTC, including the eight midnight labels. Interval start/end,
  historical receipt and fire-front representativeness remain unverified.
- **Seven curated historical claims and seven municipality population records:**
  retained from the earlier private evidence release with source attribution.
- **48 operation-validation rows:** all 12 development configurations crossed
  with passive, dispatcher, conservative and stress scripted policies. These are
  quality-control records, not training demonstrations or model trajectories.
- **Provenance:** original context, source-specific terms, transformations,
  lifecycle registry, estimate methods and explicit limitations.

There are no model trajectories, expert demonstrations, private scoring audits,
future-event catalogs, checkpoints, credentials, model weights, raw source HTML,
source PDFs, satellite imagery or original full raster/vector packages here.

## Download and run

Use Python 3.12+ and a compatible checkout of
[`jucamohedano/wildfire-response-env`](https://github.com/jucamohedano/wildfire-response-env).
Access to this private dataset and the code repository is required. The
manifest pins engine and lifecycle content; an incompatible checkout is rejected
rather than silently changing the experiment.

```bash
python -m pip install -e envs/wildfire_response/core
# Install the hf CLI separately if needed; authenticate with HF_TOKEN or hf auth login.
hf download jucamohedano/wildfire-response-spain \
  --type dataset --revision v0.3.0 \
  --local-dir .runtime/wildfire-response-spain

python -m eval.hub_runtime \
  --data-dir .runtime/wildfire-response-spain --verify-only

# Complete a synthetic fixture day without API keys or a GPU.
python -m eval.hub_runtime \
  --data-dir .runtime/wildfire-response-spain \
  --runtime fixture --policy passive --wall-seconds 300

# Real-map experiment: acknowledge estimated/unapproved source inputs explicitly.
python -m eval.hub_runtime \
  --data-dir .runtime/wildfire-response-spain \
  --runtime es-ma-2021 --allow-unapproved-data \
  --policy dispatcher --wall-seconds 300
```

These commands run CPU reference policies. Model evaluation uses the same
`IncidentDayTools` contract and requires a separate bounded provider adapter;
no inference call or training job starts during download/loading. A day lasts
1,440 simulated minutes. Interrupted or incomplete runs have no successful score.

## Load the tables

Parquet tables are standard Hugging Face Datasets configurations:

```python
from datasets import load_dataset
repo = "jucamohedano/wildfire-response-spain"
tasks = load_dataset(repo, "tasks", split="development", revision="v0.3.0", token=True)
weather = load_dataset(repo, "station_weather", split="records", revision="v0.3.0", token=True)
```

`load_dataset` reads tabular metadata. `hf download` retrieves runtime assets.
The environment loads those files; reading a Parquet table does not start a
simulation. Task metadata, hashes and historical context are not the model's
observation. Expose only the public environment tools to an evaluated agent.

## Operation-wide quality checks

The release includes `validation/runtime_matrix.json` and the `validation_cells`
Parquet configuration. The recorded matrix covers 48 completed 1,440-minute
scripted runs with exact replay, independent reward agreement, action-clock and
resource checks, timed-path observation and checkpoint continuation checks.

All eight tools are checked across the suite: reads, report queries, approach and
destination previews, draft checks, committed orders, centre status and bounded
waiting. Committed request, authorization, release, shelter and evacuation kinds
have separate coverage. Per-cell statistics retain unexercised and unsupported
operations; one successful call is not proof of a successful downstream effect.
Reads and extra queries are tested for future weather/fire independence. Fault
controls exercise invalid timing, movement, stocks and accounting.

`validation_cells` is **not actor context or training data**: every row has
`actor_input_eligible=false` and `training_eligible=false`. Counts measure
exercise coverage, not model performance. Private call journals, minute audits,
checkpoints and detailed event-level findings are excluded. Empirical fire,
travel, suppression and population calibration remain separate work.

The old `v0.2.0` tag is retained unchanged. This release adds validation and
provenance corrections; it does not invent additional historical incidents,
measured fuels or expert decisions.

## Data and uncertainty

The source-map grid has 37×36 cells at 500 m spacing. Elevations derive from
IGN MDT05 sampled at 25 m, with complete support within the retained crop.
The exact source edition/native void semantics remain open metadata questions.
Fuel categories derive from EFFIS; 80 missing source cells use explicitly
labelled land-cover-aware estimates. Their known-mask values remain false.
The cached land-cover query left 27 sample centres unclassified. A separate
visual review of georeferenced IGN aerial imagery suggests land at all 27, while
some surrounding 500 m cells contain buildings, vegetation and small water-like
features. This is not a precise full-cell mask or recovered 2021 fuel. Original
fuel values, source-known masks and runtime censor guards remain unchanged;
missing vegetation never creates a zero-fuel firebreak. See
`provenance/visual_land_review.json` for source URL, inspected-image hash and limits.

The road graph is a 2026 OSM extract, not verified 2021 operational access.
Vehicle suitability, global turn restrictions, synthetic speeds, temporary posts,
resource rosters, fire/suppression rates, weather-to-engine conversion,
communications and population behavior are not empirically calibrated.
Weather is contextual station evidence, not live or calibrated fire-front forcing.
The historical data does not establish optimal dispatch or decisions.

Source input `approval_status` remains `unapproved_source_pack`; successful
verification does not approve it. The explicit source-data flag is required.
Population-first 80/20 rewards remain simulator objectives, not mortality counts
or evidence of real-world effectiveness. This release is unsuitable for live
emergency response, operational prediction or certification claims.

## Sources and attribution

1. **IGN/CNIG, PNOA MDT05:** [terrain products](https://pnoa.ign.es/web/portal/pnoa-lidar/productos-a-descarga).
   LiDAR-derived heights, sampled and aggregated for the exercise. Retain IGN/CNIG attribution;
   original source metadata and unresolved questions are in `provenance/terrain_closeout_20261029.json`.
   A separate [official IGN orthophoto service](https://www.ign.es/wms-inspire/pnoa-ma?SERVICE=WMS&REQUEST=GetCapabilities)
   supports the visual centre review. Imagery is not redistributed; its acquisition
   date was not established and it does not recover historical fuel.
2. **European Commission, EFFIS European Fuel Map (2017 citation):**
   [fuel documentation](https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels).
   NFFL categories are not measured biomass/moisture. The citation year does not
   establish the vegetation survey year or 2021 applicability.
3. **Junta de Andalucía, REDIAM/SIOSE2020:** [REDIAM](https://www.juntadeandalucia.es/medioambiente/portal/acceso-rediam).
   Retained land-cover evidence informs explicitly assumed fuel estimates; point
   classifications do not establish composition of a whole 500 m cell.
4. **© OpenStreetMap contributors:** [copyright and ODbL terms](https://www.openstreetmap.org/copyright).
   The directed road database carries ODbL-1.0 attribution. This component's terms
   are separate from the other sources and from simulator-code licensing.
5. **IFAPA/RIA and MAPA SiAR:** “Información obtenida del Portal IFAPA”.
   [SiAR query](https://servicio.mapa.gob.es/siarweb/consultaDatos/inicio) and
   [IFAPA open data](https://www.juntadeandalucia.es/agriculturaypesca/ifapa/riaweb/web/datosabiertos).
   Preserve provider attribution and the recorded source/time limitations.
6. **Copernicus EMS, EMSR545:** [incident activation](https://mapping.emergency.copernicus.eu/activations/EMSR545/).
   Retrospective incident context; original imagery/vector products are not included.
7. **Junta de Andalucía:** [Sierra Bermeja institutional summary](https://www.juntadeandalucia.es/presidencia/portavoz/tierraymar/165737/fuego/Infoca/incendio/desarrollosostenible/medioambiente/SierraBermeja/Jubrique).
   Short historical claims retain their original source locator and uncertainties.
8. **INE, table 2882:** [municipal population](https://www.ine.es/jaxiT3/Tabla.htm?t=2882).
   Attribution: “Elaboración propia con datos extraídos del sitio web del INE”.
   Municipal totals were published after the incident; they are not people present,
   evacuated or exposed during it.

`provenance/historical_source_registry.json` retains source-specific inclusion and
rights notes from the earlier evidence release. Private storage is not public
redistribution clearance. Public visibility and reuse require source-rights review.
