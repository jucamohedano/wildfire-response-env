"""Compile display-only roads from a portable pinned source bundle, offline."""

from collections import Counter
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ACQUISITION = 'data/coastal_mobility_v1/acquisition.json'
ACQUISITION_SHA256 = '43ddc5a7aeac534a23ee1c013a93cd623eaff3836f4738141f125e5d92926aa8'
ASSET = 'ui/public/assets/map-roads-v2.json'
PROVENANCE = 'data/ui_v2/display_roads_provenance.json'
SNAPSHOT_MANIFEST = 'data/ui_v2/snapshots/display-roads-manifest.json'
SNAPSHOT_MANIFEST_SHA256 = 'b21852b130cf02edebb15eab03c40d4b3ae4d8c805d11ee317233ccbbe9107d5'
EXTENT = (297000, 4036500, 315500, 4054500)
VERSION = 'wildfire-display-roads-v2'
MAX_TILE_BYTES = 20_000_000
MAX_GZIP_BYTES = 4_000_000
MAX_ASSET_BYTES = 1_500_000
MAX_VERTICES = 100_000
SIMPLIFY_METRES = 3.0
SOURCE = dict(name='OpenStreetMap cached coastal Málaga map extract',
              url='https://www.openstreetmap.org/copyright',
              attribution='© OpenStreetMap contributors', license='ODbL-1.0',
              acquisition_date='2026-10-27')
KINDS = {
    **dict.fromkeys(('motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary',
                    'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link'), 'major'),
    **dict.fromkeys(('unclassified', 'residential', 'living_street', 'service', 'road', 'busway'), 'local'),
    'track': 'track',
    **dict.fromkeys(('path', 'footway', 'cycleway', 'bridleway', 'steps', 'pedestrian'), 'path'),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def asset_digest(asset):
    return digest(canonical({key: value for key, value in asset.items() if key != 'sha256'}))


def verified_tile(compressed, record):
    if type(compressed) is not bytes or len(compressed) > MAX_GZIP_BYTES:
        raise ValueError('Cached tile exceeds compressed byte cap')
    if type(record['bytes']) is not int or not 0 < record['bytes'] <= MAX_TILE_BYTES:
        raise ValueError('Invalid pinned tile size')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_TILE_BYTES + 1)
    if len(raw) != record['bytes'] or digest(raw) != record['sha256']:
        raise ValueError('Cached OSM decompressed size or SHA256 differs from acquisition pin')
    document = json.loads(raw)
    if not isinstance(document, dict) or not isinstance(document.get('elements'), list) or 'remark' in document:
        raise ValueError('Invalid or incomplete OSM tile')
    return document


def collect_elements(documents):
    nodes, ways = {}, {}
    duplicates = Counter()
    for document in documents:
        for element in document['elements']:
            kind = element.get('type')
            if kind not in ('node', 'way'):
                continue
            identifier, version = element['id'], element['version']
            if type(identifier) is not int or identifier <= 0 or type(version) is not int or version < 1:
                raise ValueError('Invalid OSM identity/version')
            if kind == 'node':
                longitude, latitude = element['lon'], element['lat']
                if any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (longitude, latitude)) or not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError('Invalid source node coordinates')
                record = dict(id=identifier, version=version, lon=longitude, lat=latitude)
                table = nodes
            else:
                record = dict(id=identifier, version=version, nodes=element['nodes'],
                              tags=element.get('tags', {}), timestamp=element.get('timestamp'))
                table = ways
            previous = table.get(identifier)
            if previous:
                duplicates[kind] += 1
                if version == previous['version'] and record != previous:
                    raise ValueError('Conflicting geometry/tags for an identical OSM element version')
            if previous is None or version > previous['version']:
                table[identifier] = record
    return nodes, ways, dict(sorted(duplicates.items()))


