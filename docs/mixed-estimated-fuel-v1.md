# Private mixed-surface estimated-fuel hypotheses

`eval/mixed_estimated_fuel_v1.py` builds a separately versioned private
**sensitivity evidence document**. It is not an incident-day pack, a runtime
adapter, a new source measurement, or a release. It neither edits nor replaces
map evidence or exercise versions v1–v4. The caller supplies the target cells in
a separately pinned private file; no evaluator-specific locations or outcomes
are embedded in this document or its tests. Source-derived results, target lists,
spatial support and evaluator-linked artifacts must remain under the dedicated
private directory and must not be exported to actors, training data or a public
release.

## Surface and source evidence

Before producing any hypotheses, the compiler reconstructs v1 through v4 from
pinned parent inputs, the complete EFFIS fuel archive, the protected-area
reference, separate terrain, DERA coast and water data, WorldCover, the private
OSM tiles and the source catalog. It checks each rebuilt document against a
separately supplied canonical document pin, verifies the exact bytes of every
OSM tile on each pass, and records source-code hashes and the pinned fuel-mapping source. When the DERA water and
river layers are reread for spatial indexes, the compiler hashes the exact byte
snapshot that it parses into geometries against the original source pins. A
changed response fails closed. Per-cell DERA river evidence is derived from
the same checked river snapshot, and mapped water-area exclusions are checked
against the verified geometries. The private target document is likewise parsed
from the exact bytes checked against its independently supplied hash. The evaluation catalog is
used only to reconstruct the existing v3/v4 map artifacts. Neither outcomes nor
policy performance enter the estimates or holdout diagnostic.

A target must be mapped land under the original strict evidence, unknown in the
v2 water-conflict map, inside both coastline land faces and outside their
250 m shoreline buffers and DERA water polygons. It must have a mapped OSM
watercourse, no mapped OSM water-area conflict, a non-null terrain candidate,
and no retained source fuel class. Its WorldCover 2021 samples must contain
at least 90% vegetation, no unsupported or missing samples, and at most 1%
water samples. These are screening assumptions, not proof of a dry cell, fuel
load, accuracy, water width or access. OSM mapping is a 2026 snapshot; the DERA
river response was acquired in 2026 and its survey epoch is unverified. Neither
source establishes the 2021 hydrological condition. A watercourse or missing
fuel is never an inferred firebreak.

The full fuel archive's native center sample is recorded separately. If a
parent gap was deliberately retained despite an archive value, that sample is
not silently promoted. The original v1 estimate, if any, is retained as
provenance rather than silently substituted into an input. The new document
keeps `source_fuel_class: null` for every target, even when it proposes an
exercise class.

## Spatial support and sensitivity

For each target the method examines original source-class cell centers within
1,000 m. A supporting cell must be source-known and either mapped v2 land not
challenged by the v4 DERA censor, or an explicit v4 mixed exercise cell.
Estimated fuel is never recursively used as support. A center-to-center segment
that intersects a mapped OSM water-area geometry or DERA water polygon is
excluded. Proper geometric crossings of mapped OSM and DERA watercourse lines
are retained and reported per supporting sample; a non-crossing is not proof
of connectivity, and incomplete or retrospective mapping can omit features.
No assumption about road or vehicle access is made.

At least three source neighbors and a unique plurality of at least half the
support are required; ties or more than three distinct source classes remain
unknown. A 75% vote is labeled a stronger *vote*, but every output remains
`low_uncalibrated`. A unique plurality below 75% is only an ambiguous
sensitivity hypothesis, not a robust imputation. The alternative source classes,
counts, distances, mapped-water crossings, excluded-path count and no-crossing
votes are preserved privately for review. Nearby source samples are not
independent field observations or proof that the target shares their vegetation.

For eligible hypotheses the low, nominal and high scenarios choose,
respectively, the supported class with the lowest exercise initial-fuel mapping,
the modal supported class, and the supported class with the highest exercise
mapping. The factors 0.8, 0.9 and 1.0 multiply those exercise values; they are
not derived from water-line geometry. The versioned
`wildfire-nffl-exercise-mapping-v1` mapping source is hash-pinned. A change of
class may also change modeled terrain. Consequently these low/high labels order
only the specified *initial-fuel assumptions*: they are not a confidence
interval, a physical fuel range, or an ordering of fire outcomes. They must
never be represented as measured vegetation or historical fuel.

