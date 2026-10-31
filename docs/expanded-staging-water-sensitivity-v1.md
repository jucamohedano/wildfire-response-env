# Development staging water-buffer sensitivity v1

This private, separately versioned diagnostic compares mapped-water proximity
thresholds of 0 m, 25 m and 50 m on the same twelve pinned **development**
cases and the same source-backed staging search. It does not tune a held-out
evaluation, select a threshold, replace the 50 m exercise policy, change a
scenario or route, or authorize dispatch. The narrower thresholds are not
claims of safety or calibrated water clearance. Flood, legal status, actual
vehicle access, 2021 conditions and ground-work safety remain unknown.

The tool verifies the original private 50 m candidate and audit against fixed
digests and the pinned source implementations, reconstructs original cases,
post anchors and source graph, and reconstructs each partial public handover
observation from the private audit only after verifying its original digest.
It reruns the bounded staging search independently for each buffer. The 50 m
result must reproduce the pinned original case and audit records exactly or the
comparison aborts. Source topology, mode restrictions, fire/wind guards, search
limits and unknown handling are unchanged. The resulting hypothetical search
paths are used only to count differences in the private audit; they do not
modify or export an operational route.

Only the proximity threshold for OSM and DERA water features changes. The
existing 50 m land/raster screening corridor and coast protection remain fixed,
as do the WorldCover and source-fuel/terrain requirements. The calculation
rejects intersections and touches at all thresholds, invalid or non-simple road
geometry, and invalid mapped-water geometries whose bounding boxes are near the
road. Missing water layers and failed evidence remain unknown. Coarse mapped
layers, uncalibrated corridor widths and the synthetic assumption of no unmapped
crossing do not establish road clearance, whole-cell access or walking access.

The private comparison counts added and removed unit/staging/edge-sequence
combinations relative to 50 m. It also checks whether every newly observed
combination contains a changed-screen edge associated with the same one mapped,
nonintersecting feature. This association is descriptive: bounded searches,
alternate routes, coincident or incomplete features and other filters can affect
the result, so it is not a causal or safety finding. Search incompleteness is
reported privately; a missing route is not proof no route exists.
The private audit separately inventories mapped-water conflicts encountered by
the original 50 m search, including their intersection counts and hypothetical
narrower screens, even when no route combination changes.

The audit contains case-level outcomes and source-derived feature and edge
identifiers. Its per-case results and catalog summary must remain private, and
must not be committed, served, published or provided to an actor. Stdout reports
completion only. The output is a fresh direct child of `/tmp`, mode 0700, with
an exclusively created mode-0600 audit. The process is restricted to one CPU,
including its children, and numerical-library thread counts are set to one.
Previously generated staging artifacts are not overwritten.

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_staging_water_sensitivity_v1.py \
  --opt-in-development-sensitivity --output-dir /tmp/expanded-staging-water-sensitivity-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_staging_water_sensitivity_v1.py -q -p no:cacheprovider
```

The tool requires the locally pinned private source, map, raster and staging
inputs and the raster-capable runtime documented in
[staging feasibility v1](expanded-road-staging-v1.md). It performs no full
incident evaluation or work-site assessment. © OpenStreetMap contributors,
ODbL; DERA © IECA, CC BY 4.0; ESA WorldCover 2021 / Copernicus Sentinel data.
