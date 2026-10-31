# Expanded population and destination anchors v1

`eval/expanded_population_anchors_v1.py` creates a **private, unqualified
mapping candidate** for the twelve original synthetic incident-evaluation
cases. It does not alter the original catalog, register a new scenario, modify
cell-access masks, dispatch resources or establish a historical deployment.
Every candidate case retains its original case ID and SHA-256. The output has
no copied weather or event schedules and is not a runnable catalog.

The compiler requires the original catalog and coastal road graph pins, the
original compressed coastal source snapshot, the complete private expanded
road candidate, and the independently pinned reviewed v4 graph/source pair.
It reruns the independent raw-tile road audit, validates the source-only and
exercise graph conversions, validates the post-anchor subdivision against its
parent, and checks the pinned original node and source records. The origin
guard checks each proposed exact point against all candidate source segments,
including disabled segments. A matched road is not evidence of full-cell
access, legal permission or vehicle suitability.

For each group current position, group home and destination position, an anchor
is proposed only if:

- the exact rounded original position identifies one original graph node;
- the same OSM node ID exists in both original and expanded source records,
  with identical original longitude and latitude;
- rounding its expanded projected position to 0.1 m reproduces the original
  position, with the displacement within the diagonal rounding bound;
- exactly one reviewed graph node lies within 0.15 m, and it is that same
  source node at the exact candidate position;
- the origin guard finds one unambiguous local source component, and the
  reviewed exercise graph has both incoming and outgoing engine-mode edges.

The graph's mode permissions are source-filtered **exercise assumptions**.
The compiler validates their provenance against the full candidate, including
known restriction exclusions and the conservative access, barrier, class,
width and direction policy. Missing tags and retrieved restrictions do not
establish complete legal access; unresolved legal restrictions and current
road conditions remain unknown. A missing, non-unique, changed, ambiguous or
mode-ineligible node is recorded as `unresolved` without a replacement node or
connector. No nearest different node is accepted, even if it is inside 0.15 m.

The candidate separately records graph-only directed engine connectivity for
each group current-position/destination pair. A graph path says only that
permitted exercise edges connect the nodes; it is not an evacuation plan,
road-clearance finding or accessibility assertion. A missing graph path does
not establish real-world inaccessibility.

## Result for the pinned input

The private candidate contains twelve cases, 24 group current-position
anchors, 24 destination anchors and 24 separately checked group-home anchors.
All 72 fields resolve to their original source-node IDs; none is unresolved.
Four distinct source nodes cover these fields. The maximum change from an
original rounded position to its exact source-node coordinate is **0.065577 m**.
That change is a labelled synthetic exercise adaptation, not a geographic
measurement or historical resiting. In the one occupied-at-handover case, the
group's current position and home remain separate anchors; its original
arrival state and destination reference are retained in the mapping.

Of the 48 group-to-destination combinations, the reviewed exercise graph has
47 directed engine-mode paths and one identical source node. This is a
source-graph topology diagnostic only. The source input's cell access remains
unknown, the original graph whitelist is not widened, no walk access is
inferred, and none of these results qualifies a source scenario for operation.

The candidate SHA-256 is
`5ea3fcb157146a61072dba4818c7b6d224a5242d50ed5efbd5a28fc822ce39d4`.
The private output is `/tmp/expanded-population-anchors-v1-secure/candidate.json`,
mode 0600 under a mode-0700 directory. It contains source-derived positions and
node identities and must not be committed, served or published. © OpenStreetMap
contributors; [ODbL 1.0](https://www.openstreetmap.org/copyright). See
[license status](license-status.md) and [extended access](extended-access-v1.md)
for source and redistribution limitations.

## Reproduction

Requires the pinned private lattice and sample caches, candidate, original
catalog, original coastal data, v3 source/exercise graphs and v4 post graph;
Shapely 2, GEOS `dwithin`, pyproj and PROJ are required. The output directory
must be a new, dedicated direct child of `/tmp` named
`expanded-population-anchors-v1` or using that prefix with a hyphenated suffix.
The existing `private_destination` guard checks that it is owned by the caller,
mode 0700, has no symlink ancestors and is outside every Git worktree. The
direct-child restriction excludes nested served assets, public or static
directories, even when owned by the caller. Existing output files, symlinks
and hardlinks are refused. A repeated run needs a fresh directory; an audit
failure may leave an empty reserved directory. Do not configure a server to
serve `/tmp` or this private directory: filesystem checks cannot detect every
possible same-user or privileged server configuration.

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_population_anchors_v1.py \
  --output-dir /tmp/expanded-population-anchors-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_population_anchors_v1.py -q -p no:cacheprovider
```

Tests cover original identity and coordinate checks, unique-neighbor and
crossing ambiguity, missing mode permissions, the occupied group's separate
home, original case hashes, Git worktrees, served paths, links, private output,
write-once output, and reconstruction from the full pinned source when the private inputs are present. The independent acquisition
audit checks retrieved OSM topology, not source completeness, legal status,
2021 conditions or physical access.
