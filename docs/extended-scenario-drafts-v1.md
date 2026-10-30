# Expanded-map scenario drafts

`eval.extended_scenario_drafts_v1` derives twelve **private, unqualified**
exercise drafts from the pinned incident-evaluation catalog. It preserves the
geographic centres of the old ignition cells, incident sector and report
locations on a north-up, aligned 500 m EPSG:25830 grid. Source cases and their
hashes are left untouched. Unit positions are already in projected metres and
are not moved by the grid remap.

The drafts keep the old resource and road assumptions; a widened road graph,
terrain and fuel layers, scenario preflight, delayed-event checks and full-day
replay still need to be qualified before any draft can be run or scored. In
particular, the old road-edge whitelist is not automatically evidence that a
new network is safe. These drafts are not registered with the environment or
compatible with the legacy catalog bridge. They retain future events and other
private scenario information and must never be exposed to an agent.

Ten drafts start with a synthetic wind from the south, southeast or southwest;
two begin with other directions. Wind vectors point toward the direction of
travel, with positive x east and positive y south. Their units are relative
model units rather than measured metres per second. The selection is a design
choice, not a frequency estimate for Sierra Bermeja; later scheduled shifts
remain diverse. A northward initial wind cannot guarantee that fire never
reaches the southern boundary.

The builder accepts complete catalog and grid objects; it verifies the original
catalog and scenario hashes and refuses misaligned grids or grids that do not
cover the original domain. Its output is explicitly `unqualified-not-runnable`.
This is preparation only, not an expanded-map release, calibration result,
independent held-out set or historical reconstruction.
