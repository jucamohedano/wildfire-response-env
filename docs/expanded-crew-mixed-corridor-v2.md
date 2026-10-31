# Raster-screened synthetic mixed walking corridors v2

This separately versioned private diagnostic preserves
[the vector-only v1 profile](expanded-crew-mixed-corridor-v1.md) and its output.
The v1 profile did not screen its walking corridors against the WorldCover
raster; a vector-only pass is not a raster-backed result. V2 uses the same
bounded source-fuel, terrain, v4 mixed-cell, observed-fire/wind and staging
rules, then screens **every candidate corridor**, including a zero-length
starting point, against both the pinned vector evidence and the pinned ESA
WorldCover 2021 raster. A corridor passes the raster check only if all touched
pixels fall in the existing pinned land-class set. Water, nodata, unexpected
classes and unavailable raster evidence are unknown and cannot authorize a
corridor. The vector and raster filters are uncalibrated; their agreement does
not establish real-world access, conditions, safety or a historical journey.
No water crossing, road or walking trail is invented.

The output binds the v2 tool, walking planner, staging and road/water code, and
all local geometry-compiler modules used to reconstruct OSM/DERA land and
water geometry. It verifies source-file digests and all referenced OSM tile
receipts before and after the run. It also records hashes of the effective OSM
and DERA geometries, land face, shoreline and bounds, together with geometry,
projection, raster and GDAL runtime versions. Source or implementation changes
abort the run. These hashes support reproducibility and are not independent
approval. A source file changed and restored between checks cannot be excluded
by these boundary checks.

The raster reader runs as a private child process on the same single CPU and
opens the raster once; corridor geometries travel over an anonymous pipe. It
uses the existing raster reader's CRS, dimensions, nodata and all-touched pixel
checks. Neither the results nor source geometries are actor-facing. The output
is an exclusively created mode-0600 file in a fresh mode-0700 `/tmp` directory.
Search incompleteness, later-crew inventory, engine-only geometry, current
hazards, work authorization and return limitations remain as described for v1.
This profile does not change the frozen host or bridge.

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python eval/expanded_crew_mixed_corridor_v2.py --opt-in-synthetic-mixed-corridors \
  --output-dir /tmp/expanded-crew-mixed-corridor-v2-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_crew_mixed_corridor_v2.py \
  tests/test_expanded_crew_mixed_corridor_v1.py -q -p no:cacheprovider
```