def read_cached_sources(root):
    root = Path(root)
    acquisition_bytes = (root / ACQUISITION).read_bytes()
    if digest(acquisition_bytes) != ACQUISITION_SHA256:
        raise ValueError('Acquisition manifest differs from its pinned SHA256')
    acquisition = json.loads(acquisition_bytes)
    records = acquisition['requests']
    if len(records) != 21 or len({record['raw_cache'] for record in records}) != 21:
        raise ValueError('Expected the complete 21-tile acquisition')
    source_records = []

    def documents():
        for record in sorted(records, key=lambda item: item['raw_cache']):
            relative = Path(record['raw_cache'])
            if relative.is_absolute() or relative.parts[:3] != ('data', 'coastal_mobility_v1', '.runtime') or '..' in relative.parts:
                raise ValueError('Expected a relative coastal OSM raw-cache path')
            path = root / relative
            if path.is_symlink() or not path.resolve().is_relative_to((root / 'data/coastal_mobility_v1/.runtime').resolve()):
                raise ValueError('Raw cache must be a regular in-cache file')
            compressed = path.read_bytes()
            document = verified_tile(compressed, record)
            source_records.append(dict(record, gzip_bytes=len(compressed), gzip_sha256=digest(compressed)))
            yield document

    nodes, ways, duplicates = collect_elements(documents())
    return acquisition, nodes, ways, source_records, duplicates


def clip_segment(start, end, extent=EXTENT):
    west, south, east, north = extent
    delta_east, delta_north = end[0] - start[0], end[1] - start[1]
    lower, upper = 0.0, 1.0
    for denominator, numerator in ((-delta_east, start[0] - west),
                                   (delta_east, east - start[0]),
                                   (-delta_north, start[1] - south),
                                   (delta_north, north - start[1])):
        if denominator == 0:
            if numerator < 0:
                return None
            continue
        ratio = numerator / denominator
        if denominator < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower >= upper:
            return None
    return lower, upper


def clip_line(coordinates, extent=EXTENT):
    runs, run = [], []

    def finish():
        if len(run) > 1:
            runs.append(run[:])
        run.clear()

    for start, end in zip(coordinates, coordinates[1:]):
        clipped = clip_segment(start, end, extent)
        if clipped is None:
            finish()
            continue
        lower, upper = clipped
        first = [round(start[axis] + lower * (end[axis] - start[axis]), 1) for axis in (0, 1)]
        last = [round(start[axis] + upper * (end[axis] - start[axis]), 1) for axis in (0, 1)]
        if first == last:
            continue
        if run and run[-1] != first:
            finish()
        if not run:
            run.append(first)
        if run[-1] != last:
            run.append(last)
        if upper < 1:
            finish()
    finish()
    return runs


def point_segment_distance(point, start, end):
    delta = [end[axis] - start[axis] for axis in (0, 1)]
    squared = sum(value * value for value in delta)
    fraction = max(0.0, min(1.0, sum((point[axis] - start[axis]) * delta[axis]
                   for axis in (0, 1)) / squared)) if squared else 0.0
    return math.hypot(*(point[axis] - start[axis] - fraction * delta[axis] for axis in (0, 1)))


def simplify_line(coordinates, tolerance=SIMPLIFY_METRES):
    if len(coordinates) > 3 and coordinates[0] == coordinates[-1]:
        split = max(range(1, len(coordinates) - 1), key=lambda index: math.dist(coordinates[0], coordinates[index]))
        simplified = simplify_line(coordinates[:split + 1], tolerance)[:-1] + simplify_line(coordinates[split:], tolerance)
        return simplified if len({tuple(point) for point in simplified}) >= 3 else [point[:] for point in coordinates]
    keep, pending = {0, len(coordinates) - 1}, [(0, len(coordinates) - 1)]
    while pending:
        begin, finish = pending.pop()
        if finish - begin < 2:
            continue
        index = max(range(begin + 1, finish), key=lambda candidate:
                    point_segment_distance(coordinates[candidate], coordinates[begin], coordinates[finish]))
        if point_segment_distance(coordinates[index], coordinates[begin], coordinates[finish]) > tolerance:
            keep.add(index)
            pending.extend(((begin, index), (index, finish)))
    return [coordinates[index][:] for index in sorted(keep)]


def access_category(tags):
    value = next((tags[key] for key in ('motorcar', 'motor_vehicle', 'vehicle', 'access') if key in tags), None)
    if value not in {None, 'yes', 'permissive', 'designated', 'official'}:
        return 'restricted'
    if tags.get('hgv') not in {None, 'yes', 'permissive', 'designated', 'official'}:
        return 'restricted'
    if any(key.startswith(('access:', 'vehicle:', 'motor_vehicle:', 'motorcar:', 'hgv:',
                           'maxweight', 'maxwidth', 'maxheight')) for key in tags):
        return 'restricted'
    return 'unknown'


