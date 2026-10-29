# Fuel-gap evidence review

`unknown_cells.json` is a reproducible **review ledger**, not a fuel input or an
approval. All 80 cells have `source_known: false`, `source_class: null`, and
`evidence_status: unresolved`: 78 centre samples are masked in the native EFFIS
extract and two are outside that extract. The latter does not prove absence from
the full European product. NoData says nothing about burnability. A valid native
sample contradicting the prepared pack would be flagged for review, not used to
repair it. Pixel rows and columns are zero-based. Grid centre coordinates are in
EPSG:25830; native coordinates are in EPSG:3035; longitude and latitude are the
stored EPSG:4326 pack centres. Native coordinates are rounded to six decimal
metres for the ledger; pixels are indexed from the unrounded projection.

## Reproduce

The input JSON documents use their canonical document SHA-256 (not their raw
file hashes): `ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108`
and `e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd`.
The script verifies those pins, their parent relationship, all four raster
hashes, raster alignment, and the 80 cells against the prepared class, validity,
and footprint rasters. It samples at the stored WGS84 centre, without resampling
fuel classes or modifying the source pack. It uses Rasterio/PROJ offline.

```sh
.runtime/qa-venv/bin/python eval/audit_incident_day_fuels.py \
  --runtime /home/juancm/hackspain/wildfire-response-env/.runtime > /tmp/fuel-review.json
cmp /tmp/fuel-review.json data/incident_day_v2/fuel_review/unknown_cells.json
/home/juancm/miniconda3/bin/python3 -m unittest discover -s tests -p test_incident_day_fuel_audit.py
```

The ledger was generated with Python 3.14, Rasterio 1.5.0 and pyproj 3.8.0.
Different PROJ installations may round projected coordinates differently; review
any resulting differences rather than assuming they establish different fuel.
`eval/audit_incident_day_fuels.py` emits JSON to stdout and writes no source data.
It will stop on a hash, grid, parent, footprint or sample disagreement. It does
not resolve, overwrite, or approve the source pack.

## Official alternative investigation

Sources were inspected on 2026-10-28. The subsequent bounded SIOSE service
queries and retained connector responses are documented below. No CILIFO fuel
extract was acquired. Do not substitute page or connector-output hashes for a
raw official dataset provenance record.

