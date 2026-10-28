"""Acquire and validate a portable, current OSM display-road source snapshot.

The bundle is a normalized derivative, not an archive of the full API responses.
Nothing is written until every covering request and validation has succeeded.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from eval import prepare_display_roads_v2 as roads

EXTENT = roads.EXTENT
MAX_TOTAL_RAW = 60_000_000
MAX_TOTAL_COMPRESSED = 20_000_000
MAX_TILE_RAW = 20_000_000
MAX_BUNDLE_COMPRESSED = 2_000_000
MAX_BUNDLE_RAW = 20_000_000
MAX_MANIFEST = 1_000_000
MAX_DEPTH = 8
OUTPUT_DIR = roads.ROOT / 'data/ui_v2/snapshots'
API = 'https://api.openstreetmap.org/api/0.6/map.json?bbox='
VERSION = 'display-road-snapshot-v1'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def outward_bounds(extent=EXTENT):
    from pyproj import Transformer, network
    network.set_network_enabled(False)
    transform = Transformer.from_crs('EPSG:25830', 'EPSG:4326', always_xy=True, allow_ballpark=False)
    bounds = transform.transform_bounds(*extent, densify_pts=1001, errcheck=True)

    scale = 1_000_000
    return [math.floor(bounds[0] * scale - 1) / scale,
            math.floor(bounds[1] * scale - 1) / scale,
            math.ceil(bounds[2] * scale + 1) / scale,
            math.ceil(bounds[3] * scale + 1) / scale]


def split_bounds(bounds):
    west, south, east, north = bounds
    if east - west >= north - south:
        middle = (west + east) / 2
        return [[west, south, middle, north], [middle, south, east, north]]
    middle = (south + north) / 2
    return [[west, south, east, middle], [west, middle, east, north]]


def initial_tiles(bounds):

    west, south, east, north = bounds
    return [[west + (east-west)*index/4, south + (north-south)*row/4,
             west + (east-west)*(index+1)/4, south + (north-south)*(row+1)/4]
            for index in range(4) for row in range(4)]


def acquire(bounds, *, opener=urlopen, sleeper=time.sleep, monotonic=time.monotonic):
    """Fetch every covering tile; fail closed on limits, incompleteness or HTTP errors."""
    documents, records = [], []
    totals = {'raw': 0, 'compressed': 0}
    last_request = None
    started = now()

    def request_tile(tile, depth):
        nonlocal last_request
        if depth > MAX_DEPTH:
            raise ValueError('OSM subdivision depth exceeded; coverage incomplete')
        url = API + ','.join(format(coordinate, '.12f').rstrip('0').rstrip('.') for coordinate in tile)
        if last_request is not None:
            sleeper(max(0.0, 1.0 - (monotonic() - last_request)))
        last_request = monotonic()
        requested = now()
        request = Request(url, headers={'User-Agent': 'wildfire-display-road-snapshot/1.0',
                                        'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        try:
            with opener(request, timeout=60) as response:

                limit = min(MAX_TILE_RAW, MAX_TOTAL_RAW - totals['raw'])
                raw = response.read(limit)
                totals['raw'] += len(raw)
                if len(raw) == limit:
                    raise ValueError('Raw acquisition budget or per-tile limit exceeded; coverage incomplete')
                compressed_size = len(gzip.compress(raw, mtime=0))
                totals['compressed'] += compressed_size
                if totals['compressed'] > MAX_TOTAL_COMPRESSED:
                    raise ValueError('Aggregate compressed acquisition budget exceeded; coverage incomplete')
                server_date = response.headers.get('Date')
        except HTTPError as exc:

            message = exc.read(min(4096, max(0, MAX_TOTAL_RAW - totals['raw'])))
            exc.close()
            totals['raw'] += len(message)
            totals['compressed'] += len(gzip.compress(message, mtime=0))
            if totals['raw'] > MAX_TOTAL_RAW or totals['compressed'] > MAX_TOTAL_COMPRESSED:
                raise ValueError('Acquisition budget exceeded on error response; coverage incomplete') from exc
            if exc.code != 400 or b'too many nodes' not in message.lower():
                raise
            for part in split_bounds(tile):
                request_tile(part, depth + 1)
            return
        document = json.loads(raw)
        if not isinstance(document, dict) or not isinstance(document.get('elements'), list) or 'remark' in document:
            raise ValueError('Invalid or incomplete map response; coverage incomplete')
        records.append(dict(url=url, bbox_wgs84=tile, requested_at=requested, retrieved_at=now(),
                            server_date=server_date, bytes=len(raw), sha256=roads.digest(raw),
                            element_count=len(document['elements']), gzip_bytes=compressed_size))
        documents.append(document)
        print(f"covered tile {len(records)}: {len(raw)} bytes; total {totals['raw']}", file=sys.stderr, flush=True)

    for tile in initial_tiles(bounds):
        request_tile(tile, 0)
    return documents, records, dict(started_at=started, completed_at=now(), raw_bytes=totals['raw'],
                                     compressed_bytes=totals['compressed'])


def normalize(documents, extent=EXTENT):
    from pyproj import Transformer, network
    network.set_network_enabled(False)
    projector = Transformer.from_crs('EPSG:4326', 'EPSG:25830', always_xy=True, allow_ballpark=False)
    nodes, ways, duplicates = roads.collect_elements(documents)
    selected, required = [], set()
    for way in sorted(ways.values(), key=lambda way_record: way_record['id']):
        if way['tags'].get('highway') not in roads.KINDS or way['tags'].get('area') == 'yes':
            continue
        points = []
        for identifier in way['nodes']:
            if identifier not in nodes:
                raise ValueError('Missing source way node; cannot invent geometry')
            node = nodes[identifier]
            point = projector.transform(node['lon'], node['lat'], errcheck=True)
            if not all(math.isfinite(value) for value in point):
                raise ValueError('Nonfinite source geometry')
            points.append(point)
        if roads.clip_line(points, extent):
            selected.append(dict(type='way', **way))
            required.update(way['nodes'])
    if not selected:
        raise ValueError('No supported roads intersect extent')
    normalized = dict(version=VERSION, extent=list(extent), crs='EPSG:25830',
                      elements=[dict(type='node', **nodes[index]) for index in sorted(required)] + selected)
    return normalized, duplicates


def prepare(documents, records, acquisition, bounds, *, extent=EXTENT):
    normalized, duplicates = normalize(documents, extent)
    raw = roads.canonical(normalized) + b'\n'
    if len(raw) > MAX_BUNDLE_RAW:
        raise ValueError('Normalized snapshot exceeds decompressed cap')
    compressed = gzip.compress(raw, mtime=0)
    if len(compressed) > MAX_BUNDLE_COMPRESSED:
        raise ValueError('Normalized snapshot exceeds compressed cap; no sampling permitted')
    nodes, ways, _ = roads.collect_elements([normalized])
    manifest = dict(version=VERSION, created_at=now(), extent=list(extent), crs='EPSG:25830',
        aoi_wgs84=bounds, acquisition=acquisition, requests=records,
        source=dict(name='OpenStreetMap current map API', url='https://www.openstreetmap.org/copyright',
                    attribution='© OpenStreetMap contributors', license='ODbL-1.0',
                    license_url='https://opendatacommons.org/licenses/odbl/1-0/',
                    temporal_scope='Current, non-atomic API tile acquisition; not a 2021 historical reconstruction.'),
        normalization=dict(description='Derived subset, not full API responses. Highest version by OSM type/id; equal-version conflicts fail. Includes supported non-area highway ways intersecting the extent and all of their original referenced nodes. Preserves compiler-input node id/version/coordinates and way id/version/full node references/raw tags/timestamp; no invented connectors or clipping of source geometry.',
                           node_count=len(nodes), way_count=len(ways), duplicate_source_records=duplicates,
                           serialization='Sorted canonical JSON, UTF-8, trailing LF; deterministic gzip mtime=0.'),
        bundle=dict(path='display-roads-source.json.gz', gzip_bytes=len(compressed),
                    gzip_sha256=roads.digest(compressed), bytes=len(raw), sha256=roads.digest(raw)))
    return compressed, manifest


def read_regular_bounded(path, limit):
    """Read at most limit plus one byte, rejecting symlinks and special files."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError('Snapshot input cannot be opened as a regular file') from error
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Snapshot input must be a regular file')
        return source.read(limit + 1)


