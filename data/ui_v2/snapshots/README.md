# Portable display-road source snapshot

This directory contains a normalized OpenStreetMap (OSM) source bundle for the
small display-road extent. The data was acquired on **28 October 2026**, from
11:27:13 to 11:27:30 UTC, using the [OSM map API](https://wiki.openstreetmap.org/wiki/API_v0.6#Retrieving_map_data_by_bounding_box:_GET_/api/0.6/map).
The 16 requests are separate current-map responses, not an atomic snapshot or a
reconstruction of roads during the 2021 fire.

This is a new source revision. It does not restore the lost 27 October 2026 raw
cache pinned in `data/coastal_mobility_v1/acquisition.json`, and its hashes must
not be described as hashes of that earlier acquisition.

The target UI rectangle is EPSG:25830 `[297000, 4036500, 315500, 4054500]`.
Its outward-transformed WGS84 request envelope is
`[-5.269930, 36.452281, -5.058884, 36.618189]` in west, south, east, north
order. All 16 tiles covering that envelope were retrieved. The bundle retains
supported, non-area highway ways that intersect the UI rectangle and their
complete referenced node sequences, including nodes outside the rectangle. It
preserves compiler-input versions, coordinates, way tags and timestamps; it
does not invent connectors or clip source ways. The OSM map API's selection and
OSM's own coverage can still omit roads, including ways crossing an area without
a selected node. This is display context, not a routing network or a claim of
road availability.

- `display-roads-manifest.json` records the requests, timestamps, response
  sizes and SHA-256 hashes, the normalization and source licence. Its SHA-256 is
  `b21852b130cf02edebb15eab03c40d4b3ae4d8c805d11ee317233ccbbe9107d5`.
- `display-roads-source.json.gz` is the portable normalized derivative:
  1,133,663 compressed bytes (about 1.13 MB) and 7,846,625 decompressed bytes.
  Its compressed SHA-256 is
  `d6e1dac8bc97190f469e31736e6600e0020105aba43a70c716d3628225b8e401`.
- The 16 original responses totaled 30,673,317 raw bytes (about 30.67 MB).
  Their individually gzip-compressed sizes totaled 2,697,560 bytes. The full
  responses were not retained; the manifest hashes describe them, while the
  bundle contains only the normalized road subset.

The acquisition limits were 60,000,000 total raw bytes, 20,000,000 total
compressed bytes, and 20,000,000 raw bytes per response. The portable bundle
has a 2,000,000-byte compressed limit. Acquisition fails if these limits or
complete tile coverage cannot be met; it does not silently crop or sample the
area. The helper in `eval/display_road_snapshot.py` loads the bundle offline
using an independently supplied manifest hash, validates compressed and
uncompressed sizes and hashes, and returns the source nodes and ways. A later
API download is current data and cannot be expected to reproduce these bytes.

## Attribution, licence and source availability

**© OpenStreetMap contributors.** OSM data is available under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
See [OSM copyright and attribution](https://www.openstreetmap.org/copyright)
and the [OSM Foundation licence guidance](https://osmfoundation.org/wiki/Licence).
Display appropriate attribution when using this data and make the licence clear;
redistribution of database derivatives must meet the ODbL's applicable
share-alike and database-availability requirements. These terms do not license
unrelated imagery or terrain.

The normalized source database and its manifest are the two files in this
folder. Anyone receiving an applicable derivative should be able to obtain the
corresponding normalized source bundle in a usable form, with its licence and
attribution, as required by the ODbL. This local repository is not itself a
public download service; if a derivative is shared, arrange source availability
as required. The [official OSM API documentation](https://wiki.openstreetmap.org/wiki/API_v0.6)
explains how to obtain current OSM data, but it is not a source for the discarded
2026-10-28 response bytes.
