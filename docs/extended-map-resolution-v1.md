# Extended map resolution: preserved v1–v3, synthetic exercise v4

**The original strict v1 is superseded for assessment:** it omitted OSM water
areas and watercourses. Its counts and sensitivity tier below are retained for
lineage only. The separate water-conflict v2 is the current conservative
evidence candidate. Neither is approved for runtime or release.

This is a **private, source-supported evidence candidate**, not an approved
release, a runnable map, or an update of the extended-input v1 contract.
`/tmp/wildfire-map-resolution-private/resolution-v1.json` is the complete
candidate (mode 0600); [aggregate evidence](../data/extended_map_resolved_v1/evidence.json)
contains no source-derived grid. Its canonical document SHA-256 is
`a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a`.
The strict candidate is preserved separately from the later synthetic sensitivity document.
The terms land and sea below mean **classified by the specified map sources and
rules**, not surveyed or independently verified ground truth. No cell is
certified as wholly dry, traversable, or fuel-bearing. Unknown is censored,
never treated as a firebreak or an accessible route. No access graph is created.

## Sources and shoreline comparison

* The complete [IECA DERA 01_07 coastline](https://www.juntadeandalucia.es/institutodeestadisticaycartografia/dega/datos-espaciales-de-referencia-de-andalucia-dera/descarga-de-informacion)
  was acquired on 2026-10-30 through the official WFS. It returned 1,580 of
  1,580 features in EPSG:25830; response SHA-256
  `8be89addcb3dae3437b42dbb9e31720c22ec6fc5291fa5ee19595987d45741ce`.
  The [IECA source metadata](https://www.juntadeandalucia.es/institutodeestadisticaycartografia/dega/sites/default/files/docs/094-datos-espaciales-referencia-dera-informacion-basica-datos-6f7g.pdf)
  describes photogrammetric BCA10 cartographic linework, scale 1:10,000,
  data date 2010-06-23 and publication 2023-01-30. It is not a 2021 shoreline survey.
* IECA DERA `03_02_MasaAgua` hydrography returned 94 of 94 features for the
  grid bounding box, SHA-256
  `f5fa1249f0a8efbb90ec396c1a233cc48be8297801073fdfbf16cdb37e71b432`.
  Its source is IGN IGR-HI; features touching a cell exclude it from both
  classifications. This is not proof of complete inland-water coverage.
* The private, non-atomic 2026 OSM lattice and sample tiles from
  [extended access v1](extended-access-v1.md) are verified by their manifest and
  raw tile hashes, including source way/node identity and coastline topology.
  They do not prove completeness, historical conditions, or access. © OpenStreetMap
  contributors, ODbL; consult the cited access documentation for attribution and
  redistribution requirements. OSM directed coastline land-left/sea-right is
  checked along every in-domain segment before identifying the two reference faces.
* [ESA WorldCover 2021 v200](https://esa-worldcover.org/en/data-access), tile
  `N36W006`, SHA-256
  `224ffccb57011119e7395a2ff172f3b1b6059add8d12a4280ae1a994d0b71212`,
  is a 10 m land-cover classification, not a direct fuel or ground survey.
  © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data
  (2021) processed by ESA WorldCover consortium. The [validation report](https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/docs/WorldCover_PVR_V2.0.pdf)
  reports 76.7 ± 0.5% global overall accuracy; that is not an accuracy or
  uncertainty estimate for these particular cells.
* Pinned full EFFIS fuel archive, parent, protected-area envelope, and separate
  elevation source are reconstructed as in [extended map v1](extended-map-v1.md).
  The two original parent gaps that happen to have full-archive classes remain
  unknown in the source layer. Fuel and terrain are not measured 2021 values.

Each coastline independently partitions the domain; the same fixed northern
land and southeastern sea reference points identify corresponding faces. The
OSM directed-side test checks their interpretation. Within the grid, the
clipped lines' GEOS Hausdorff distance is 116.293 m, the two identified land
faces disagree over 176,312.025 m² and sea faces over 176,897.156 m². These
are comparisons of the available linework, not a certified error bound; 2010
and 2026 features can differ legitimately. Small unanchored faces remain unknown.

## Water-conflict correction and current candidate

`/tmp/wildfire-map-resolution-private/resolution-v2.json`, canonical SHA-256
`a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87`,
preserves the original strict document as a pinned parent. It checks the pinned
2026 OSM tile manifests and every raw tile hash; conflicting or missing nodes,
ways, or relation members fail validation. It uses mapped water and wetland
areas, reservoirs, basins, riverbank areas and watercourses as **conflicting
evidence**, not as proof of 2021 water or a firebreak. Any cell geometrically
intersecting such evidence is unknown, including a boundary touch. The revised
map recomputes neighbor-based estimates with the revised land mask. Existing
source fuel classes remain evidence; no conflicting cell receives an estimate.

The current local acquisition contains 271 valid in-grid closed water-area
ways, three reconstructed water-area multipolygon relations, seven open
water-area ways treated as line evidence only, and 2,354 mapped watercourse
lines. Relation outer areas are censored including their holes as a precaution.
Incomplete mapping, omitted crossing ways, open boundaries, non-atomic tiles,
intermittent waterways, and a 2026 source date mean these observations do not
establish complete water coverage or the actual 2021 condition. Absence of an
OSM observation is not evidence of dry ground. The policy conservatively
censors 2,844 formerly mapped-land cells, including cells with intermittent or
possibly dry channel lines; it does not imply that the whole cell is water.
Among 4,956 cells, v2 has 1,488 mapped land, 232 mapped sea, and 3,236 unknown
surface. It has 73 remaining low-confidence fuel estimates and 149 unknown
fuel cells on mapped land. No release or simulation readiness follows.

The separately pinned `/tmp/wildfire-map-resolution-private/sensitivity-v2.json`
has no eligible second-tier synthetic fuel assumptions on these remaining 149
land gaps; 87 contain a built-up sample and 62 have mixed or other unsupported
classes. The v1 sensitivity artifact is retained unchanged and must not be
transferred to v2 without reconstruction.

## Archived IECA cross-check

The separately downloaded official [relief GeoPackage ZIP](https://ws089.juntadeandalucia.es/institutodeestadisticaycartografia/dega/sites/default/files/datos/094-dera-1-relieve-gpkg.zip)
(SHA-256 `922b32d80018ef6d49d989c5d3d29ad63454f17bf1f68418cec85b495209aba8`,
archive dated 2023) contains `T01_07_LineaCostaAndalucia` and the separate
IGN zero-line layer. The 64 intersecting BCA10 features have zero GEOS
Hausdorff distance and zero symmetric-difference length versus the pinned WFS
coastline within the grid. This is the same mapped layer in two deliveries,
not independent geographic evidence. The dated BCA10 caveat still applies.

The official [administrative-limits ZIP](https://ws089.juntadeandalucia.es/institutodeestadisticaycartografia/dega/sites/default/files/datos/094-dera-13-limites-administrativos-gpkg.zip)
(SHA-256 `749c61776cb90d71b174859bcd8f09abaca9883434b1fb5d251bb276d39b5eb2`)
has a 2025 archive and `T13_01_TerminoMunicipal`. The present IECA description
traces municipal polygons to December 2024 territorial boundaries, which are
retrospective and have not been proven independent of the mapped coastline.
Of 4,332 mapped-land cells, all are covered by the union of 30 intersecting
municipalities. Of 392 unknown cells, 306 are covered, 62 partially intersect,
and 24 do not intersect. All 232 mapped-sea cells are outside. Administrative
coverage is corroboration of geography, not proof that a cell is dry; missing
polygon coverage is not sea evidence. These counts do not alter the strict
candidate or its classifications.

## Surface and fuel rules

A cell is classified only if its entire square lies inside the intersection of
both matching coast-derived faces and does not touch either coastline's 250 m
buffer or any DERA inland-water polygon. The buffer is a precaution, **not** a
validated 250 m positional-error bound. WorldCover is nearest-neighbor projected
to 2,500 regularly spaced 10 m centre samples per cell; for land all must be
terrestrial classes 10, 20, 30, 40, 50, 60 or 100. Permanent water, wetlands,
snow, missing samples or other classes make land unknown. For sea all samples
must be permanent-water class 80 or zero/NoData; **zero is not interpreted as
water**: the two coastline faces support sea, while zero merely adds no
contradictory mapped land. Any sea cell with a source fuel class becomes
unknown. A raster sample is not proof that every part of the cell is dry or wet;
coastlines can omit features and inland water may be unmapped. Shoreline,
disagreement, mixed and unsupported cells remain unknown.

Fuel source classes remain separate from estimates. An estimate is proposed only
for a mapped-land cell with no source fuel and at least 90% of WorldCover samples
in tree, shrub, grass or crop classes. At least three **source-known, mapped-land**
neighbors within 1,000 m must support a unique class with at least 75% agreement.
Estimates do not recursively vote, and neighbors are not verified to share a
vegetation patch. Every estimated cell carries the method, class, supporting
source cells, vote fraction and `low_uncalibrated` confidence. Every source
class carries `effis_source_center_sample` and `source_class_unverified`;
unknowns have no replacement class. No estimate is called measured or confirmed.
A vote fraction is not a calibrated probability. Source classes are retained even
in unknown-surface cells as evidence, not proof of burnable land.

The resulting 4,956 cells comprise 4,332 mapped land, 232 mapped sea, and 392
unknown surface. Land has 3,842 source classes, 155 estimates, and 335 unresolved
fuel gaps. Of unknown-surface cells, 176 retain source-class evidence and 216
have unknown fuel; all 232 sea cells have unknown fuel. These counts are not a
simulation-ready coverage claim. The separate pinned terrain candidate has
4,639 numeric means and 317 null cells with one or more zero samples; it is not
blended with inherited parent elevation or approved for slopes. The reference
is a 2025 protected-area working envelope, not a complete massif boundary.

The exploratory leave-one-cell-out check predicts 1,772 of 1,883 eligible known
cells correctly. A stricter check withholds all source cells in the target's
quadrant and predicts 145 of 172 eligible targets; eligibility is restricted to
within 1 km of another quadrant, so these are not representative missing-cell
accuracy estimates. Both evaluate source-class agreement, not field fuel truth or
spread behavior. They do not justify calibrated confidence or runtime use.

## Optional synthetic sensitivity tier

The separate `/tmp/wildfire-map-resolution-private/sensitivity-v1.json` has canonical SHA-256
`fa115157d5c8535fb4bf69f7e94625c8e272e21716adb6a8dadfbfbd218b332d` and records
assumptions for **one** of the 335 mapped-land fuel gaps left after the strict
neighbor estimate. The other 334 remain unresolved: 230 have one or more
built-up samples; 104 have mixed vegetation or other insufficient evidence.
These are sample categories, not a surveyed count of urban or bare parcels.
The tier considers only strict-land cells with unknown fuel; it requires a
single vegetation class (tree 10, shrub 20, grass 30 or cropland 40) in at
least 2,250 samples, with any remaining samples restricted to bare class 60.
Any built-up, multiple vegetation categories, or other mapped classes are
excluded. Unknown surface, water, source fuel and first-tier estimates are not
filled by this tier.

The synthetic parameters use the repository's existing exercise mapping
`wildfire-nffl-exercise-mapping-v1` as a *scenario anchor*: woodland 0.9,
scrub 1.0 and field 0.75 dimensionless initial fuel. The assumed low parameter
is half that anchor; high is 1.5 times the anchor capped at 1.0. These are
illustrative, uncalibrated local bounds, not observed fuel, measured load,
NFFL class numbers, a probabilistic interval, or guarantees about low/high
fire outcomes. The one eligible cell is mapped grass with synthetic initial
fuel **0.375 to 1.0**. For this one-cell tier, the aggregate low/high parameter
sums are also 0.375 and 1.0. Unit tests exercise eligibility, exclusion, bound
selection, provenance and tamper rejection. No spread, policy, or outcome
sensitivity simulation was run: the extended-input v1 contract forbids these
estimates and the existing candidate still has censored surface, fuel, terrain
and access. A future censor-aware engine and explicit versioned contract are
needed before a fire-outcome sensitivity test can be interpreted. The strict
candidate is neither changed nor used as a firebreak-filled substitute.

## Private mixed-cell investigation

A separately versioned private interior mixed-cell evidence document and its
source-reconstructed neighborhood audit remain under
`/tmp/wildfire-map-resolution-private`. They keep evaluator identities,
per-cell audit results and source-derived detail private. The investigation
compares WorldCover 2021 samples, DERA and OSM coastline and inland water
features, and mapped watercourse lines. These observations do not establish
2021 stream flow, whole-cell water, a calibrated burnable fraction, or precise
subcell fire geometry. Coastal buffer cells are ineligible. The v1 runtime
contract continues to censor mixed cells.

## Synthetic inland mixed-cell exercise v3 and host interface

The private overlay `/tmp/wildfire-map-resolution-private/inland-mixed-exercise-v3.json`
uses version `extended-map-inland-mixed-exercise-v3` and pins the complete v2
document and the original evaluation catalog. It does
not mutate or replace v2. Each eligible record has `column`, `row`,
`surface: mixed_exercise_assumption`, the source fuel class and its provenance,
`fuel_factor_assumptions: {low: 0.8, nominal: 0.9, high: 1.0}`, WorldCover
sample counts, 2026 OSM watercourse references, a terrain-availability flag,
and `confidence: synthetic_uncalibrated`. These are **exercise assumptions**,
not measurements or an estimated area fraction; the factors are not calculated
from a line's geometry. The current v1 input contract censors mixed cells and
the v1 source converter rejects this overlay.

Eligibility requires a v2-unknown cell intersecting a mapped OSM watercourse,
coverage by both mapped land-side coastline faces, no contact with either
250 m coastal buffer or a DERA water polygon, no OSM water-area or open-area
boundary conflict, and a retained EFFIS source fuel class. Its 2,500 WorldCover
samples must include at least 2,250 tree, shrub, grass or crop samples, no more
than 25 water samples, and no missing or unsupported classes. These thresholds
are conservative selection rules for this exercise, not certified water-area
or vegetation bounds. Open-area water ways and relation polygons remain
censored; missing OSM features cannot prove dry land. Mapped streams and canals
can be intermittent, and their 2026 mapping does not establish 2021 flow.
Hydraulic widths, flow directions and fire crossings are **not measured or
calibrated**; the overlay provides no water crossing geometry, spotting or
edge-resolved physical spread rule. Coast, sea, major water areas and all other
unsupported cells retain their v2 classifications.

There are 2,423 eligible cells; of the other v2 unknown cells with a mapped
linear waterway, 242 lack source fuel, 143 lack corroborated inland geometry,
186 intersect mapped water-area evidence, and 121 fail WorldCover thresholds.
These reasons are assigned in that order and are not independent counts. An
initial-fire neighborhood audit preserves the catalog cases and all initial
coordinates. Its case-level and catalog-derived aggregate results are retained
only in the private v3 artifact. They do not establish that a run will remain
uncensored, and do not validate access, later spread, weather, scoring, runtime
compatibility or rights.

Host handoff: a future separately versioned, explicit-opt-in converter/backend
should independently reconstruct and verify the v2 and v3 document hashes and
source pins, select exactly one of the three named factors for an exercise, and
apply that factor to the existing mapped exercise initial fuel for each v3
record. It must keep source classes and synthetic factors distinguishable in
private provenance, leave every other v2 unknown censored, preserve sea
nonburnability, check the full spread stencil before and after every step, and
include the factor selection in replay/checkpoint identity. It must not export
evaluator cases or source-derived grids, or feed the overlay to the frozen v1
converter as ordinary land. A host would still need tests of mixed-cell fire
and containment semantics, reproducibility, censoring, routing and output
suppression before any synthetic diagnostic; no runtime or release is approved
by this artifact.

## Small-water exercise v4 and DERA river correction

The separate private overlay
`/tmp/wildfire-map-resolution-private/small-water-exercise-v4.json` uses version
`extended-map-small-water-exercise-v4` and pins the unchanged v2 and v3 parents.
It reconstructs every OSM water feature and the independently acquired DERA
`03_01_Rio` response (410 of 410 features; SHA-256
`6f26722f6c97bc5d0defc2f5e9245d39db0ba4f6816d89e0678dcda01adcc9d8`). The
response was acquired on 2026-10-30; the mapped watercourse survey epoch is
unverified. Earlier v2 and v3 did not screen this river layer. Its intersections
are conflict evidence, not proof that an entire 500 m cell is wet or a claim
about flow in 2021.

V4 inherits v3 mixed hypotheses and additionally permits an inland mixed-water
exercise when the source class and WorldCover vegetation checks hold, a mapped
watercourse is present, the mapped water area consists exclusively of valid,
disjoint, hole-free `natural=water` OSM way polygons wholly inside the cell,
and their union covers strictly less than 5% of its area. Any relation, open
water-area boundary, polygon touching a cell edge, overlapping polygons,
missing data, DERA water-area intersection, or coastal-buffer ambiguity is
rejected. The geometry percentage describes the available 2026 OSM snapshot,
not 2021 water or a measured firebreak. Each eligible cell preserves its 2021
WorldCover samples, 2026 OSM snapshot, DERA line provenance and unverified
source fuel class separately. Synthetic fuel factors remain 0.8, 0.9 and 1.0,
chosen as sensitivity assumptions rather than derived from mapped area.

The DERA line screen intersects 2,261 cells; 308 had been v2 land with no OSM
water conflict. V4 creates 251 additional DERA-line mixed exercise hypotheses
and 40 small-water-area hypotheses; 57 DERA-challenged v2 land/sea cells remain
explicitly censored. Together with inherited v3 cells, v4 has 2,714 mixed
exercise records. These are geographic aggregate counts, not evaluator audit
results. Detailed polygon measurements and all catalog-derived preflight
results remain private. The map is incomplete and not calibrated or approved.

Host handoff: reconstruct and independently pin v2, v3 and v4 before use. V4
`cells` is the complete mixed exercise overlay; each record carries its
`exercise_basis`, source fuel provenance and three assumed factors. Honor
`additional_dera_censors` even where v2 had labeled land or sea. Preserve all
other v2 unknowns, choose one factor explicitly, and retain uncertainty and
source epoch in private provenance. The private `small-water-topology-audit.py`
and `small-water-topology-audit.json` in the private directory preserve the
geometry checks without publishing evaluator-specific locations. V4 `cases`
and all case-derived summaries
are private evaluator material, never an actor observation or published
aggregate. No supplied v1 runtime accepts this interface. A separately
versioned backend, fire/containment sensitivity tests, replay and censor checks,
access assessment, rights review and full scenario qualification remain needed.

## Reproduction and rights gate

All raw and source-derived grids are private under `/tmp/wildfire-map-resolution-private`, outside every Git worktree. The output guard rejects other paths and nested worktrees.
The source response downloads are pinned bytes, including WFS timestamps: a
new request may have identical geometry but different bytes and must not silently
replace the pins. The following were acquired in the private directory:

```sh
curl -fsSL 'https://www.ideandalucia.es/services/DERA_g1_relieve/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=DERA_g1_relieve%3Ag01_07_LineaCostaAndalucia&outputFormat=application%2Fjson&srsName=EPSG%3A25830' -o /tmp/wildfire-map-resolution-private/dera-coast.geojson
curl -fsSL 'https://www.ideandalucia.es/services/DERA_g3_hidrografia/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=DERA_g3_hidrografia%3Ag03_02_MasaAgua&outputFormat=application%2Fjson&srsName=EPSG%3A25830&bbox=290000%2C4035000%2C332000%2C4064500%2CEPSG%3A25830' -o /tmp/wildfire-map-resolution-private/dera-water.geojson
curl -fsSL 'https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N36W006_Map.tif' -o /tmp/wildfire-map-resolution-private/worldcover-2021.tif
curl -fsSL 'https://www.ideandalucia.es/services/DERA_g3_hidrografia/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=DERA_g3_hidrografia%3Ag03_01_Rio&outputFormat=application%2Fjson&srsName=EPSG%3A25830&bbox=290000%2C4035000%2C332000%2C4064500%2CEPSG%3A25830' -o /tmp/wildfire-map-resolution-private/dera-rivers.geojson
```

With the pinned sources and the GIS interpreter described in the previous map
and access documentation, run from the repository root:

```sh
export PROJ_NETWORK=OFF PYTHONDONTWRITEBYTECODE=1
PY=/home/juancm/miniconda3/envs/detectree/bin/python
PRIVATE=/tmp/wildfire-map-resolution-private
for ACTION in compile validate sensitivity-compile sensitivity-validate water-v2-compile water-v2-validate water-v2-sensitivity-compile water-v2-sensitivity-validate exercise-v3-compile exercise-v3-validate exercise-v4-compile exercise-v4-validate; do
  "$PY" eval/extended_map_resolved_v1.py "$ACTION" \
    --parent /tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json \
    --fuel-archive /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-missing-data-v1/malaga-terrain-fuel-search/FuelMap_LAEA.zip \
    --boundary-archive /tmp/wildfire-natura-2025-shp.zip \
    --expanded /tmp/wildfire-bermeja-full-mdt25-halo.tif \
    --parent-sample /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-terrain-validation-v2/acquisition/mdt05-sampled25m-halo.asc \
    --coast "$PRIVATE/dera-coast.geojson" --water "$PRIVATE/dera-water.geojson" \
    --worldcover "$PRIVATE/worldcover-2021.tif" \
    --osm-manifest /tmp/extended-access-lattice-v1/manifest.json \
    --osm-samples data/extended_access_v1/tile-acquisition.json \
    --output "$PRIVATE/resolution-v1.json" --sensitivity-output "$PRIVATE/sensitivity-v1.json" \
    --water-v2-output "$PRIVATE/resolution-v2.json" --water-v2-sensitivity-output "$PRIVATE/sensitivity-v2.json" \
    --exercise-v3-output "$PRIVATE/inland-mixed-exercise-v3.json" \
    --exercise-v3-catalog /tmp/wildfire-eval-v1-final2-20261030/catalog.json \
    --exercise-v4-output "$PRIVATE/small-water-exercise-v4.json" --dera-rivers "$PRIVATE/dera-rivers.geojson"
done
/tmp/wildfire-response-env-incident-day/.runtime/qa-venv/bin/python -m pytest \
  tests/test_extended_map_resolved_v1.py tests/test_extended_map_v1.py tests/test_extended_inputs_v1.py -q -p no:cacheprovider
```

The archive comparison can be reconstructed and validated without changing the
strict or sensitivity candidates:

```sh
for ACTION in audit-dera audit-dera-validate; do
  "$PY" eval/extended_map_resolved_v1.py "$ACTION" \
    --relief-archive "$PRIVATE/dera-relieve-gpkg.zip" \
    --limits-archive "$PRIVATE/dera-limits-gpkg.zip" \
    --coast "$PRIVATE/dera-coast.geojson" --strict "$PRIVATE/resolution-v1.json" \
    --output "$PRIVATE/dera-archive-audit.json"
done
```

The complete source gate checks every published aggregate field against freshly
reconstructed v1–v4, sensitivity and archive artifacts. It fails with an
explicit list when required source files or OSM tile files are absent. A pass
is a data-consistency result only, not a rights or publication approval:

```sh
"$PY" eval/extended_map_resolved_v1.py validate-aggregate \
  --parent /tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json \
  --fuel-archive /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-missing-data-v1/malaga-terrain-fuel-search/FuelMap_LAEA.zip \
  --boundary-archive /tmp/wildfire-natura-2025-shp.zip \
  --expanded /tmp/wildfire-bermeja-full-mdt25-halo.tif \
  --parent-sample /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-terrain-validation-v2/acquisition/mdt05-sampled25m-halo.asc \
  --coast "$PRIVATE/dera-coast.geojson" --water "$PRIVATE/dera-water.geojson" \
  --worldcover "$PRIVATE/worldcover-2021.tif" \
  --osm-manifest /tmp/extended-access-lattice-v1/manifest.json \
  --osm-samples data/extended_access_v1/tile-acquisition.json \
  --output "$PRIVATE/resolution-v1.json" --sensitivity-output "$PRIVATE/sensitivity-v1.json" \
  --water-v2-output "$PRIVATE/resolution-v2.json" --water-v2-sensitivity-output "$PRIVATE/sensitivity-v2.json" \
  --exercise-v3-output "$PRIVATE/inland-mixed-exercise-v3.json" \
  --exercise-v3-catalog /tmp/wildfire-eval-v1-final2-20261030/catalog.json \
  --exercise-v4-output "$PRIVATE/small-water-exercise-v4.json" --dera-rivers "$PRIVATE/dera-rivers.geojson" \
  --relief-archive "$PRIVATE/dera-relieve-gpkg.zip" --limits-archive "$PRIVATE/dera-limits-gpkg.zip" \
  --archive-audit "$PRIVATE/dera-archive-audit.json" --aggregate data/extended_map_resolved_v1/evidence.json
```

The private inspection script `/tmp/wildfire-map-resolution-private/inspect-ignition-gaps.py`
reconstructs the neighborhood audit using the pinned evaluation catalog,
WorldCover raster, DERA coastline and water polygons, the 2026 OSM tile, and
the DERA named-river WFS response. The latter response SHA-256 is
`6f26722f6c97bc5d0defc2f5e9245d39db0ba4f6816d89e0678dcda01adcc9d8`;
its 410 of 410 returned features were requested for the corrected map extent
from `DERA_g3_hidrografia:g03_01_Rio`. A new WFS request can differ in timestamp
and therefore its byte hash. The script and evaluator-specific results stay
private. Given the independently pinned audit, set `TRUSTED_PRIVATE_AUDIT_PIN` from
the private trusted manifest; mixed evidence can then be reconstructed and
validated separately:

```sh
for ACTION in mixed-evidence-compile mixed-evidence-validate; do
  "$PY" eval/extended_map_resolved_v1.py "$ACTION" \
    --strict "$PRIVATE/resolution-v1.json" --water-v2 "$PRIVATE/resolution-v2.json" \
    --audit "$PRIVATE/ignition-neighborhood-audit.json" --output "$PRIVATE/interior-mixed-hypotheses-v1.json" \
    --strict-sha256 a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a \
    --water-v2-sha256 a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87 \
    --audit-sha256 "$TRUSTED_PRIVATE_AUDIT_PIN"
done
```

Validation reconstructs the candidate from the pinned raw sources and compares
all fields, including source and estimate evidence, rather than trusting a
rehash. The recorded run completed strict, sensitivity, and archive reconstruction
and validation, and 53 tests passed. The GIS
process emitted an initial PROJ database-open diagnostic, but completed both
reconstructions and the existing independent fuel pixel agreement check; this
warning deserves investigation before any release. Four existing NumPy
DeprecationWarnings were emitted in the previous terrain test.

DERA metadata describes CC BY 4.0 general reuse with attribution; WorldCover
and OSM require their respective notices and terms. These observations do not
clear the combined artifact: EFFIS, parent, terrain, boundary, OSM derived-data
and project-code rights remain subject to [the publication gate](license-status.md).
Do not redistribute the source-derived candidate or integrate these estimates
into v1. A future versioned contract and censor-aware engine must explicitly
accept estimates, validate provenance, account for unknown and mixed cells,
provide verified access/terrain coverage, complete source and rights review, and
undergo separate simulation validation.