The private document also records leave-one-cell-out and quadrant-blocked
source-class diagnostics with the same neighbor and mapped-water-area rules.
The block diagnostic can evaluate only targets close enough to a quadrant
boundary to have eligible support outside their quadrant. Both compare against
known samples from the same EFFIS source, not independent ground truth, and may
be unrepresentative of missing cells. A separate no-mapped-watercourse-crossing
vote and its eligible count are reported; absent mapped crossings do not prove
that samples belong to one continuous fuel patch. They do not calibrate these hypotheses or
validate the fire model.

## Rebuild and validation

Use the same private sources and GIS environment described in
[the extended map documentation](extended-map-resolution-v1.md). The private
target file is JSON with version `private-spatial-targets-v1` and a `cells`
array of `[column, row]` pairs. It must be placed in
`/tmp/wildfire-map-resolution-private`, pinned independently by byte SHA-256,
and kept private. Duplicate or out-of-bounds cells are rejected. A missing
source fails explicitly. Outputs must be direct children of that owner-only dedicated directory, outside
Git worktrees. Existing hardlinked destinations and input inode aliases are
rejected. Inputs include all tile paths named by both hash-verified OSM
manifests, with resolved paths and file identities checked before reconstruction
and again before replacement. The writer creates a fresh mode-0600 inode and
atomically replaces the directory entry, so an existing link cannot expose
newly written data or cause an input inode to be truncated.

The following expects all document pins and the target-file hash to come from a
trusted private manifest, not from the documents being checked:

```sh
export PROJ_NETWORK=OFF PYTHONDONTWRITEBYTECODE=1
PY=/home/juancm/miniconda3/envs/detectree/bin/python
PRIVATE=/tmp/wildfire-map-resolution-private
for ACTION in compile validate; do
  "$PY" eval/mixed_estimated_fuel_v1.py "$ACTION" \
    --parent /tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json \
    --fuel-archive /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-missing-data-v1/malaga-terrain-fuel-search/FuelMap_LAEA.zip \
    --boundary-archive /tmp/wildfire-natura-2025-shp.zip \
    --expanded /tmp/wildfire-bermeja-full-mdt25-halo.tif \
    --parent-sample /home/juancm/hackspain/wildfire-response-env/.runtime/malaga-terrain-validation-v2/acquisition/mdt05-sampled25m-halo.asc \
    --coast "$PRIVATE/dera-coast.geojson" --water "$PRIVATE/dera-water.geojson" \
    --worldcover "$PRIVATE/worldcover-2021.tif" \
    --osm-manifest /tmp/extended-access-lattice-v1/manifest.json \
    --osm-samples data/extended_access_v1/tile-acquisition.json \
    --dera-rivers "$PRIVATE/dera-rivers.geojson" \
    --catalog /tmp/wildfire-eval-v1-final2-20261030/catalog.json \
    --strict "$PRIVATE/resolution-v1.json" --strict-sha256 "$TRUSTED_STRICT_PIN" \
    --water-v2 "$PRIVATE/resolution-v2.json" --water-v2-sha256 "$TRUSTED_V2_PIN" \
    --exercise-v3 "$PRIVATE/inland-mixed-exercise-v3.json" --exercise-v3-sha256 "$TRUSTED_V3_PIN" \
    --exercise-v4 "$PRIVATE/small-water-exercise-v4.json" --exercise-v4-sha256 "$TRUSTED_V4_PIN" \
    --targets "$PRIVATE/mixed-estimated-fuel-targets-v1.json" --targets-sha256 "$TRUSTED_TARGET_FILE_PIN" \
    --output "$PRIVATE/mixed-estimated-fuel-hypotheses-v1.json"
done
/tmp/wildfire-response-env-incident-day/.runtime/qa-venv/bin/python -m pytest \
  tests/test_mixed_estimated_fuel_v1.py -q -p no:cacheprovider
```

Validation reconstructs all fields from the pinned source artifacts, target
file and compiler, rather than trusting a self-hash. The existing v1–v4 artifacts
remain unchanged. The current source-only/mixed exercise backends reject
estimated fuel: a future separately versioned opt-in input and engine would have
to validate these hypotheses, retain their estimated provenance, support
uncertainty and censoring, include the chosen scenario in replay/checkpoint
identity, and separately test fire and containment behavior. No scenario or
fire-outcome sensitivity run is claimed here. Source rights, terrain semantics,
access and the [publication gate](license-status.md) remain unresolved.