def build_asset(nodes, ways, *, extent=EXTENT, max_vertices=MAX_VERTICES, max_asset_bytes=MAX_ASSET_BYTES,
                source=None):
    from pyproj import Transformer, network

    network.set_network_enabled(False)
    projector = Transformer.from_crs('EPSG:4326', 'EPSG:25830', always_xy=True, allow_ballpark=False)
    projected, lines, timestamps = {}, [], []
    statistics = Counter()
    for way_id, way in sorted(ways.items()):
        tags = way['tags']
        highway = tags.get('highway')
        if highway not in KINDS or tags.get('area') == 'yes':
            continue
        statistics['source_highway_ways'] += 1
        coordinates = []
        for node_id in way['nodes']:
            if node_id not in nodes:
                raise ValueError('Missing source way node; no geometry may be invented')
            if node_id not in projected:
                node = nodes[node_id]
                point = projector.transform(node['lon'], node['lat'], errcheck=True)
                if not all(math.isfinite(value) for value in point):
                    raise ValueError('Nonfinite projected source geometry')
                projected[node_id] = point
            coordinates.append(projected[node_id])
        parts = clip_line(coordinates, extent)
        if parts:
            statistics['intersecting_source_ways'] += 1
            if way.get('timestamp'):
                timestamps.append(way['timestamp'])
        for index, part in enumerate(parts):
            simplified = simplify_line(part)
            if not any(first != second for first, second in zip(simplified, simplified[1:])):
                continue
            lines.append(dict(id=f'osm:w{way_id}:{index:04d}', kind=KINDS[highway],
                              highway=highway, access=access_category(tags), coordinates=simplified))
            statistics['vertices_before_simplification'] += len(part)
    lines.sort(key=lambda line: line['id'])
    asset = dict(version=VERSION, crs='EPSG:25830', extent=list(extent),
                 role='display_only_not_routing', source=dict(SOURCE if source is None else source), lines=lines)
    asset['sha256'] = asset_digest(asset)
    statistics.update(lines=len(lines), vertices=sum(len(line['coordinates']) for line in lines))
    if not lines or statistics['vertices'] > max_vertices or len(canonical(asset)) + 1 >= max_asset_bytes:
        raise ValueError('Display geometry is empty or exceeds its vertex/byte cap; do not sample roads arbitrarily')
    report = dict(statistics, kind_counts=dict(sorted(Counter(line['kind'] for line in lines).items())),
                  access_counts=dict(sorted(Counter(line['access'] for line in lines).items())),
                  intersecting_way_timestamp_min=min(timestamps) if timestamps else None,
                  intersecting_way_timestamp_max=max(timestamps) if timestamps else None)
    return asset, report


