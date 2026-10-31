# Extended access v1: partial source observations

This is an offline **evidence candidate**, not a vehicle graph, an incident-day
input pack, or an approved data release. The corrected grid is EPSG:25830,
`[290000, 4035000, 332000, 4064500]`, 84 columns by 59 north-up rows of 500 m.
This supersedes the extent used by the earlier 57 × 46 candidate for this audit.
The requested extent uses MITECO Natura 2000 sites ES6170010 and ES6170004 as a
geographic proxy, extended 5 km north, east and west and aligned to the grid.
The supplied 2025 archive has SHA-256
`0b35e4f6ec7ab198f51cd66705a090a496d5b98f9203c825b499a9f8ebbb2cbd`;
the [official archive](https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/servicios/banco-datos-naturaleza/3-rn2000/n2000_2025_shp.zip)
and extent derivation are documented in the separate extended-domain provenance.
Protected-area polygons are neither a complete massif boundary nor water or
road evidence, and the 2025 designations are not a 2021 incident boundary.

## Road evidence

`/tmp/extended-access-acquisition/observations.json.gz` contains **92,945 source segments**
intersecting the corrected map and **1,389 cells** touched by at least one
segment. The other cells have no retained observation; they are not known to be
roadless or inaccessible. Each segment has an OSM way ID, segment index, its
original two node IDs, highway class and clipped EPSG:25830 line coordinates.
These IDs refer to the original segment: a clipped line endpoint need not be
at its original node. Crossings never create node IDs or junctions. A shared
source node is evidence of source topology, not a verified legal connection.

The source is the pinned `data/coastal_mobility_v1/source_snapshot.json.gz`
(SHA-256 `fd276b8a4a799cffedd268f485a97aaff5ff88d83d11726be0e23894d282f323`).
It contains highway ways intersecting the old WGS84 selection box
`[-5.345, 36.36, -5.13, 36.625]`, their nodes and selected tags, from OSM map
API tiles acquired on 2026-10-27. Tile URLs, dates and raw hashes are recorded in
`data/coastal_mobility_v1/acquisition.json`; the raw tile cache is absent here.
The tiles were not atomic, the data is retrospective, and OSM is community
mapped and potentially incomplete. This product retains segments only when
**both** original nodes are within that selection box; segments crossing its
edge are deliberately omitted even if some geometry is inside. A way extending
beyond that box cannot prove source coverage there.

The earlier raw tile acquisition covered WGS84 `[-5.42, 36.36, -4.96, 36.65]`.
The corrected extent reaches approximately west −5.351, south 36.437, east
−4.874 and north 36.712 in geographic coordinates. Thus the acquisition bbox
misses parts of the **north and east** of the corrected map. Its projected
footprint covers approximately **64.78%** of the map area: about 3,150 cells are
fully inside, 3,268 intersect, and 1,688 are outside. This is only the extent
of the historical acquisition requests: the raw tiles are absent, and this
footprint is not a recoverable road dataset or proof of road coverage.

The smaller retained snapshot selection-box footprint covers approximately **31.97%** of the
corrected map area: approximately 1,524 cells are completely inside it, 1,632
intersect it, and 3,324 are outside it. Both estimates use a 512-segment-per-side
projection of each bounding box; the percentages and counts are numerical
approximations, not a certified coverage polygon. In particular, being inside
the box does **not** establish complete road coverage, retention of every
boundary segment, or valid access. Observations include paths, private ways and
other non-motor roads. The artifact does not retain the complete access tags,
turn restrictions, barriers, direction, speeds or operational conditions, so it
must never be used as a routing graph or as evidence of fire-truck access.
No off-network connectors, crossings, inferred routes, or speed assumptions
are made. The existing filtered coastal exercise network is a different,
narrower product and does not cover the corrected extent.