def load_snapshot(manifest_path, expected_manifest_sha256):
    """Load only after verifying an independently supplied manifest pin and both bundle hashes."""
    path = Path(manifest_path)
    data = read_regular_bounded(path, MAX_MANIFEST)
    if len(data) > MAX_MANIFEST or roads.digest(data) != expected_manifest_sha256:
        raise ValueError('Snapshot manifest exceeds cap or does not match independent SHA256 pin')
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get('version') != VERSION:
        raise ValueError('Invalid snapshot manifest version')
    bundle = manifest['bundle']
    if bundle['path'] != 'display-roads-source.json.gz':
        raise ValueError('Invalid snapshot bundle path')
    if type(bundle['gzip_bytes']) is not int or not 0 < bundle['gzip_bytes'] <= MAX_BUNDLE_COMPRESSED:
        raise ValueError('Invalid compressed bundle size')
    if type(bundle['bytes']) is not int or not 0 < bundle['bytes'] <= MAX_BUNDLE_RAW:
        raise ValueError('Invalid decompressed bundle size')
    bundle_path = path.parent / bundle['path']
    compressed = read_regular_bounded(bundle_path, MAX_BUNDLE_COMPRESSED)
    if len(compressed) != bundle['gzip_bytes'] or roads.digest(compressed) != bundle['gzip_sha256']:
        raise ValueError('Compressed snapshot size or SHA256 mismatch')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_BUNDLE_RAW + 1)
    if len(raw) != bundle['bytes'] or roads.digest(raw) != bundle['sha256']:
        raise ValueError('Decompressed snapshot size or SHA256 mismatch')
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get('version') != VERSION or document.get('crs') != 'EPSG:25830' or document.get('extent') != manifest.get('extent') or not isinstance(document.get('elements'), list):
        raise ValueError('Invalid normalized snapshot schema')
    if document['extent'] != list(EXTENT) or manifest.get('crs') != 'EPSG:25830':
        raise ValueError('Snapshot extent or CRS differs from supported UI extent')
    elements = document['elements']
    for element in elements:
        if not isinstance(element, dict):
            raise ValueError('Invalid normalized element')
        kind = element.get('type')
        required = ({'type', 'id', 'version', 'lon', 'lat'} if kind == 'node' else
                    {'type', 'id', 'version', 'nodes', 'tags', 'timestamp'} if kind == 'way' else set())
        if not required or set(element) != required:
            raise ValueError('Invalid normalized element fields')
        if kind == 'way' and (not isinstance(element['tags'], dict) or
                any(not isinstance(key, str) or not isinstance(value, str) for key, value in element['tags'].items()) or
                (element['timestamp'] is not None and not isinstance(element['timestamp'], str))):
            raise ValueError('Invalid normalized way tags or timestamp')
    nodes, ways, _ = roads.collect_elements([document])
    if len(elements) != len(nodes) + len(ways):
        raise ValueError('Unexpected or duplicate normalized source elements')
    needed = set()
    for way in ways.values():
        if not isinstance(way['tags'], dict) or way['tags'].get('highway') not in roads.KINDS or way['tags'].get('area') == 'yes' or not isinstance(way['nodes'], list) or len(way['nodes']) < 2:
            raise ValueError('Invalid normalized highway way')
        if any(type(index) is not int or index not in nodes for index in way['nodes']):
            raise ValueError('Snapshot way references a missing node')
        needed.update(way['nodes'])
    if not ways or set(nodes) != needed or manifest['normalization']['node_count'] != len(nodes) or manifest['normalization']['way_count'] != len(ways):
        raise ValueError('Snapshot node/way counts or references differ from manifest')
    return manifest, nodes, ways


