# Extended map candidates: corrected geographic reference

The corrected source-coverage candidate is generated privately at
`/tmp/wildfire-extended-map-private/map-v2.json`. It is **not an approved or
runnable incident-day pack**. Only code and the small aggregate
`data/extended_map_v1/evidence-v2.json` are project deliverables; source-derived
grids must not be committed or redistributed before the rights review in
[license-status.md](license-status.md).
An earlier 57 × 46 exploration extended only the old crop; it was insufficient
for the protected-area reference and is not a project deliverable.

## Reference and grid

The [MITECO Natura 2000 2025 archive](https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/servicios/banco-datos-naturaleza/3-rn2000/n2000_2025_shp.zip),
downloaded on 2026-10-30, has SHA-256
`0b35e4f6ec7ab198f51cd66705a090a496d5b98f9203c825b499a9f8ebbb2cbd`.
Its Iberian/Balearic shapefile has two valid EPSG:25830 features:
`ES6170004` (Los Reales de Sierra Bermeja) and `ES6170010` (Sierras Bermeja
y Real), with combined bounds `[295023.6081, 4035210.911, 326731.6042,
4059055.8712]` metres. Redistribution terms have not been cleared.
It is a **2025 protected-area reference**, not a 2021 condition or evidence
of the complete physical massif. No claim that the corrected rectangle covers
the entire physical massif is made.

The compiler checks the file hash, both site codes, geometry validity, CRS and
the resulting extent. It buffers the reference bounds by 5 km north, east and
west, includes the reference and the original crop to the south, and rounds
outward to 500 m. The corrected bounds are `[290000, 4035000, 332000,
4064500]`, EPSG:25830: 84 columns × 59 rows (4,956 cells). The original
37 × 36 parent begins at column 14, row 20. Coordinates describe cell edges;
centres are half a cell inside. The rectangular extent is a working envelope,
not a fire perimeter or a surveyed mountain boundary.

## Fuel and parent provenance

The real parent `incident-day-inputs-v2` pack has file SHA-256
`c4568ea6f5f16e41c8a81768f510def1e7d7440eebce0af0e5382d7226f0638e`
and embedded canonical SHA-256
`50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d`.
Its network hash is recorded for lineage, not proof of expanded road coverage.
See [hub-runtime.md](hub-runtime.md), [dataset-card-spain-runtime.md](dataset-card-spain-runtime.md)
and the parent provenance for original sources and restrictions.

The compiler verifies the complete EFFIS `FuelMap_LAEA.zip` archive SHA-256
`ed06080d81048de18dc34caf0f4fcc2f26d626f020bf132277d4e470a71a83d8`
and its `FuelMap2000_NFFL_LAEA.tif` member SHA-256
`cfb7447de166fb6fab12b685c98c1d12f55c4c62708ebff2af26a9ca59452102`.
The archive is the European Forest Fire Information System, 2017, European
Fuel Map, European Commission; [EFFIS fuel documentation](https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels).
The member name includes 2000; the citation year is not a verified vegetation
survey date. Fuel class is not measured 2021 fuel, fuel load, moisture or a
calibrated engine parameter. Redistribution clearance is not asserted.

For each 500 m centre, the compiler transforms to EPSG:3035 and selects the
containing native 250 m pixel, with the source's zero/NoData kept null. Parent
centres use the stored WGS84 coordinates as in the original audit. GDAL's
WGS84 route and pyproj's direct EPSG:25830 route must select the same native
pixel for every centre; this is a coordinate/pixel consistency check, not
independent field validation. All 1,252 parent known source classes agree.
Two parent cells `(row 0, column 36)` and `(row 1, column 36)` were outside
the original clipped source footprint but have class 10 in the full archive.
The candidate retains both as unknown to preserve the 80-cell parent uncertainty;
the comparison records these additional samples without promoting them.

Of 3,624 cells outside the parent, 2,766 have source fuel classes and 858 are
unknown. With the 1,252 parent known cells, 4,018 of 4,956 have source values.
A centre sample is not a whole-cell cover survey. All 4,956 `surface` values
are `unknown`; fuel does not establish sea, land, a firebreak or traversability.
NoData, unverified coast, roads, inaccessible areas and interior water are not
converted into one another. The compiler cannot assert land or sea; a future
version needs complete documented coastal evidence and a conservative whole-cell
and shoreline policy. It does not create a walk or road-coverage mask.

## Separate corrected terrain candidate