def prepare(root=ROOT, *, legacy_raw_cache=False):
    import pyproj

    root = Path(root)
    if legacy_raw_cache:
        acquisition, nodes, ways, records, duplicates = read_cached_sources(root)
        source = dict(SOURCE)
        source_record = dict(path=ACQUISITION, sha256=ACQUISITION_SHA256,
            started_at=acquisition['acquisition_started_at'], completed_at=acquisition['acquisition_completed_at'],
            files=records, snapshot='Original pinned API cache; fails closed when any raw tile is missing.')
        loader_record = None
    else:
        from eval import display_road_snapshot
        manifest, nodes, ways = display_road_snapshot.load_snapshot(root / SNAPSHOT_MANIFEST,
                                                                 SNAPSHOT_MANIFEST_SHA256)
        source = dict(manifest['source'], acquisition_date=manifest['acquisition']['completed_at'][:10],
                      revision='display-road-source-20261028')
        source_record = dict(path=SNAPSHOT_MANIFEST, sha256=SNAPSHOT_MANIFEST_SHA256,
            started_at=manifest['acquisition']['started_at'], completed_at=manifest['acquisition']['completed_at'],
            files=manifest['requests'], bundle=manifest['bundle'],
            snapshot='Portable normalized current-map derivative; not restoration of the lost original API cache.',
            prior_revision='data/ui_v2/archive/20261027/manifest.json')
        duplicates = manifest['normalization']['duplicate_source_records']
        loader_record = dict(script='eval/display_road_snapshot.py',
                             sha256=digest(Path(display_road_snapshot.__file__).read_bytes()))
    asset, statistics = build_asset(nodes, ways, source=source)
    encoded = canonical(asset) + b'\n'
    provenance = dict(version='wildfire-display-roads-provenance-v2', role=asset['role'], source=source,
        acquisition=source_record,
        preparation=dict(script='eval/prepare_display_roads_v2.py', script_sha256=digest(Path(__file__).read_bytes()),
                         command='PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/prepare_display_roads_v2.py',
                         execution='Offline CPU only; verifies the pinned source and writes only the v2 display asset/provenance.',
                         source_loader=loader_record,
                         pyproj_version=pyproj.__version__, proj_version=pyproj.proj_version_str,
                         source_crs='EPSG:4326', crs=asset['crs'], always_xy=True, proj_network_enabled=False,
                         clip_extent=list(EXTENT), coordinate_rounding_metres=0.1,
                         simplification_metres=SIMPLIFY_METRES,
                         simplification='Projected Douglas-Peucker, unchanged clipped endpoints; closed-loop split retains a real opposite source vertex. No sampling or invented connectors.',
                         deduplication='Highest OSM element version wins by type/id; conflicting equal-version records fail closed.',
                         duplicate_source_records=duplicates),
        contract=dict(version=VERSION, asset_url='/assets/map-roads-v2.json',
            line_fields=dict(id='OSM way ID plus deterministic clipped part index; never a routing arc',
                             kind='major | local | track | path', highway='Original OSM highway class',
                             access='unknown | restricted; no positive availability state',
                             coordinates='EPSG:25830 [easting,northing] projected metres'),
            class_mapping=KINDS,
            access='Most-specific motorcar > motor_vehicle > vehicle > access determines tagged restrictions; specific permission can override a broader restriction. Limited, conditional, directional, dimension or HGV restrictions mark restricted context. Even explicit positive OSM access tags remain unknown: no access, vehicle suitability or live availability is verified.',
            geometry='Area=yes and unsupported highway classes omitted; all supported intersecting line parts retained, including disconnected, restricted, track and non-motorized path context. No crossing noding, snapped junctions or network construction.',
            suggested_palette='Major roads cream; local roads subdued; tracks faint/dashed; paths thinner/dashed. Restricted and unknown access never indicate a drivable itinerary.',
            canonical_sha256='json.dumps(payload_without_sha256, sort_keys=True, separators=(comma,colon), allow_nan=False), default ensure_ascii=True, UTF-8; file has trailing LF.'),
        statistics=statistics,
        output=dict(path=ASSET, bytes=len(encoded), sha256=asset['sha256'], file_sha256=digest(encoded)),
        rights=dict(attribution='Display © OpenStreetMap contributors and link https://www.openstreetmap.org/copyright.',
                    database_license='ODbL-1.0', license_url='https://opendatacommons.org/licenses/odbl/1-0/',
                    publication='Local derivative database, not published by this preparation. Any public use/redistribution must retain attribution and licence notices and satisfy applicable ODbL derivative-database share-alike and database-availability obligations. This is not a blanket licence for unrelated imagery.'),
        limitations=['Cartographic context only; not routing, physical road condition, fire-truck access, staffing or an availability claim.',
                     'Community source may be incomplete or inaccurate; the asset does not add missing roads or infer access across gates, fords or private land.',
                     'Road geometry date differs from the September 2021 exercise context; it does not reconstruct incident-time roads.',
                     'One-way rules, turn restrictions, gates, bridge/tunnel height and live closures are not modeled; geometric crossings are not connections.',
                     'No elevation, road width or new terrain is inferred. The frontend must clip/drape only against its independently validated terrain.',
                     'V1 road/cache/physics datasets are read-only; this file cannot substitute for the pinned v1 routing graph.'])
    for relative, payload in ((ASSET, encoded), (PROVENANCE, (json.dumps(provenance, sort_keys=True, indent=2, allow_nan=False) + '\n').encode())):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return asset, provenance


if __name__ == '__main__':
    result, provenance = prepare()
    print(json.dumps(dict(sha256=result['sha256'], bytes=provenance['output']['bytes'],
                          extent=result['extent'], statistics=provenance['statistics']), indent=2))