def main():
    import pyproj
    bounds = outward_bounds()
    print('covering WGS84 bounds:', bounds, file=sys.stderr)
    documents, records, acquisition = acquire(bounds)
    compressed, manifest = prepare(documents, records, acquisition, bounds)
    manifest['normalization']['pyproj_version'] = pyproj.__version__
    manifest['normalization']['proj_version'] = pyproj.proj_version_str
    encoded = json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
    if len(encoded) > MAX_MANIFEST:
        raise ValueError('Manifest exceeds cap')

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bundle_path = OUTPUT_DIR / manifest['bundle']['path']
    manifest_path = OUTPUT_DIR / 'display-roads-manifest.json'
    if bundle_path.exists() or manifest_path.exists():
        raise FileExistsError('Snapshot revision already exists')

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        (temp / bundle_path.name).write_bytes(compressed)
        (temp / manifest_path.name).write_bytes(encoded)
        _, nodes, ways = load_snapshot(temp / manifest_path.name, roads.digest(encoded))
        actual, _ = roads.build_asset(nodes, ways)
        all_nodes, all_ways, _ = roads.collect_elements(documents)
        expected, _ = roads.build_asset(all_nodes, all_ways)
        if actual != expected:
            raise ValueError('Normalized snapshot does not reproduce full-source display geometry')
    bundle_path.write_bytes(compressed)
    manifest_path.write_bytes(encoded)
    print(json.dumps(dict(manifest=str(manifest_path), manifest_sha256=roads.digest(encoded),
                          bundle=manifest['bundle'], acquisition=acquisition), indent=2))


if __name__ == '__main__':
    main()