The private `terrain-candidate-v2.json` uses the IGN WCS sample acquired in
this session on 2026-10-30, with SHA-256
`299b0cf337786e666fdbdeb32df86531d84a490da8d00dc91678bfe136d909a2`.
The source reports no NoData value, has no band unit, and its exact epoch,
vertical datum and native void handling are unverified. The exact recorded
[WCS request](https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=1.0.0&REQUEST=GetCoverage&COVERAGE=Elevacion25830_5&CRS=EPSG%3A25830&RESPONSE_CRS=EPSG%3A25830&BBOX=289975%2C4034975%2C332025%2C4064525&RESX=25&RESY=25&FORMAT=GEOTIFFINT16&INTERPOLATION=nearest%20neighbor)
requests `Elevacion25830_5` with 25 m resolution and nearest-neighbor
interpolation. The requested resolution does not establish the survey epoch or
native missing-value semantics.
The input's shape is 1,182 × 1,682, bounds `[289975, 4034975, 332025,
4064525]`, and it covers exactly 20 × 20 samples per 500 m cell plus a one
pixel halo on all sides. The compiler checks the hash, alignment, CRS, dtype,
mask, shape, scale and numeric range over the **entire** input and halo.

There are 115,380 zero samples: 114,313 in cells and 1,067 in the halo.
Because zero may be missing, all 317 cells containing any zero have null
candidate means. The other 4,639 means are arithmetic averages of their 400
nonzero samples; numeric coverage is not verification of elevation accuracy,
void semantics or suitability for physics. Halo values are screened, not used
in means or slopes. The terrain artifact is separate from the map's inherited
parent elevations; **no old and new terrain is blended**. It is not approved
for slopes or simulation.

Against the SHA-pinned floating-point parent 25 m ASCII sample, the aligned
intersection has 624,780 pixels, of which 5,633 are exactly equal; direct
unrounded absolute differences have mean 2.6537463 and maximum 27.4190063
in the returned source values. This comparison uses the larger corrected
extent and direct floating-point values, so it is not the same calculation as
the earlier rounded/smaller-overlap figures. It does not establish which source
is more accurate. The earlier smaller terrain exploration is not used by the corrected candidate.

## Rebuild and validation

Compilation requires local pinned parent, fuel archive and boundary archive;
terrain compilation separately requires the pinned new raster and parent ASCII.
GIS dependencies are `rasterio`, `numpy`, `pyproj`, and `pyogrio` with its
GeoPandas/Shapely dependencies. A local environment with these packages is
`/home/juancm/miniconda3/envs/detectree/bin/python`. These commands use local
files only and disable PROJ network access:

```sh
export PROJ_NETWORK=OFF
PY=/home/juancm/miniconda3/envs/detectree/bin/python
PARENT=/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json
FUEL=/home/juancm/hackspain/wildfire-response-env/.runtime/malaga-missing-data-v1/malaga-terrain-fuel-search/FuelMap_LAEA.zip
BOUNDARY=/tmp/wildfire-natura-2025-shp.zip
OLD=/home/juancm/hackspain/wildfire-response-env/.runtime/malaga-terrain-validation-v2/acquisition/mdt05-sampled25m-halo.asc
NEW=/tmp/wildfire-bermeja-full-mdt25-halo.tif
PRIVATE=/tmp/wildfire-extended-map-private
"$PY" eval/extended_map_v2.py compile --parent "$PARENT" --fuel-archive "$FUEL" \
  --boundary-archive "$BOUNDARY" --map "$PRIVATE/map-v2.json"
"$PY" eval/extended_map_v2.py validate --parent "$PARENT" --fuel-archive "$FUEL" \
  --boundary-archive "$BOUNDARY" --map "$PRIVATE/map-v2.json"
"$PY" eval/extended_map_terrain_v2.py compile --expanded "$NEW" \
  --parent-sample "$OLD" --output "$PRIVATE/terrain-candidate-v2.json"
"$PY" eval/extended_map_terrain_v2.py validate --expanded "$NEW" \
  --parent-sample "$OLD" --output "$PRIVATE/terrain-candidate-v2.json"
```

The compiler only writes source-derived grids under `/tmp` outside this
checkout or its ignored `.runtime` directory, with file permissions 0600.
Tests create their outputs in temporary directories. Validation reconstructs and compares every field and sample, not just the self-hash.
The full EFFIS member is temporarily extracted during sampling, then removed;
allow approximately 622 MB of temporary disk space. The targeted regression
suite is `/tmp/wildfire-response-env-incident-day/.runtime/qa-venv/bin/python
-m pytest tests/test_extended_map_v1.py -q`; its source checks invoke the GIS
interpreter above. Source-dependent tests skip explicitly when required inputs
or dependencies are absent.

Integration requires verified land/sea and road coverage, source-quality and
licence review, and an engine that propagates or censors unknown fuel, surface
and terrain. Do not coerce unknowns to sea, zero elevation, synthetic fuel,
traversable ground or a closed boundary. A new versioned engine, scenario and
release validation are required before any simulation use. The existing runtime,
release pins and published inputs remain unchanged.