* **REDIAM / CILIFO**, the best lead: the [May 2024 Andalusian bulletin](https://www.juntadeandalucia.es/medioambiente/portal/documents/d/guest/boletin_rediam_mayo_2024_ext-pdf)
  reports high-resolution fuel maps using LiDAR, SIPNA and field calibration,
  and gives the REDIAM downloads catalogue path
  `06_RIESGOS_NATURALES_TECNOLOG/01_RIESGOS_NATURALES/01_RIESGO_INCENDIOS/ModeloCombustibles_CILIFO`.
  The [Andalusian field manual page](https://www.juntadeandalucia.es/organismos/ema/areas/incendios-forestales/prevencion/participacion-social/paginas/manual-modelos-de-combustible.html)
  says the study covers Andalusia and describes UCO40 fuel models and field
  surveys. This is a regional modelled map, not a direct measurement at each
  grid centre. The exact downloadable layer, per-cell coverage, observation
  dates, updates, CRS, class schema, nodata, licence and UCO40-to-runtime
  compatibility were not verified. Publication in 2024 is not the data epoch.
  In particular, post-fire updates could make a layer unsuitable as 2021
  pre-incident evidence. Catalogue discovery does not establish coverage of the
  80 points or permit class substitution.
  The [authors' 2025 paper](https://9cfe.congresoforestal.es/wp-content/uploads/2025/9cfe-1781.pdf)
  describes UCO30 for field identification, UCO20 for LiDAR mapping, and NFFL9
  equivalences to mapped UCO20; its Table 2 contains the specific crosswalk.
  Its reported 3,400 surface, 200 destructive and 800 tree plots are regional
  calibration evidence, not measurements at these points. The paper gives
  LiDAR flight dates July 2020 to May 2021, not an individual cell's date or
  proof that the distributed map was not subsequently updated. The official
  manual linked from the Junta page reported a 48,549,400-byte content length
  and was not downloaded because it exceeds the 30 MB cap. Crosswalks apply to
  fuel classifications only; they are not land-cover-to-fuel mappings.
* **IGN SIOSE AR** is an independent land-cover lead, not a fuel map. The
  [IGN 2026 announcement](https://www.ign.es/web/noticias?_IGNNoticias_WAR_IGNNoticiasportlet_advancedSearch=false&_IGNNoticias_WAR_IGNNoticiasportlet_andOperator=true&_IGNNoticias_WAR_IGNNoticiasportlet_delta=50&_IGNNoticias_WAR_IGNNoticiasportlet_resetCur=false)
  lists newly released 2020 provinces and does not list Málaga in that tranche;
  it does not establish Málaga's overall availability. Its [SIOSE description](https://www.ign.es/resources/noticias/VisualizadorSIOSE.pdf)
  specifies land cover and use. Any land-cover-to-fuel conversion is a modelling
  inference, never measured fuel or historical truth. No mapping has been
  selected or validated. CORINE's 25 ha minimum mapping unit and incompatible
  class semantics are already documented in the parent README.

## Bounded SIOSE 2020 WFS evidence

REDIAM's separate **SIOSE Andalucía** product is a better documented land-cover
lead than the IGN provincial release list. Its [official dataset metadata](https://www.ideandalucia.es/catalogo/inspire/srv/api/records/534d35a9-d809-4ffd-8d4a-27396baba070)
describes polygons with occupation codes and cover percentages, scale 1:10,000,
CRS EPSG:3042, CC BY 4.0, a 2024 revision, and a 2020-05-08 to 2023-02-13
metadata interval. Its lineage mentions 2016 PNOA and 2020 SIGPAC; the title
year alone is not the observation date of every polygon. The [official service
metadata](https://www.ideandalucia.es/catalogo/inspire/srv/api/records/066d65f59f7a33f4faafc952a62ca80f7fd63e86)
states free service use with attribution. WFS GetCapabilities exposed
`ms:siose20`, GeoJSON, and EPSG:4326/CRS:84 support at
`https://www.juntadeandalucia.es/medioambiente/mapwms/REDIAM_siose_2020`.

`siose2020_wfs_probe.json` retains connector-extracted service responses for
all 80 centres, queried in windows of +/-0.0001 degrees in CRS:84 with a maximum
of five features. Responses were collected on 2026-10-28 and the last retry
completed 11:37:45 UTC; the sidecar is under 1 MB and has SHA-256
`224ce89157e5a13d075a7b9d587ef16964d5dcb88da5e82f614523efaf318242`
and 831,254 bytes.
This is the hash and size of the **saved connector text**, not raw HTTP bytes;
the connector does not expose the original response bytes, headers, download
sizes or their hashes. It therefore is not an independently pinned source
extract. Each cell also records `response_extracted_utf8_bytes` and
`response_extracted_sha256` for its saved response string encoded as UTF-8,
including the connector's content-info annotation. These are not HTTP payload
sizes or hashes. The dataset- and service-level metadata URLs for the licence
and attribution are recorded in the sidecar. The reported service observations are:

* 53 centres are strictly inside one returned polygon, with feature ID and
  `cod_os`/`os` in the sidecar. These are modelled land-cover attributes.
* 20 small-window queries returned no features, and seven returned nearby
  polygons that did not contain the centre. These results do not prove there
  is no land cover or fuel at those cells. No boundary/overlap ambiguity or
  truncated result remains in the saved sample.

Point inclusion is a planar longitude/latitude test against the returned
GeoJSON, including Polygon and MultiPolygon holes, with a `1e-10` degree
boundary tolerance; it is not area coverage or 500 m-cell aggregation. The
offline helper checks response hashes, connector truncation, WFS feature count
limits, coordinates and recorded classifications before recounting. Recheck the
retained text and coordinates without network or raster libraries:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/juancm/miniconda3/bin/python3 \
  eval/audit_incident_day_fuels.py --verify-siose-probe \
  data/incident_day_v2/fuel_review/siose2020_wfs_probe.json
```

The REDIAM GeoNetwork CSW catalogue endpoint identified by the
[official service guide](https://www.juntadeandalucia.es/medioambiente/portal/fr/web/guest/landing-page-%C3%ADndice/-/asset_publisher/zX2ouZa4r1Rf/content/c-c3-b3mo-puedo-acceder-a-los-servicios-ogc/20151)
was inaccessible through the web tool and reset a browser connection. A local
`curl` attempt could not resolve that host. Thus the exact CILIFO metadata,
WMS/WFS layer, licence, small download and 80-point coverage remain unverified;
the failures describe these access paths, not proof that the service is down
for everyone. The accessible structural-risk WMS is a risk index, not a fuel
class layer. No FTP or HTTPS CILIFO data URL has been verified.

## Real-geography synthetic sensitivity

`sensitivity_real_geography.json` records one passive development scenario for
each synthetic fallback 1, 4 and 8, using frozen core digest
`03e4fc4d2957e37a168f53183399347b27e0adc107d857dee18c44df772e48d5`.
All three episodes passed independent replay and outcome verification in 19.958
seconds, using one CPU worker, and completed 1,440 minutes per case without
boundary censoring. All input packs retained the 80-cell source-known mask and
`unapproved_source_pack` status.

Each case assigns one **uniform representative assumption to the 80 unknown
cells**: class 1 maps to field, class 4 to scrub and class 8 to woodland.
The existing exercise mapping assigns these classes initial fuel values of 0.75,
1.0 and 0.9 respectively, along with different terrain spread and duration
parameters. These three assumptions are not ordered low, medium and high
physical fuel bounds, nor are they a land-cover-to-fuel inference for any
individual cell.

Each case reported 625 inherited and 350 additional burned hectares and zero
newly exposed people. These are **unapproved synthetic sensitivity results**,
not grades, benchmark results, training data, historical replay, or evidence
that the assumptions match actual fuel. Identical outcomes in this one quiet
passive case do not establish robustness or general insensitivity. The road,
resource, weather and spread assumptions remain subject to the existing gates.

To reproduce in a fresh output directory, from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/juancm/miniconda3/bin/python3 \
  eval/check_incident_day_fuel_sensitivity.py \
  --runtime /home/juancm/hackspain/wildfire-response-env/.runtime \
  --output /tmp/incident-fuel-sensitivity-new --wall-budget 120 \
  --expected-core-sha256 03e4fc4d2957e37a168f53183399347b27e0adc107d857dee18c44df772e48d5 \
  --verify-episode
```

The saved report requested replay verification. The optional `--verify-episode`
flag independently replays completed and censored episodes
within the same wall budget; it does not make their results graded or approved.
Timeouts leave the active case `interrupted_ungraded`. Boundary contact is
explicitly reported as censored and ungraded. The script rejects a core digest change,
bad pins, incomplete heights, crop misalignment, or an unexpected unknown count;
it does not repair those conditions.

The CLI defaults to the frozen digest above. Before future runs, pass the
caller-held frozen implementation digest using `--expected-core-sha256`; do not
compare results across different cores as if only fuel had changed.

## Exact next data requirements and gates

1. Obtain the CILIFO catalogue metadata and an authoritative bounded vector or
   native raster extract covering all 80 coordinates plus a margin, initially
   under 30 MB. Obtain the product legend, nodata/mask, native CRS, resolution,
   collection/processing dates and updates, especially around September 2021,
   licence and attribution, class definitions and any documented NFFL/UCO40
   equivalence. Record actual download URL, UTC retrieval time, byte count,
   SHA-256, licence, epoch, CRS and schema; retain the raw extract unchanged.
   If a bounded extract cannot be obtained, contact the REDIAM catalogue data
   provider for a scoped extract and metadata; do not download a large regional
   archive implicitly.
2. Reproject these 80 centres with an offline, documented CRS pipeline and
   report per-cell polygon/raster coverage, source pixel or feature ID, source
   value, nodata/overlap, acquisition date and disagreements with EFFIS. Do not
   treat a 20 m or land-cover sample as representative of a 500 m cell without
   documenting spatial aggregation and uncertainty. Distinguish a modelled fuel
   product from an in-situ measurement; field inventory validation elsewhere
   does not measure these exact cells.
3. If only land cover is available, document a locally defensible mapping with
   source classes, alternatives, assumptions and uncertainties, and evaluate
   representative sensitivity cases without treating them as ordered physical
   bounds. Keep these values explicitly inferred and separate from known masks.
   Check their impact before any proposed use. Unknown or ambiguous categories
   stay unknown.
4. Independently review source provenance, temporal applicability, spatial
   coverage, mapping and sensitivity. **Real source-pack approval remains
   blocked**; this ledger neither changes the gate nor supplies automatic
   approval. Preserve the existing two-hour benchmark and its results.
