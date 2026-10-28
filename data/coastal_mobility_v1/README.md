# Coastal road exercise fixture v1

`network.json` is a real, directed OpenStreetMap road extract in **EPSG:25830**:
4,298 nodes, 8,878 directed arcs, 2,604,364 bytes (2.60 MB; above the preferred
2 MB target to preserve approaches and original mission-access vertices).
Canonical graph SHA256:

`12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16`

Hash `json.dumps(payload_without_sha256, sort_keys=True, separators=(',', ':'),
allow_nan=False).encode('utf-8')`, using default `ensure_ascii=True`. The JSON file
has a trailing newline, so its file hash differs from its graph hash.

## Source and license

**© OpenStreetMap contributors, Open Database License (ODbL) 1.0.** This derived
database is distributed under ODbL 1.0. Display attribution with maps and retain
the database license when redistributing it. See
<https://www.openstreetmap.org/copyright> and
<https://opendatacommons.org/licenses/odbl/1-0/>.

The preferred IGN/CNIG provincial Málaga download could not be acquired: the
official HTTPS connection failed with a TLS EOF and the web fetch returned 502.
Overpass returned 406 or timed out. The fallback is the **official OSM map API**,
not invented geometry. Twenty-one bounded successful map tiles were acquired
on 2026-10-27, with adaptive bisection for the API's 50,000-node limit. Each tile's
URL, acquisition times, raw byte count and SHA256 are in `acquisition.json` and
`provenance.json`. This is current OSM data, not a historical 2021 road network.

The acquisition bbox was [-5.42,36.36,-4.96,36.65] in WGS84. The graph clip is
[-5.345,36.36,-5.13,36.625], covering the towns and western forest approaches.
The westward extent retains the real A-377/Casares connection to MA-8302.
Projected final geometry bounds are
**[290015.7,4026188.4,309111.4,4055676.8]** (west,south,east,north).
The eastern portion of the active grid is outside this graph's coverage.

## Offline regeneration

`source_snapshot.json.gz` is an included, hash-pinned minimized source snapshot:
OSM highway ways intersecting the graph bbox, their original geometry nodes,
relevant tags and retrieved restriction relations. Raw API downloads, including
unrelated map features, are kept only in ignored `.runtime/` under this folder.

From the repository root, with Python and pyproj installed:

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/prepare_coastal_roads.py
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m unittest discover \
  -s tests -p test_coastal_road_data.py -v
```

The default preparation is CPU-only and offline, and reproduces the network and
provenance byte for byte with the recorded pyproj/PROJ runtime. No episode needs
an external call. `--output data/coastal_mobility_v1/.runtime/rebuild` writes a
comparison copy. `--acquire` explicitly requests fresh OSM map tiles (using the
existing verified raw cache when available); `--snapshot-from-cache` reconstructs
the minimized snapshot from that cache without making network requests. Remove
or relocate cached tiles only when deliberately acquiring a different snapshot.
Changing source/runtime/script pins is a reviewed fixture update, not an episode
action. Acquisition labels include an optional separately cached Marbella node.

## Filtering and topology

Only supported motor road classes and conservatively surfaced tracks enter the
graph. Unknown tracks, footpaths, restricted/private motor access, ambiguous
conditional directions, fords, unverified gates and explicit vehicle dimension
limits are excluded. Specific motor access tags override broader access tags;
there is no assumed emergency override. Speeds are **synthetic conservative class
defaults**, not measurements or historical travel times. Surface/class/access
tags do **not** establish that a fire truck can use a road.

Only shared OSM node IDs create junctions. Grade-separated geometric crossings
are never joined. Clipping introduces source-specific boundary nodes. Same-class
unbranched chains are contracted; projected Douglas-Peucker simplification has a
2 m tolerance and preserves graph endpoints. Original source vertices within
150 m of an interior grid-cell centre are retained for node-based mission access.
Every two-way link has an explicit reversed arc and reversed coordinates. Length
is recomputed on final projected geometry. No random sampling or fabricated
connectors are used. The largest weak component contains all three exercise
anchors; 182 disconnected components are omitted from the engine graph and remain
available in the source snapshot. Turn-restriction relations are retained as
source evidence but cannot be represented in this edge-only contract, and are
**not enforced**. Routes need independent legal and operational validation.

## Integration and measured coverage

The following are exercise street-node candidates, not verified safe parking,
fire stations, historical deployments or approved staging grounds:

- Estepona: `osm:n1676609079`, [307642.5,4033433.4], 14.239 m from the parent's anchor.
- Casares: `osm:n7179050537`, [296190.2,4035655.8], 9.009 m from the parent's anchor.
- Sabinillas: `osm:n4444590544`, [298048.7,4027400.4].

The nearest nodes to the two parent anchors belong to the main connected
component; small disconnected islands do not dominate those snaps. Directed
reachability and exact candidate/component counts are recorded in provenance.
There are **62 interior 500 m cell centres** with an Estepona-reachable original
road node within 150 m, listed in
`coverage.interior_cell_centres_with_reachable_node_within_150m`. These are
geographic candidates only; the task generator must still check fuel and other
eligibility conditions. One example is centre [301250,4046750] (row15,col8),
node `osm:n3854610860` at [301170.8,4046669.9], gap112.644 m.

Connected road access from the Estepona candidate, using shortest **distance**:

- [301250,4046750]: road57,888.354 m; final geometric/node gap112.644 m.
- [306250,4046250]: road62,863.265 m; final gap4,472.320 m — inadequate for either limit.
- [303250,4045250]: road62,863.265 m; final gap1,328.626 m — within the walking distance
  limit geometrically, but beyond the150 m suppression limit. No traversable
  walking route is established by a Euclidean gap.

The latter two snap to original node `osm:n3860480180` at [301931.3,4045087.9].
The graph therefore provides **partial coverage**, not adequate access to every
requested target. Excluded unknown tracks, fords and gates are not repaired by
inventing truck routes. Shortest-time routes in the parent may differ from these
shortest-distance measurements.

`town_labels.json` contains actual OSM named settlement nodes for Estepona,
Casares, Manilva and Marbella, with WGS84/projected coordinates, source URLs and
element timestamps. Marbella was resolved to municipal relation340756's actual
admin-centre node1081751552, rather than using a geocoder municipality centroid.
These labels contain no invented population locations or population counts.