**© OpenStreetMap contributors**, [ODbL 1.0](https://www.openstreetmap.org/copyright).
Source-derived geography is subject to rights review under [license-status.md](license-status.md). Keep raw and derived data under `/tmp` or an ignored runtime directory; do not commit or publish it. Any redistribution requires review of attribution and ODbL obligations.

## Bounded lattice acquisition, 2026-10-30

A deterministic 0.03-degree lattice spans the corrected projected grid using a
padded WGS84 envelope `[-5.352, 36.436, -4.873, 36.713]`. The acquisition follows
the existing map API approach: a failed oversized tile is split along its
longer side, with at most three attempts for other failures. The cache records
request bounds, response-body hashes, timing, attempts and statuses under
`/tmp/extended-access-lattice-v1/manifest.json`. Pending tiles are not covered.
The first-run manifest is preserved separately as `manifest-first-run.json`.

The first run stopped at the byte cap after 47.826 seconds: **101 of 160 root tiles
succeeded and 59 remain pending**; no splits were triggered. It received
100,000,000 response-body bytes, including a 6,050,710-byte partial response
that was discarded. The saved response headers add 57,586 bytes, and request,
TLS and protocol overhead was not measured. Thus this first run **exceeded the
requested 100 MB total transfer cap**, even though its body counter stopped at
100 MB. The code now counts response headers and reserves 1 MB for protocol
and request overhead; that reserve is an estimate, not a direct measurement of
wire bytes.

At the end of that first run, the independent audit checked the lattice and split tree, all successful raw
hashes and response-body accounting, way-node completeness within each tile,
identities repeated across tiles, and projected request-box overlap and gaps.
At that point, for the lattice alone, successful request boxes covered approximately **60.43%**
of map area; 2,940 cells are fully inside, 95 intersect partly and 1,921 do
not intersect. Including the earlier nine samples gives approximately **62.81%**,
with 3,016 full, 178 partial and 1,762 outside. The sample boxes overlap the
lattice, with approximately 44.65 million m² of summed projected overlap across
all request boxes, including outside the map. These are sampled-projection
estimates of request footprints, not certified road coverage. The padded
geographic envelope contains the sampled transformed boundary of the map.

At that point, across both acquisitions, the audit found 306,590 unique node IDs and 9,081
unique highway-way IDs; repeated node IDs agree on version and coordinate, and
repeated highway/coastline way IDs agree on version, node sequence and selected
highway/natural tags. Other tags are not compared. The audit found five unique
coastline ways in two endpoint components with four degree-one endpoints. Those
counts refer to whole OSM ways returned in the partial requests, which can extend
outside their request boxes. No line joining, polygon closure, surface
classification or legal routing is inferred. Further acquisition and independent
coastline, access and rights checks remain necessary.

### Resumed acquisition and routing blockers

A separately authorized resume rechecked cached tile hashes, then requested only
pending tiles. It added **98,973,436 response-body bytes plus 27,132 response
header bytes**, a total of **99,000,568 bytes**, in 37.674 seconds. The
additional limit was 100,000,000 response/header bytes and 180 seconds.
Request and TLS overhead is not included in this response/header total. The
run stopped on its reserved byte limit with **148 successful and 12 pending**
lattice tiles; all pending tile rectangles intersect the corrected map. They
occupy the southern row at approximately 36.436–36.466° N and 5.352–4.992° W;
the earlier samples overlap some parts of these boxes. Exact tile IDs and
bounds are recorded in the private audit output
`/tmp/extended-access-lattice-v1/resumed-combined-audit-final.json`.

Together with the nine earlier samples, the independently computed request-box
footprint covers approximately **93.3181%** of map area: 4,578 cells fully
inside, 88 partly intersecting and 290 outside. This is not proof of complete
OSM mapping or access. There are 634,980 unique source node IDs and 20,648
unique highway-way IDs across the successful tiles and samples. No conflicting
node version/coordinate or highway/coastline way version, node sequence and
selected class tag was detected. The footprint has 16 unique coastline ways,
one endpoint component and two degree-one endpoints; it is not a verified
closed shoreline or water polygon. All 4,956 surface cells remain unknown.

At that point no full-footprint directed network was built: 12 request tiles remained missing,
and the acquired source contains **243 restriction relations, four with members
absent** from the acquired elements. The existing
`eval/prepare_coastal_roads.py` filters classes and access and respects oneway
rules, but its edge-only graph does not encode turn restrictions and it compacts
source chains. A separate network would need to preserve source tags and mapped
endpoints, represent and enforce or conservatively exclude unresolved turn
restrictions, independently validate directed topology, and handle absent or
conflicting source members before it can meet the requested contract. The
snapshot is non-atomic, not a 2021 reconstruction, and neither a source road
nor a nominal graph edge establishes vehicle suitability. No connectors were
inferred.

### Completed requests, coastline and directed road candidate

A final separately authorized run obtained the 12 pending tiles. It transferred
**19,761,222 response-body bytes and 6,816 response-header bytes** in 7.218
seconds; request and TLS overhead was not measured. All **160 root requests**
succeeded. In the sampled projected-footprint audit, all 4,956 grid cells are
inside successful request boxes and the estimated uncovered area is zero. This
establishes request coverage, not complete or accurate OSM mapping. The OSM map
API can omit ways crossing a request box if no way node is inside; this limit
also applies to road-source coverage. The current
private manifest is `/tmp/extended-access-lattice-v1/manifest.json`; earlier
run manifests are preserved separately.

The combined source contains 16 distinct coastline ways. The audit follows
original node IDs and finds one connected, consistently directed chain of 1,130
nodes, with no detected branch or degenerate segment and two endpoints outside
the projected map. The chain intersects the rectangular map boundary four
times; portions can leave and re-enter the map. This establishes the topology
of the retrieved OSM ways, not proof that every actual shoreline, island, tidal
feature or inland water body is represented. The endpoints remain open; no
polygon was constructed and all surface cells remain unknown.

The source contains **262 restriction relations**, three with one or more
members absent from the acquired source elements. The private candidate
`/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz` retains
all 262 relation tags and members, full source tags and original node lists for
its included ways, and available source records for ways and nodes referenced
by restrictions. It also preserves tags for included nodes. It has 92,278
mapped nodes, 8,665 included ways and 168,948 directed arcs. Each arc joins two
consecutive original OSM nodes of the same way, **both inside or on the map
boundary**. The 100 eligible segments crossing the boundary are recorded
separately with their source nodes, way tags and directions as non-routable
evidence. They do not create graph edges or outside junctions. There are no
manufactured boundary nodes, crossing junctions or off-network connectors.
The previous v1 candidate allowed through-routing at outside nodes and was
removed from the private output directory; it must not be used.

The candidate reuses the existing coastal road filtering and direction policy.
To avoid silently ignoring turn restrictions, it **excludes every source way
referenced by a retrieved restriction and every arc incident to a restriction
via node**. This deliberately overblocks and does not execute turn relations;
426 referenced way IDs are excluded, and 3,286 barrier or via node IDs are
blocked in the source. Missing relation members remain recorded and unresolved.
OSM tags, absent tags, this filtering policy and a complete request footprint do
not establish legal access, operational fire-truck suitability, current closure
status, or complete real-world road coverage. This is a separate private
candidate, not an engine-integrated or operational route product. The source
is non-atomic and not a historical 2021 snapshot.

The validator finds **511 weak components**; the largest has 37,498 nodes.
From its smallest-ID node, 35,598 nodes are directionally reachable. This is a
source-graph diagnostic, not evidence that any particular mission route is
available. The exclusions and extent clipping can fragment real roads.

The independent validator checks the lattice and the earlier sample source
manifests, raw hashes, complete request footprint and duplicate source
identities, then compares each directed arc with source-node endpoints and a
separate GEOS segment/grid intersection. It verifies included way and node tags
and exact restriction records. It independently checks both endpoints of
every arc are covered by the grid and verifies the boundary-only evidence
against source segments. In particular, outside nodes 1123524259 and 915674901
are absent from the routing graph and retained only as boundary evidence.
The candidate's source identity is the SHA-256
of canonical JSON containing the lattice and sample manifest SHA-256 values.

## Coastal surface assessment

The [OSM coastline convention](https://wiki.openstreetmap.org/wiki/OSM_Inspector/Views/Coastline)
places land to the left and water to the right of each directed coastline way.
For the retrieved chain, a GEOS check found a simple line, four point
intersections with the map boundary, and three valid boundary-closed faces.
Each counted crossing is checked for a true inside/outside side change;
tangent and ambiguous boundary contacts fail closed.
The two outside endpoints and direction are consistent across the 16 source
ways. On 679 in-domain segment probes, each left and right side fell into a
consistent face; two faces were on the left and one on the right. This is an
assessment of the retrieved OSM line and numerical probes, not proof that all
shoreline or inland water is represented.

**No surface cells are classified.** The [OSM map API documentation](https://wiki.openstreetmap.org/wiki/Api06)
states that a way crossing a bounding box can be omitted if none of its nodes
is inside. Successful tile requests therefore cannot prove that all crossing
coastline ways were captured; the acquisition is also non-atomic. Coastline
land-side geometry alone does not establish whether a cell contains inland
water. The fail-closed private assessment at
`/tmp/extended-access-lattice-v1/coastal-surface-assessment-v1.json` keeps all
4,956 cells unknown, including shoreline and partial cells. No absence of fuel
is used to infer water. OSM geometry is approximate and from 2026, not a
measured or validated 2021 shoreline. Surface classification requires a source
and coverage method that resolve these gaps, with independent topology,
whole-cell and inland-water checks.

The acquisition directories are private (mode 700), and their files are mode
600. Newly created private output files are written mode 600 using atomic replacement;
symlink or non-regular targets are rejected. The acquisition
runner creates its dedicated directory mode 700 and uses a restrictive umask
for curl. Download bodies and headers use uniquely and exclusively created
private files passed to curl as inherited descriptors. Publication checks the
file identity and link count and rejects existing cache entries, including
symlinks and hard links. Each request has a unique attempt identifier. The
private manifest records an in-flight transfer reservation before curl and a
receipt before publishing either file. The receipt also pins the temporary file
identities. If a process stops between creating a publication hard link and
removing its temporary link, recovery checks the recorded inode and content,
then removes only the matching temporary link. Older receipts without inode
metadata require a unique matching temporary link and the recorded content
hash. Unexpected links or mismatched content fail closed. A complete published
pair is recovered; an incomplete publication is charged once and retried under
a new identifier. Recovered ordinary failures obey the same three-attempt
limit as uninterrupted failures. If the process stopped before recording a
receipt, the remaining reserved byte and request-time allowance is charged conservatively;
a fresh authorized allowance may be needed to continue. An orphan from the
earlier non-journaled naming scheme is not trusted or overwritten: its
remaining old allowance is charged conservatively and resumption requires a
fresh authorized allowance. No raw or derived
geography belongs in repository changes until the rights review described in
[license-status.md](license-status.md).

## Coastline and unknowns

All **4,956 cells** remain surface-unknown. A road line, elevation, protected
site, lack of an observation, and the edge of the map do not prove land, sea,
inland water, access, or a firebreak. The [official CNIG/IHM coastline](https://centrodedescargas.cnig.es/CentroDescargas/busquedaRedirigida.do?ruta=PUBLICACION_CNIG_DATOS_VARIOS%2Flineas_limite%2FLC.zip)
is a potentially useful 1:50,000 shapefile, dated 14 October 2022 on the source
page. IHM requires attribution; the page also gives derivative attribution
language. It is a line product and cannot by itself establish complete sea
polygons or an unambiguous tidal-zone classification. The
[OSM-derived land polygons](https://osmdata.openstreetmap.de/data/land-polygons.html)
are another candidate, but are derived from community coastline ways and may
include automatic repairs; their actual edition, topology, inland-water
semantics and coverage must be checked before use.

Normal sandbox DNS failed for the CNIG and OSM hosts. With user-authorized
network escalation, nine small OSM map API tiles were fetched on 2026-10-30;
URLs, request bounding boxes, response hashes, sizes and server dates are in
`data/extended_access_v1/tile-acquisition.json`. The raw responses are under
`/tmp/extended-access-acquisition/`, not in this repository. These samples
include new northern/eastern areas and two coastal tiles. Separate per-tile
audits found five coastline ways intersecting the corrected map: one in the
western coastal sample and four in the eastern sample. Each sample's coastline
has two degree-one way endpoints; no polygonization or gap filling was done.
These are partial OSM shoreline observations, not verified complete coastlines
or land/sea polygons. The samples are not a complete acquisition of the map;
ways can extend beyond their requested tile, and overlapping, non-atomic tiles
are not merged. Per-tile highway counts include private and non-motor ways and
do not imply access or road completeness.

The bounded Overpass request returned HTTP 406 from `overpass-api.de`; the
`overpass.kumi.systems` and `overpass.private.coffee` mirrors timed out after
45 seconds with no response. The user reported a TLS EOF for the official CNIG
coastline download. No official shoreline geometry or complete OSM coastline
polygon was acquired. A future source must be pinned with acquisition time, hash, extent,
license and applicable attribution; polygon validity and coverage must be
independently verified, including holes, inland water, gaps, tidal ambiguity and
a conservative whole-cell policy. Boundary and partial cells must remain
unknown absent adequate evidence. Neither source availability in 2021 nor
operational suitability has been established.

## Reproduction and validation

The compiler uses Python, `pyproj`, and PROJ with network access disabled. The
independent validator additionally uses Shapely/GEOS. The runtime actually used
was pyproj 3.8.0 / PROJ 9.8.1 and Shapely 2.1.2 / GEOS 3.13.1. The suggested
GIS venv has pyproj but lacks Shapely; the system Python used here has both.
From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py compile \
  --output /tmp/extended-access-v1-rebuilt.json.gz
cmp /tmp/extended-access-acquisition/observations.json.gz /tmp/extended-access-v1-rebuilt.json.gz
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py validate \
  --output /tmp/extended-access-acquisition/observations.json.gz
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py audit-tiles \
  --manifest data/extended_access_v1/tile-acquisition.json
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py audit-lattice \
  --manifest /tmp/extended-access-lattice-v1/manifest.json \
  --sample-manifest data/extended_access_v1/tile-acquisition.json
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py validate-roads \
  --manifest /tmp/extended-access-lattice-v1/manifest.json \
  --sample-manifest data/extended_access_v1/tile-acquisition.json \
  --output /tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/extended_access_v1.py validate-surface \
  --manifest /tmp/extended-access-lattice-v1/manifest.json \
  --sample-manifest data/extended_access_v1/tile-acquisition.json \
  --output /tmp/extended-access-lattice-v1/coastal-surface-assessment-v1.json
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_extended_access_v1.py -q -p no:cacheprovider
```

Validation checks the compressed snapshot hash, reconstructs every eligible
source segment, independently intersects source geometry using GEOS, checks
source way/node identity, geometry, segment order and all intersected cells, and
rejects unbacked or omitted segments and unsupported surface claims. This checks
the candidate against the pinned snapshot, not the correctness, completeness,
legal access or historical accuracy of the underlying OSM source. The separate tile audit checks raw file hashes, source way-node completeness,
map intersections and coastline endpoint degrees per tile; it does not prove
coastline completeness or merge source versions. Tests cover
private/path observations, distinct crossing IDs, clipped endpoints, boundary
cells, missing source nodes and deliberate graph/coverage mutations. The gzip
file SHA-256 is `0dc62cecc69b8b167ba6b344ee4e61c97c09b3115de0a92b60ea39ce2801037d`.
