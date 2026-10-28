# Tactical world v2

The generated concept guides the overhead composition, symbols and color
hierarchy, not the geography or fire ground truth. The first v2 pass is visual:
the existing terrain coordinates, synthetic population groups and simulation
mechanics do not change.

V1 is frozen locally in `.runtime/v1-freeze-20261027/`: a recoverable source
archive, file-by-file SHA-256 manifest, pending binary diff and pinned runtime
inputs. This includes the previously reviewed, uncommitted 3D prototype. It is
not a Git commit, release tag or published dataset. Extract the archive into a
separate directory to inspect/rebuild v1; never restore it over current work.
The same archive, manifest and patch are also backed up in this Codex task's
dated visualization directory, outside `/tmp`, with the archive hash verified.

`design_brief.json` defines the matching criteria. Final browser comparison
must distinguish visual improvements from remaining source-coverage gaps.
Full Sierra Bermeja/coastal heights and real settlement/facility inputs remain
a subsequent, versioned geography step, not invented scenery in this design.

## Reproducible road overlay

The default road compiler now reads the checked-in, hash-pinned normalized source
bundle in `snapshots/`. Run `python eval/prepare_display_roads_v2.py` offline with
pyproj 3.8.0 / PROJ 9.8.1 to reproduce the current asset and provenance. The bundle
is approximately 1.13 MB compressed and needs no ignored `.runtime` cache.
Acquisition is a separate explicit operation; it never runs during an episode.

The original 2026-10-27 raw API tiles were lost when the temporary checkout was
cleared, and a current response did not match their recorded checksums. We did
not change those acquisition pins or call the original source recovered. The
new source was acquired on 2026-10-28 and reproduces the prior displayed road
geometry. Its current-map metadata is different. The prior asset and original
provenance remain byte-preserved in `archive/20261027/`.

See `snapshots/README.md` for coverage, licence and source availability. This
overlay remains display-only; it does not repair routing coverage, establish
fire-truck passability or change wildfire physics.
