#!/usr/bin/env python3
"""Compile and independently audit source-backed road observations, not routes."""
import argparse
import gzip
import hashlib
import json
import math
import os
import secrets
import stat
import datetime
import importlib.util
import subprocess
import time
import urllib.parse
from collections import Counter
from pathlib import Path

from pyproj import Transformer, network

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/coastal_mobility_v1/source_snapshot.json.gz'
SOURCE_SHA = 'fd276b8a4a799cffedd268f485a97aaff5ff88d83d11726be0e23894d282f323'
BOUNDS = (290000, 4035000, 332000, 4064500)
BBOX = (-5.345, 36.36, -5.13, 36.625)
ACQUISITION_BBOX = (-5.42, 36.36, -4.96, 36.65)
WIDTH, HEIGHT, CELL = 84, 59, 500


def make_private_directories(path):
    # Create each missing ancestor privately; never chmod an existing parent.
    pending = []
    current = Path(path)
    while not current.exists():
        pending.append(current)
        current = current.parent
    for directory in reversed(pending):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass


def write_private_bytes(path, data):
    """Atomically replace a regular file without ever following a target symlink."""
    path = Path(path)
    make_private_directories(path.parent)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp_name = None
    temp_created = False
    try:
        def check_target():
            try:
                target = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(target.st_mode):
                raise ValueError('private output target must be a regular file, not a symlink')
        check_target()
        temp_name = f'.{path.name}.{secrets.token_hex(16)}.tmp'
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dir_fd)
        temp_created = True
        with os.fdopen(fd, 'wb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError('private output temporary file is not regular')
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
        check_target()
        # Even if the target is replaced after the check, replace never follows it.
        os.replace(temp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        temp_created = False
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        os.close(dir_fd)


def inside_grid(point):
    return BOUNDS[0] <= point[0] <= BOUNDS[2] and BOUNDS[1] <= point[1] <= BOUNDS[3]


def strict_json(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate JSON key: {key}')
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError(f'invalid JSON number: {value}')
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_source(path=SOURCE):
    raw = Path(path).read_bytes()
    if hash_bytes(raw) != SOURCE_SHA:
        raise ValueError('source snapshot hash mismatch')
    obj = json.loads(gzip.decompress(raw))
    if obj.get('version') != 'coastal-osm-source-v1' or tuple(obj.get('bbox_wgs84', ())) != BBOX:
        raise ValueError('source identity mismatch')
    return obj


def index_source(snapshot):
    nodes, ways = {}, []
    seen = set()
    for e in snapshot['elements']:
        key = (e['type'], e['id'])
        if key in seen:
            raise ValueError('duplicate source identity')
        seen.add(key)
        if e['type'] == 'node':
            if not (math.isfinite(e['lon']) and math.isfinite(e['lat'])):
                raise ValueError('invalid source coordinate')
            nodes[e['id']] = e
        elif e['type'] == 'way':
            ways.append(e)
    for way in ways:
        if len(way['nodes']) < 2 or any(n not in nodes for n in way['nodes']):
            raise ValueError('incomplete source way')
    return nodes, ways


def inside_bbox(n):
    return BBOX[0] <= n['lon'] <= BBOX[2] and BBOX[1] <= n['lat'] <= BBOX[3]


def clip(a, b, bounds):
    # Liang-Barsky; equal boundary contact is retained.
    dx, dy = b[0]-a[0], b[1]-a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0]-bounds[0]), (dx, bounds[2]-a[0]),
                 (-dy, a[1]-bounds[1]), (dy, bounds[3]-a[1])):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
            if lo > hi:
                return None
    return [[a[0]+lo*dx, a[1]+lo*dy], [a[0]+hi*dx, a[1]+hi*dy]]


def transform_nodes(nodes):
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326, 25830, always_xy=True)
    return {i: transformer.transform(n['lon'], n['lat']) for i, n in nodes.items() if inside_bbox(n)}


def source_segments(snapshot):
    nodes, ways = index_source(snapshot)
    positions = transform_nodes(nodes)
    for way in sorted(ways, key=lambda w: w['id']):
        for i, (a, b) in enumerate(zip(way['nodes'], way['nodes'][1:])):
            if a in positions and b in positions:
                yield way, i, a, b, positions[a], positions[b]


def cell_range(a, b):
    x0, y0, x1, y1 = BOUNDS
    # Include both neighbours when a segment lies on a grid boundary.
    c0 = max(0, math.ceil((min(a[0], b[0])-x0)/CELL)-1)
    c1 = min(WIDTH-1, math.floor((max(a[0], b[0])-x0)/CELL))
    r0 = max(0, math.ceil((y1-max(a[1], b[1]))/CELL)-1)
    r1 = min(HEIGHT-1, math.floor((y1-min(a[1], b[1]))/CELL))
    return ((r, c) for r in range(r0, r1+1) for c in range(c0, c1+1))


def cell_bounds(r, c):
    x = BOUNDS[0] + c*CELL
    y = BOUNDS[3] - r*CELL
    return (x, y-CELL, x+CELL, y)


def compile_observations(snapshot):
    records, cells = [], set()
    for way, i, a, b, p, q in source_segments(snapshot):
        points = clip(p, q, BOUNDS)
        if points is None:
            continue
        # No endpoint identifiers are invented at clipping boundaries.
        item = {'way': way['id'], 'segment': i, 'nodes': [a, b],
                'highway': way['tags'].get('highway'), 'coordinates': points}
        records.append(item)
        for r, c in cell_range(*points):
            if clip(points[0], points[1], cell_bounds(r, c)) is not None:
                cells.add((r, c))
    return {'version': 'extended-access-observations-v1',
            'status': 'incomplete_observations_not_routing',
            'source_sha256': SOURCE_SHA, 'crs': 'EPSG:25830', 'bounds': list(BOUNDS),
            'source_bbox_wgs84': list(BBOX), 'grid': [HEIGHT, WIDTH, CELL],
            'segments': records, 'observed_cells': [list(c) for c in sorted(cells)],
            'surface': 'unknown_for_every_cell'}


def approximate_footprint(bbox=BBOX):
    """Estimate bbox overlap; this is not a completeness or access guarantee."""
    from shapely.geometry import Polygon, box
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326, 25830, always_xy=True)
    west, south, east, north = bbox
    corners = [(west, south), (east, south), (east, north), (west, north)]
    ring = []
    for a, b in zip(corners, corners[1:] + corners[:1]):
        for i in range(512):
            t = i / 512
            ring.append(transformer.transform(a[0] + (b[0]-a[0])*t,
                                              a[1] + (b[1]-a[1])*t))
    footprint = Polygon(ring)
    whole, intersects = 0, 0
    for r in range(HEIGHT):
        for c in range(WIDTH):
            cell = box(*cell_bounds(r, c))
            whole += footprint.contains(cell)
            intersects += footprint.intersects(cell)
    fraction = footprint.intersection(box(*BOUNDS)).area / box(*BOUNDS).area
    return {'approx_source_bbox_area_fraction': round(fraction, 6),
            'approx_cells_inside_source_bbox': whole,
            'approx_cells_intersecting_source_bbox': intersects,
            'approx_cells_outside_source_bbox': WIDTH*HEIGHT-intersects}


def validate(result, snapshot):
    """Audit from source using GEOS intersection, without calling the compiler or clip."""
    from shapely.geometry import LineString, Point, box
    expected_keys = {'version', 'status', 'source_sha256', 'crs', 'bounds', 'source_bbox_wgs84', 'grid', 'segments', 'observed_cells', 'surface'}
    if set(result) != expected_keys or (result['version'], result['status'], result['source_sha256'], result['crs'], result['bounds'], result['source_bbox_wgs84'], result['grid'], result['surface']) != (
        'extended-access-observations-v1', 'incomplete_observations_not_routing', SOURCE_SHA, 'EPSG:25830', list(BOUNDS), list(BBOX), [HEIGHT, WIDTH, CELL], 'unknown_for_every_cell'):
        raise ValueError('invalid metadata or surface claim')
    seen, expected_cells = set(), set()
    records = {(e['way'], e['segment']): e for e in result['segments']}
    if len(records) != len(result['segments']):
        raise ValueError('duplicate segment')
    area = box(*BOUNDS)
    for way, i, a, b, p, q in source_segments(snapshot):
        geom = LineString([p, q]) if p != q else Point(p)
        part = geom.intersection(area)
        key = (way['id'], i)
        if part.is_empty:
            continue
        item = records.get(key)
        if item is None:
            raise ValueError('missing source segment')
        if set(item) != {'way', 'segment', 'nodes', 'highway', 'coordinates'} or item['nodes'] != [a, b] or item['highway'] != way['tags'].get('highway'):
            raise ValueError('source topology or tags mismatch')
        pts = item['coordinates']
        if len(pts) != 2 or any(len(pt) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pt) for pt in pts):
            raise ValueError('invalid coordinates')
        observed = LineString(pts) if pts[0] != pts[1] else Point(pts[0])
        if observed.hausdorff_distance(part) > 1e-6:
            raise ValueError('segment geometry mismatch')
        # Preserve original segment order, essential for source topology.
        if math.dist(pts[0], p) > math.dist(pts[1], p) + 1e-6:
            raise ValueError('segment direction mismatch')
        seen.add(key)
        # Compute candidate cells independently from the compiler's cell_range.
        minx, miny, maxx, maxy = part.bounds
        first_col = max(0, int(math.floor((minx - BOUNDS[0]) / CELL)) - 1)
        last_col = min(WIDTH - 1, int(math.floor((maxx - BOUNDS[0]) / CELL)) + 1)
        first_row = max(0, int(math.floor((BOUNDS[3] - maxy) / CELL)) - 1)
        last_row = min(HEIGHT - 1, int(math.floor((BOUNDS[3] - miny) / CELL)) + 1)
        for r in range(first_row, last_row + 1):
            for c in range(first_col, last_col + 1):
                if part.intersects(box(*cell_bounds(r, c))):
                    expected_cells.add((r, c))
    if seen != set(records):
        raise ValueError('unbacked source segment')
    actual_cells = result['observed_cells']
    if actual_cells != [list(c) for c in sorted(expected_cells)]:
        raise ValueError('cell coverage mismatch')
    acquisition = approximate_footprint(ACQUISITION_BBOX)
    return {'segments': len(seen), 'observed_cells': len(expected_cells),
            'unknown_surface_cells': WIDTH*HEIGHT, **approximate_footprint(),
            'acquisition_bbox_estimate': {
                'approx_area_fraction': acquisition['approx_source_bbox_area_fraction'],
                'approx_cells_inside': acquisition['approx_cells_inside_source_bbox'],
                'approx_cells_intersecting': acquisition['approx_cells_intersecting_source_bbox'],
                'approx_cells_outside': acquisition['approx_cells_outside_source_bbox']}}


def audit_tiles(manifest):
    """Check separately pinned raw tiles; do not merge non-atomic OSM versions."""
    from shapely.geometry import LineString, box
    if manifest.get('version') != 'extended-access-osm-samples-v1' or manifest.get('status') != 'partial_samples_not_complete_coverage':
        raise ValueError('unexpected tile manifest')
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326, 25830, always_xy=True)
    area = box(*BOUNDS)
    reports = []
    names = set()
    for tile in manifest['tiles']:
        if tile['name'] in names:
            raise ValueError('duplicate tile')
        names.add(tile['name'])
        raw = Path(tile['path']).read_bytes()
        if len(raw) != tile['bytes'] or hash_bytes(raw) != tile['sha256']:
            raise ValueError('tile hash or size mismatch')
        payload = json.loads(raw)
        nodes, ways = index_source(payload)
        relevant = [w for w in ways if 'highway' in w.get('tags', {}) or w.get('tags', {}).get('natural') == 'coastline']
        road_count = coast_count = map_roads = map_coasts = 0
        coast_degree = Counter()
        for way in relevant:
            tags = way['tags']
            highway = 'highway' in tags
            coast = tags.get('natural') == 'coastline'
            road_count += highway
            coast_count += coast
            if coast:
                coast_degree[way['nodes'][0]] += 1
                coast_degree[way['nodes'][-1]] += 1
            points = [transformer.transform(nodes[n]['lon'], nodes[n]['lat']) for n in way['nodes']]
            if LineString(points).intersects(area):
                map_roads += highway
                map_coasts += coast
        reports.append({'name': tile['name'], 'road_ways': road_count,
                        'coastline_ways': coast_count, 'road_ways_intersecting_map': map_roads,
                        'coastline_ways_intersecting_map': map_coasts,
                        'coastline_endpoint_degree_one': sum(n == 1 for n in coast_degree.values()),
                        'coastline_endpoint_degree_other_than_two': sum(n != 2 for n in coast_degree.values())})
    return {'status': 'partial_samples_not_complete_coverage', 'tiles': reports}


# Fixed padded envelope of the corrected projected grid. Bounds are in
# microdegrees to make the lattice and split boundaries exactly reproducible.
LATTICE = (-5352000, 36436000, -4873000, 36713000)
STEP = 30000
MAX_BYTES = 100_000_000
PROTOCOL_RESERVE = 1_000_000
MAX_SECONDS = 300


def lattice_roots():
    west, south, east, north = LATTICE
    # North-to-south, east-to-west prioritizes the previous acquisition gaps.
    xs = list(range(west, east, STEP))
    ys = list(range(south, north, STEP))
    return [{'id': f'r{r:02d}c{c:02d}',
             'bounds_microdegrees': [x, y, min(x + STEP, east), min(y + STEP, north)],
             'status': 'pending', 'attempts': []}
            for r, y in reversed(list(enumerate(ys)))
            for c, x in reversed(list(enumerate(xs)))]


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def bbox_url(bounds):
    return 'https://api.openstreetmap.org/api/0.6/map.json?bbox=' + ','.join(f'{v / 1_000_000:.6f}' for v in bounds)


def save_manifest(path, manifest):
    tmp = path.with_suffix('.tmp')
    write_private_bytes(tmp, (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
    tmp.replace(path)


def check_cache_name(name):
    if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\x00' in name:
        raise ValueError('cache entry must be a single filename')


class PrivateDownloadTemp:
    """An exclusively-created download file held open until safe publication."""
    def __init__(self, directory, label):
        check_cache_name(label)
        self.dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.name = f'.{label}.{secrets.token_hex(16)}.tmp'
        self.fd = None
        try:
            self.fd = os.open(self.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=self.dir_fd)
            os.fchmod(self.fd, 0o600)
            self.verify()
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
            os.close(self.dir_fd)
            raise

    def verify(self):
        opened = os.fstat(self.fd)
        try:
            named = os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError('download temporary file was removed') from error
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode) or
            opened.st_nlink != 1 or named.st_nlink != 1 or
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ValueError('download temporary file identity or link count changed')
        return opened

    def read(self):
        self.verify()
        os.lseek(self.fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(self.fd), 'rb') as handle:
            return handle.read()

    def publish(self, destination):
        check_cache_name(destination)
        self.verify()
        # link() creates the destination exclusively: it cannot replace a
        # symlink, hardlink, or any other existing cache entry.
        os.link(self.name, destination, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd,
                follow_symlinks=False)
        linked = os.stat(destination, dir_fd=self.dir_fd, follow_symlinks=False)
        opened = os.fstat(self.fd)
        if (not stat.S_ISREG(linked.st_mode) or
            (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino) or
            opened.st_nlink != 2):
            os.unlink(destination, dir_fd=self.dir_fd)
            raise ValueError('download temporary file changed during publication')
        os.unlink(self.name, dir_fd=self.dir_fd)
        self.name = None

    def close(self):
        if self.name is not None:
            try:
                named = os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False)
                opened = os.fstat(self.fd)
                if (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino):
                    os.unlink(self.name, dir_fd=self.dir_fd)
            except FileNotFoundError:
                pass
        os.close(self.fd)
        os.close(self.dir_fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def reject_existing_cache_entry(directory, name):
    check_cache_name(name)
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise FileExistsError(f'cache entry already exists: {name}')
    finally:
        os.close(dir_fd)


def fetch_lattice_tile(directory, tile, url, remaining, seconds, attempt_id=None, record_receipt=None):
    """Run curl against inherited descriptors, not replaceable filesystem paths."""
    attempt_id = attempt_id or secrets.token_hex(16)
    check_cache_name(attempt_id)
    body_name = f"{tile['id']}.{attempt_id}.json"
    header_name = f"{tile['id']}.{attempt_id}.headers"
    reject_existing_cache_entry(directory, body_name)
    reject_existing_cache_entry(directory, header_name)
    with PrivateDownloadTemp(directory, tile['id']+'.body') as body, \
         PrivateDownloadTemp(directory, tile['id']+'.header') as header:
        command = ['curl', '-sS', '--connect-timeout', '8', '--max-time', str(min(25, max(1, int(seconds)-1))),
                   '--max-filesize', str(remaining), '--header', 'Accept: application/json',
                   '--header', 'Accept-Encoding: identity', '--user-agent', 'wildfire-source-coverage-audit/1.0',
                   '--output', f'/proc/self/fd/{body.fd}', '--dump-header', f'/proc/self/fd/{header.fd}',
                   '--write-out', '%{http_code}', url]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                pass_fds=(body.fd, header.fd), preexec_fn=lambda: os.umask(0o077))
        raw = body.read()
        header_data = header.read()
        # A result is published only if both descriptor-backed temporary files
        # still have their original inode and link count.
        header_info = header.verify()
        body_info = body.verify()
        valid = False
        if result.returncode == 0 and result.stdout[-3:] == '200':
            try:
                obj = json.loads(raw)
                valid = isinstance(obj.get('elements'), list)
            except (ValueError, TypeError, AttributeError):
                valid = False
        receipt = {'attempt_id':attempt_id, 'http_status':result.stdout[-3:], 'curl_exit':result.returncode,
                   'bytes':len(raw), 'header_bytes':len(header_data), 'sha256':hash_bytes(raw),
                   'header_sha256':hash_bytes(header_data), 'valid':valid,
                   'too_many_nodes':result.stdout[-3:] == '400' and b'too many nodes' in raw.lower(),
                   'completed_at':utc_now(),
                   'header_temp':{'name':header.name,'device':header_info.st_dev,'inode':header_info.st_ino},
                   'body_temp':{'name':body.name,'device':body_info.st_dev,'inode':body_info.st_ino}}
        if record_receipt is not None:
            record_receipt(receipt)
        header.publish(header_name)
        if valid:
            body.publish(body_name)
        return result, raw, len(header_data), valid, str(Path(directory)/body_name), str(Path(directory)/header_name)


def read_recoverable_published(path, temp_info, label, tile_id, expected_size, expected_hash):
    """Reconcile exactly the two journaled links left by interrupted publication."""
    path=Path(path)
    dir_fd=os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd=None
    try:
        fd=os.open(path.name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=dir_fd)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink not in (1,2):
            raise ValueError('published cache entry has unexpected file type or link count')
        if temp_info is not None:
            if (info.st_dev,info.st_ino)!=(temp_info['device'],temp_info['inode']):
                raise ValueError('published cache entry differs from journaled inode')
        with os.fdopen(os.dup(fd),'rb') as handle:
            raw=handle.read()
        if len(raw)!=expected_size or hash_bytes(raw)!=expected_hash:
            raise ValueError('published cache content mismatch after interruption')
        if info.st_nlink == 2:
            prefix=f'.{tile_id}.{label}.'
            if temp_info is not None:
                name=temp_info['name']; check_cache_name(name)
                if not name.startswith(prefix) or not name.endswith('.tmp'):
                    raise ValueError('invalid journaled temporary filename')
            else:
                # Older receipts have the content hash but no inode/name. A
                # unique private temporary name matching the published inode,
                # plus exactly two links and the journaled hash, is sufficient
                # to identify the interrupted link operation without guessing.
                matches=[]
                for candidate in os.listdir(dir_fd):
                    if not candidate.startswith(prefix) or not candidate.endswith('.tmp'):
                        continue
                    try:
                        candidate_info=os.stat(candidate,dir_fd=dir_fd,follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if (stat.S_ISREG(candidate_info.st_mode) and candidate_info.st_nlink==2 and
                        (candidate_info.st_dev,candidate_info.st_ino)==(info.st_dev,info.st_ino)):
                        matches.append(candidate)
                if len(matches)!=1:
                    raise ValueError('cannot uniquely reconcile unjournaled temporary link')
                name=matches[0]
            temporary=os.stat(name,dir_fd=dir_fd,follow_symlinks=False)
            if (not stat.S_ISREG(temporary.st_mode) or temporary.st_nlink != 2 or
                (temporary.st_dev,temporary.st_ino)!=(info.st_dev,info.st_ino)):
                raise ValueError('journaled temporary link does not match published inode')
            # Recheck both names immediately before removing the temporary link.
            destination=os.stat(path.name,dir_fd=dir_fd,follow_symlinks=False)
            temporary=os.stat(name,dir_fd=dir_fd,follow_symlinks=False)
            if ((destination.st_dev,destination.st_ino)!=(info.st_dev,info.st_ino) or
                (temporary.st_dev,temporary.st_ino)!=(info.st_dev,info.st_ino) or
                destination.st_nlink != 2 or temporary.st_nlink != 2):
                raise ValueError('published links changed during recovery')
            os.unlink(name,dir_fd=dir_fd)
            final=os.fstat(fd)
            destination=os.stat(path.name,dir_fd=dir_fd,follow_symlinks=False)
            if (final.st_nlink != 1 or
                (destination.st_dev,destination.st_ino)!=(info.st_dev,info.st_ino)):
                raise ValueError('published link changed after recovery')
        return raw
    finally:
        if fd is not None:
            os.close(fd)
        os.close(dir_fd)


def split_lattice_tile(manifest, tile):
    index=manifest['tiles'].index(tile)
    x0,y0,x1,y1=tile['bounds_microdegrees']
    if x1-x0 >= y1-y0:
        middle=(x0+x1)//2
        children=[[x0,y0,middle,y1],[middle,y0,x1,y1]]
    else:
        middle=(y0+y1)//2
        children=[[x0,y0,x1,middle],[x0,middle,x1,y1]]
    tile['status']='split'
    tile['children']=[tile['id']+'-0',tile['id']+'-1']
    manifest['tiles'][index+1:index+1]=[
        {'id':ident,'bounds_microdegrees':bounds,'status':'pending','attempts':[]}
        for ident,bounds in zip(tile['children'],children)]


def recover_lattice_inflight(directory, manifest_path, manifest):
    """Reconcile a crash once, charging unknown transfer at its reserved maximum."""
    pending=manifest.get('inflight')
    if pending is None:
        return None
    recovery_stop=None
    matching=[tile for tile in manifest['tiles'] if tile['id']==pending['tile_id']]
    if len(matching)!=1 or matching[0]['status']!='pending':
        raise ValueError('invalid in-flight tile state')
    tile=matching[0]; ident=pending['attempt_id']; check_cache_name(ident)
    if any(a.get('attempt_id') == ident for a in tile['attempts']):
        raise ValueError('in-flight attempt already recorded')
    receipt=pending.get('receipt')
    header_path=Path(directory)/f"{tile['id']}.{ident}.headers"
    body_path=Path(directory)/f"{tile['id']}.{ident}.json"
    attempt={'attempt_id':ident, 'requested_at':pending['requested_at'], 'recovered':True}
    if receipt is None:
        attempt.update(http_status='unknown',curl_exit=None,bytes=pending['body_reserve'],
                       header_bytes=pending['header_reserve'],
                       accounting='conservative_upper_bound_after_interruption')
    else:
        if (receipt['attempt_id'] != ident or receipt['bytes'] > pending['body_reserve'] or
            receipt['header_bytes'] > pending['header_reserve'] or receipt['bytes'] < 0 or receipt['header_bytes'] < 0):
            raise ValueError('invalid in-flight receipt')
        attempt.update({k:receipt[k] for k in ('http_status','curl_exit','bytes','header_bytes','completed_at')})
        if header_path.exists() or header_path.is_symlink():
            raw_header=read_recoverable_published(header_path,receipt.get('header_temp'),'header',tile['id'],
                                                  receipt['header_bytes'],receipt['header_sha256'])
            attempt['header_path']=str(header_path)
        if body_path.exists() or body_path.is_symlink():
            raw=read_recoverable_published(body_path,receipt.get('body_temp'),'body',tile['id'],
                                           receipt['bytes'],receipt['sha256'])
            if not receipt['valid'] or not isinstance(strict_json(raw).get('elements'),list):
                raise ValueError('published body mismatch after interruption')
            if 'header_path' not in attempt:
                raise ValueError('published body without its response header')
            tile.update(status='success',url=bbox_url(tile['bounds_microdegrees']),path=str(body_path),
                        bytes=len(raw),sha256=receipt['sha256'],completed_at=receipt['completed_at'])
        elif receipt['too_many_nodes'] and tile['id'].count('-') < 8:
            split_lattice_tile(manifest,tile)
        elif receipt['curl_exit']==0 and receipt['http_status']=='200' and not receipt['valid']:
            tile['status']='failed_invalid_json'
        elif (receipt['curl_exit'] != 0 or receipt['http_status'] != '200'):
            exhausted = (receipt['curl_exit'] == 63 or
                         receipt['bytes'] + receipt['header_bytes'] >= pending['body_reserve'])
            if exhausted:
                recovery_stop='byte_limit'
            elif len(tile['attempts']) + 1 >= 3:
                tile['status']='failed_request'
    manifest['bytes_transferred'] += attempt['bytes']
    manifest['headers_bytes_transferred'] += attempt['header_bytes']
    # The precise elapsed time is unknowable after a crash; charge the full
    # request timeout so restart cannot silently regain that time allowance.
    manifest['elapsed_seconds'] += pending['timeout_seconds']
    tile['attempts'].append(attempt)
    del manifest['inflight']
    if recovery_stop is not None:
        manifest['status']=recovery_stop
    save_manifest(manifest_path,manifest)
    return recovery_stop


def recover_legacy_orphans(directory, manifest_path, manifest):
    """Do not silently regain an unjournaled legacy request's allowance."""
    orphans=[]
    for tile in manifest['tiles']:
        if tile['status'] != 'pending' or tile.get('legacy_orphan_accounted'):
            continue
        names=[tile['id']+'.json', f"{tile['id']}.attempt-{len(tile['attempts'])+1:03d}.headers"]
        for name in names:
            check_cache_name(name)
        if any((Path(directory)/name).exists() or (Path(directory)/name).is_symlink() for name in names):
            orphans.append(tile)
    if len(orphans)>1:
        raise ValueError('multiple unjournaled legacy orphan tiles require manual review')
    if not orphans:
        return
    tile=orphans[0]
    # Old versions had no receipt and could have consumed their entire
    # remaining allowance even when only a header was published. The old
    # orphan files are neither trusted nor deleted.
    charge=max(0, manifest.get('byte_limit',MAX_BYTES)-manifest['bytes_transferred']-
               manifest['headers_bytes_transferred'])
    tile['attempts'].append({'attempt_id':f"legacy-orphan-{len(tile['attempts'])}",
                             'http_status':'unknown','curl_exit':None,'bytes':charge,'header_bytes':0,
                             'accounting':'conservative_combined_upper_bound_for_legacy_orphan'})
    tile['legacy_orphan_accounted']=True
    manifest['bytes_transferred'] += charge
    manifest['elapsed_seconds']=max(manifest['elapsed_seconds'],manifest.get('time_limit_seconds',MAX_SECONDS))
    save_manifest(manifest_path,manifest)


def acquire_lattice(directory, additional_bytes=None, additional_seconds=None):
    """Bounded request-only OSM acquisition. The manifest counts every response body."""
    directory = Path(directory)
    make_private_directories(directory)
    if directory.resolve() in (Path('/tmp'), ROOT.resolve()):
        raise ValueError('private output directory must be dedicated to this acquisition')
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(directory_fd, 0o700)
    finally:
        os.close(directory_fd)
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['version'] != 'extended-access-lattice-v1' or manifest['envelope_microdegrees'] != list(LATTICE):
            raise ValueError('cache manifest identity mismatch')
    else:
        manifest = {'version': 'extended-access-lattice-v1', 'envelope_microdegrees': list(LATTICE),
                    'step_microdegrees': STEP, 'byte_limit': MAX_BYTES, 'time_limit_seconds': MAX_SECONDS,
                    'bytes_transferred': 0, 'headers_bytes_transferred': 0,
                    'protocol_reserve_bytes': PROTOCOL_RESERVE, 'elapsed_seconds': 0, 'started_at': utc_now(),
                    'attribution': '© OpenStreetMap contributors', 'license': 'ODbL-1.0',
                    'status': 'in_progress', 'tiles': lattice_roots()}
        save_manifest(manifest_path, manifest)
    if (additional_bytes is None) != (additional_seconds is None):
        raise ValueError('additional byte and time limits must be supplied together')
    if additional_bytes is not None and (not 0 < additional_bytes <= 100_000_000 or not 0 < additional_seconds <= 180):
        raise ValueError('additional limits exceed authorized bounds')
    if 'headers_bytes_transferred' not in manifest:
        # First run recorded one request per tile and retained the headers.
        # Refuse to guess if retries make those historical totals ambiguous.
        total_headers = 0
        for tile in manifest['tiles']:
            attempts = tile.get('attempts', [])
            if len(attempts) > 1:
                raise ValueError('cannot recover historical retry headers')
            if attempts:
                header = directory / (tile['id'] + '.headers')
                if not header.is_file():
                    raise ValueError('missing historical response headers')
                attempts[0]['header_bytes'] = header.stat().st_size
                total_headers += attempts[0]['header_bytes']
        manifest['headers_bytes_transferred'] = total_headers
    recovery_stop=recover_lattice_inflight(directory, manifest_path, manifest)
    recover_legacy_orphans(directory, manifest_path, manifest)
    if additional_bytes is not None:
        # Do not trust previously cached success records without checking them.
        for tile in manifest['tiles']:
            if tile['status'] == 'success':
                raw = Path(tile['path']).read_bytes()
                if len(raw) != tile['bytes'] or hash_bytes(raw) != tile['sha256'] or tile['url'] != bbox_url(tile['bounds_microdegrees']):
                    raise ValueError('cached tile hash or URL mismatch')
    start = time.monotonic()
    prior_elapsed = manifest['elapsed_seconds']
    prior_body = manifest['bytes_transferred']
    prior_headers = manifest['headers_bytes_transferred']
    byte_ceiling = MAX_BYTES if additional_bytes is None else prior_body + prior_headers + additional_bytes
    elapsed_ceiling = MAX_SECONDS if additional_seconds is None else prior_elapsed + additional_seconds
    run = {'started_at': utc_now(), 'additional_byte_limit': additional_bytes,
           'additional_time_limit_seconds': additional_seconds, 'body_bytes_before': prior_body,
           'header_bytes_before': prior_headers}
    if additional_bytes is not None:
        manifest.setdefault('resumed_runs', []).append(run)
        save_manifest(manifest_path, manifest)
    stop = recovery_stop if additional_bytes is None else None
    i = 0
    while i < len(manifest['tiles']) and stop is None:
        tile = manifest['tiles'][i]
        if tile['status'] != 'pending':
            i += 1
            continue
        remaining = byte_ceiling - PROTOCOL_RESERVE - manifest['bytes_transferred'] - manifest.get('headers_bytes_transferred', 0)
        seconds = elapsed_ceiling - prior_elapsed - (time.monotonic()-start)
        if remaining <= 0 or seconds < 2:
            stop = 'byte_limit' if remaining <= 0 else 'time_limit'
            break
        bounds = tile['bounds_microdegrees']
        url = bbox_url(bounds)
        requested = utc_now()
        attempt_id=secrets.token_hex(16)
        manifest['elapsed_seconds']=round(prior_elapsed+time.monotonic()-start,3)
        manifest['inflight']={'tile_id':tile['id'],'attempt_id':attempt_id,'requested_at':requested,
                              'body_reserve':remaining,'header_reserve':PROTOCOL_RESERVE,
                              'timeout_seconds':min(25,max(1,int(seconds)-1))}
        save_manifest(manifest_path,manifest)
        def record_receipt(receipt):
            manifest['inflight']['receipt']=receipt
            save_manifest(manifest_path,manifest)
        result, raw, header_size, valid, target, header_path = fetch_lattice_tile(
            directory, tile, url, remaining, seconds, attempt_id, record_receipt)
        size = len(raw)
        manifest['bytes_transferred'] += size
        manifest['headers_bytes_transferred'] = manifest.get('headers_bytes_transferred', 0) + header_size
        attempt = {'attempt_id':attempt_id, 'requested_at': requested, 'completed_at': utc_now(), 'http_status': result.stdout[-3:],
                   'curl_exit': result.returncode, 'bytes': size, 'header_bytes': header_size,
                   'header_path': header_path}
        tile['attempts'].append(attempt)
        del manifest['inflight']
        status = attempt['http_status']
        if result.returncode == 0 and status == '200':
            if not valid:
                tile['status'] = 'failed_invalid_json'
            else:
                tile.update(status='success', url=url, path=target, bytes=size,
                            sha256=hash_bytes(raw), completed_at=attempt['completed_at'])
        elif status == '400' and b'too many nodes' in raw.lower() and tile['id'].count('-') < 8:
            split_lattice_tile(manifest,tile)
        else:
            if result.returncode == 63 or manifest['bytes_transferred'] + manifest['headers_bytes_transferred'] >= byte_ceiling - PROTOCOL_RESERVE:
                stop = 'byte_limit'
            elif len(tile['attempts']) >= 3:
                tile['status'] = 'failed_request'
            else:
                manifest['elapsed_seconds'] = round(prior_elapsed + time.monotonic()-start, 3)
                save_manifest(manifest_path, manifest)
                continue
        manifest['elapsed_seconds'] = round(prior_elapsed + time.monotonic()-start, 3)
        save_manifest(manifest_path, manifest)
        if stop:
            break
        i += 1
    manifest['elapsed_seconds'] = round(prior_elapsed + time.monotonic()-start, 3)
    manifest['finished_at'] = utc_now()
    if additional_bytes is not None:
        run.update(finished_at=manifest['finished_at'], response_body_bytes=manifest['bytes_transferred']-prior_body,
                   response_header_bytes=manifest['headers_bytes_transferred']-prior_headers,
                   elapsed_seconds=round(time.monotonic()-start, 3))
    manifest['status'] = stop or ('complete_requests' if all(t['status'] in ('success','split') for t in manifest['tiles']) else 'incomplete_requests')
    save_manifest(manifest_path, manifest)
    return {'status': manifest['status'], 'response_body_bytes': manifest['bytes_transferred'],
            'response_header_bytes': manifest.get('headers_bytes_transferred', 0), 'elapsed_seconds': manifest['elapsed_seconds'],
            'resumed_run': run if additional_bytes is not None else None,
            'tile_statuses': dict(Counter(t['status'] for t in manifest['tiles']))}


def coastline_topology(coast_values, node_values, coast_conflicts, node_conflicts):
    """Trace mapped coastline through node identities; never polygonize or infer joins."""
    from shapely.geometry import LineString, Point, box
    from shapely.ops import unary_union
    transformer = Transformer.from_crs(4326, 25830, always_xy=True)
    area = box(*BOUNDS)
    adjacency, incoming, outgoing = {}, Counter(), Counter()
    ways, excluded, degenerate = [], 0, 0
    for ident, (_, nodes, _, _) in coast_values.items():
        if ident in coast_conflicts or any(n in node_conflicts or n not in node_values for n in nodes):
            excluded += 1
            continue
        ways.append((ident, nodes))
        for a,b in zip(nodes,nodes[1:]):
            if a == b:
                degenerate += 1
            adjacency.setdefault(a,set()).add(b)
            adjacency.setdefault(b,set()).add(a)
            outgoing[a] += 1
            incoming[b] += 1
    component_of, components = {}, []
    unseen = set(adjacency)
    while unseen:
        first = unseen.pop()
        todo, members = [first], {first}
        while todo:
            for neighbor in adjacency[todo.pop()] & unseen:
                unseen.remove(neighbor)
                members.add(neighbor)
                todo.append(neighbor)
        index = len(components)
        components.append(members)
        component_of.update((node,index) for node in members)
    geometries = [[] for _ in components]
    for _, nodes in ways:
        points = [transformer.transform(node_values[n][1],node_values[n][2]) for n in nodes]
        geometries[component_of[nodes[0]]].append(LineString(points))
    details = []
    for members, lines in zip(components, geometries):
        intersects = any(line.intersects(area) for line in lines)
        if not intersects:
            continue
        ends = [n for n in members if incoming[n]+outgoing[n] == 1]
        abnormal = [n for n in members if incoming[n]+outgoing[n] != 2 and n not in ends]
        inconsistent = [n for n in members if incoming[n]+outgoing[n] == 2 and (incoming[n],outgoing[n]) != (1,1)]
        end_detail = []
        for n in sorted(ends):
            x,y = transformer.transform(node_values[n][1], node_values[n][2])
            distance = area.boundary.distance(Point(x,y))
            position = 'boundary' if distance < 0.01 else ('inside' if area.contains(Point(x,y)) else 'outside')
            end_detail.append({'node_id': n, 'position': position,
                               'projected_xy_m': [round(x,2),round(y,2)],
                               'incoming': incoming[n], 'outgoing': outgoing[n]})
        contacts = [line.intersection(area.boundary) for line in lines if line.intersects(area.boundary)]
        merged = unary_union(contacts) if contacts else None
        crossings, nonpoint = [], False
        def collect(g):
            nonlocal nonpoint
            if g.is_empty:
                return
            if g.geom_type == 'Point':
                crossings.append([round(g.x,2),round(g.y,2)])
            elif hasattr(g, 'geoms'):
                for part in g.geoms:
                    collect(part)
            else:
                nonpoint = True
        if merged is not None:
            collect(merged)
        details.append({'node_count':len(members),'way_count':len(lines), 'endpoints':end_detail,
                        'branch_or_isolated_nodes':len(abnormal), 'direction_inconsistent_nodes':len(inconsistent),
                        'boundary_intersections':sorted(crossings), 'boundary_overlap_or_nonpoint_intersection':nonpoint})
    details.sort(key=lambda item:(item['boundary_intersections'],item['node_count']))
    return {'intersecting_components':details,'all_source_components':len(components),
            'excluded_ways':excluded,'degenerate_segments':degenerate,
            'interpretation':'source-node topology only; no proof of missing shoreline, land, water or polygon validity'}


def audit_lattice(manifest, samples=None):
    """Independently check lattice coverage, raw hashes, and OSM identities."""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    if (manifest.get('version') != 'extended-access-lattice-v1' or
        manifest.get('envelope_microdegrees') != list(LATTICE) or manifest.get('step_microdegrees') != STEP):
        raise ValueError('lattice identity mismatch')
    if 'bytes_transferred' in manifest:
        accounted = sum(a['bytes'] for tile in manifest['tiles'] for a in tile.get('attempts', []))
        if accounted != manifest['bytes_transferred']:
            raise ValueError('request body byte accounting mismatch')
    if 'headers_bytes_transferred' in manifest:
        accounted_headers = sum(a.get('header_bytes', 0) for tile in manifest['tiles'] for a in tile.get('attempts', []))
        if accounted_headers != manifest['headers_bytes_transferred']:
            raise ValueError('response header byte accounting mismatch')
    records = {t['id']: t for t in manifest['tiles']}
    if len(records) != len(manifest['tiles']):
        raise ValueError('duplicate tile identity')
    expected = {t['id']: t['bounds_microdegrees'] for t in lattice_roots()}
    visited, leaves = set(), []
    def visit(ident, bounds):
        if ident in visited or ident not in records or records[ident]['bounds_microdegrees'] != bounds:
            raise ValueError('missing or inconsistent lattice tile')
        visited.add(ident)
        tile = records[ident]
        if tile['status'] == 'split':
            x0,y0,x1,y1 = bounds
            if x1-x0 >= y1-y0:
                m = (x0+x1)//2
                children = [[x0,y0,m,y1], [m,y0,x1,y1]]
            else:
                m = (y0+y1)//2
                children = [[x0,y0,x1,m], [x0,m,x1,y1]]
            ids = [ident+'-0',ident+'-1']
            if tile['children'] != ids:
                raise ValueError('invalid split identities')
            for child, child_bounds in zip(ids, children):
                visit(child, child_bounds)
        elif tile['status'] in ('pending','success','failed_request','failed_invalid_json'):
            leaves.append(tile)
        else:
            raise ValueError('invalid tile status')
    for ident, bounds in expected.items():
        visit(ident, bounds)
    if visited != set(records):
        raise ValueError('unbacked lattice tile')
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326,25830,always_xy=True)
    inverse = Transformer.from_crs(25830,4326,always_xy=True)
    def project_box(bounds):
        w,s,e,n = [x/1_000_000 for x in bounds]
        points = [(w,s),(e,s),(e,n),(w,n)]
        return Polygon([transformer.transform(a[0]+(b[0]-a[0])*i/32,
                                               a[1]+(b[1]-a[1])*i/32)
                        for a,b in zip(points,points[1:]+points[:1]) for i in range(32)])
    grid = box(*BOUNDS)
    success = [t for t in leaves if t['status'] == 'success']
    sample_count = 0
    if samples is not None:
        if samples.get('version') != 'extended-access-osm-samples-v1' or samples.get('status') != 'partial_samples_not_complete_coverage':
            raise ValueError('unexpected sample manifest')
        names = set()
        for sample in samples['tiles']:
            if sample['name'] in names:
                raise ValueError('duplicate sample tile identity')
            names.add(sample['name'])
            parsed = urllib.parse.urlsplit(sample['url'])
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            bounds = [round(v*1_000_000) for v in sample['bbox_wgs84']]
            if (parsed.scheme,parsed.netloc,parsed.path) != ('https','api.openstreetmap.org','/api/0.6/map.json') or set(query) != {'bbox'} or [round(float(v)*1_000_000) for v in query['bbox'][0].split(',')] != bounds:
                raise ValueError('sample URL or bbox mismatch')
            success.append({'id': 'sample:'+sample['name'], 'bounds_microdegrees': bounds,
                            'url': bbox_url(bounds), 'path': sample['path'],
                            'bytes': sample['bytes'], 'sha256': sample['sha256']})
            sample_count += 1
    polygons = [project_box(t['bounds_microdegrees']) for t in success]
    union = unary_union(polygons)
    missing = grid.difference(union).area
    overlap = sum(p.area for p in polygons) - union.area
    full = partial = 0
    for r in range(HEIGHT):
        for c in range(WIDTH):
            cell = box(*cell_bounds(r,c))
            if union.covers(cell):
                full += 1
            elif union.intersects(cell):
                partial += 1
    x0,y0,x1,y1 = BOUNDS
    projected_ring = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    boundary_wgs = Polygon([inverse.transform(a[0]+(b[0]-a[0])*i/512,
                                              a[1]+(b[1]-a[1])*i/512)
                            for a,b in zip(projected_ring,projected_ring[1:]+projected_ring[:1]) for i in range(512)])
    envelope = box(*(v/1_000_000 for v in LATTICE))
    node_values, road_values, coast_values, restriction_values = {}, {}, {}, {}
    node_conflicts, road_conflicts, coast_conflicts, restriction_conflicts = set(), set(), set(), set()
    all_way_ids, all_relation_ids, restriction_members = set(), set(), {}
    duplicated_nodes = duplicated_roads = duplicated_coasts = 0
    def add(store, conflicts, ident, value):
        if ident in store:
            if store[ident] != value:
                conflicts.add(ident)
            return 1
        store[ident] = value
        return 0
    for tile in success:
        if tile.get('url') != bbox_url(tile['bounds_microdegrees']):
            raise ValueError('tile URL does not match bounds')
        raw = Path(tile['path']).read_bytes()
        if len(raw) != tile['bytes'] or hash_bytes(raw) != tile['sha256']:
            raise ValueError('lattice tile hash mismatch')
        obj = json.loads(raw)
        nodes, ways = index_source(obj)
        all_way_ids.update(way['id'] for way in ways)
        for relation in (e for e in obj['elements'] if e['type'] == 'relation'):
            all_relation_ids.add(relation['id'])
            tags = relation.get('tags', {})
            if tags.get('type', '').startswith('restriction'):
                ident = relation['id']
                members = relation.get('members', [])
                value = (relation.get('version'), canonical(tags), canonical(members))
                add(restriction_values, restriction_conflicts, ident, value)
                restriction_members.setdefault(ident, members)
        for ident, node in nodes.items():
            duplicated_nodes += add(node_values,node_conflicts,ident,
                                    (node.get('version'),node['lon'],node['lat']))
        for way in ways:
            tags = way.get('tags', {})
            value = (way.get('version'), tuple(way['nodes']), tags.get('highway'), tags.get('natural'))
            if 'highway' in tags:
                duplicated_roads += add(road_values,road_conflicts,way['id'],value)
            if tags.get('natural') == 'coastline':
                duplicated_coasts += add(coast_values,coast_conflicts,way['id'],value)
    # Conflicting identities are excluded from the endpoint graph; no geometric
    # joining, OSM version selection or polygon closure is inferred.
    degree = Counter()
    adjacency = {}
    skipped = 0
    for ident, (_, ns, _, _) in coast_values.items():
        if ident in coast_conflicts or any(n in node_conflicts for n in ns):
            skipped += 1
            continue
        a,b = ns[0],ns[-1]
        degree[a] += 1
        degree[b] += 1
        adjacency.setdefault(a,set()).add(b)
        adjacency.setdefault(b,set()).add(a)
    unseen = set(adjacency)
    components = 0
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            for neighbor in adjacency[stack.pop()] & unseen:
                unseen.remove(neighbor)
                stack.append(neighbor)
    missing_details = []
    for ident, members in restriction_members.items():
        missing_members = [member for member in members
                           if (member['type'] == 'node' and member['ref'] not in node_values) or
                              (member['type'] == 'way' and member['ref'] not in all_way_ids) or
                              (member['type'] == 'relation' and member['ref'] not in all_relation_ids)]
        if missing_members:
            missing_details.append({'relation_id': ident, 'members': missing_members})
    missing_details.sort(key=lambda item: item['relation_id'])
    missing_tiles = [{'id': t['id'], 'bounds_wgs84': [v/1_000_000 for v in t['bounds_microdegrees']]}
                     for t in leaves if t['status'] != 'success' and project_box(t['bounds_microdegrees']).intersects(grid)]
    return {'status': manifest.get('status'), 'bytes_transferred': manifest.get('bytes_transferred'),
            'elapsed_seconds': manifest.get('elapsed_seconds'), 'root_tiles': len(expected),
            'additional_sample_tiles': sample_count,
            'leaf_statuses': dict(Counter(t['status'] for t in leaves)),
            'estimated_map_area_fraction_covered_by_successful_request_boxes': round(1-missing/grid.area, 6),
            'estimated_uncovered_map_area_m2': round(missing, 2),
            'estimated_overlap_area_m2': round(max(0,overlap), 2),
            'estimated_cells_fully_inside_successful_boxes': full,
            'estimated_cells_partly_intersecting_successful_boxes': partial,
            'estimated_cells_outside_successful_boxes': WIDTH*HEIGHT-full-partial,
            'padded_envelope_covers_sampled_projected_boundary': envelope.covers(boundary_wgs),
            'unique_node_ids': len(node_values), 'duplicate_node_occurrences': duplicated_nodes,
            'conflicting_node_ids': len(node_conflicts), 'unique_highway_way_ids': len(road_values),
            'duplicate_highway_way_occurrences': duplicated_roads, 'conflicting_highway_way_ids': len(road_conflicts),
            'unique_coastline_way_ids': len(coast_values), 'duplicate_coastline_way_occurrences': duplicated_coasts,
            'conflicting_coastline_way_ids': len(coast_conflicts), 'coastline_ways_excluded_from_endpoint_graph': skipped,
            'coastline_endpoint_components': components, 'coastline_degree_one_nodes': sum(v == 1 for v in degree.values()),
            'coastline_degree_other_than_two_nodes': sum(v != 2 for v in degree.values()),
            'coastline_topology': coastline_topology(coast_values, node_values, coast_conflicts, node_conflicts),
            'restriction_relation_ids': len(restriction_values),
            'conflicting_restriction_relation_ids': len(restriction_conflicts),
            'restriction_relations_with_missing_members': len(missing_details),
            'restriction_missing_member_details': missing_details,
            'pending_or_failed_tiles_intersecting_map': missing_tiles,
            'unknown_surface_cells': WIDTH*HEIGHT}


def road_policy():
    """Reuse the existing conservative source-tag policy, without modifying it."""
    spec = importlib.util.spec_from_file_location('coastal_road_policy', ROOT / 'eval/prepare_coastal_roads.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_complete_road_sources(manifest_path, sample_manifest_path=None):
    manifest_path = Path(manifest_path)
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    sample_raw = Path(sample_manifest_path).read_bytes() if sample_manifest_path else None
    samples = json.loads(sample_raw) if sample_raw is not None else None
    audit = audit_lattice(manifest,samples)
    if (manifest['status'] != 'complete_requests' or
        audit['leaf_statuses'] != {'success': len([t for t in manifest['tiles'] if t['status'] == 'success'])} or
        audit['estimated_uncovered_map_area_m2'] > 0.01 or
        not audit['padded_envelope_covers_sampled_projected_boundary'] or
        any(audit[k] for k in ('conflicting_node_ids', 'conflicting_highway_way_ids', 'conflicting_restriction_relation_ids'))):
        raise ValueError('road source footprint or identities are incomplete')
    nodes, ways, restrictions = {}, {}, {}
    def merge(target, item, fields):
        record = {key:item[key] for key in fields if key in item}
        previous = target.get(item['id'])
        if previous is not None and previous != record:
            raise ValueError('conflicting full source tags or identity')
        target[item['id']] = record
    source_tiles = [tile for tile in manifest['tiles'] if tile['status'] == 'success']
    if samples is not None:
        source_tiles += samples['tiles']
    for tile in source_tiles:
        obj = json.loads(Path(tile['path']).read_bytes())
        for e in obj['elements']:
            if e['type'] == 'node':
                merge(nodes, e, ('id','version','lon','lat','tags'))
            elif e['type'] == 'way' and 'highway' in e.get('tags', {}):
                merge(ways, e, ('id','version','nodes','tags'))
            elif e['type'] == 'relation' and e.get('tags', {}).get('type', '').startswith('restriction'):
                merge(restrictions, e, ('id','version','members','tags'))
    source_id = hash_bytes(canonical({'lattice_manifest_sha256':hash_bytes(raw_manifest),
                                     'sample_manifest_sha256':hash_bytes(sample_raw) if sample_raw is not None else None}))
    return nodes, ways, restrictions, source_id, audit


def graph_inputs(nodes, ways, restrictions):
    policy = road_policy()
    blocked_ways, blocked_via_nodes = set(), set()
    for relation in restrictions.values():
        for member in relation['members']:
            if member['type'] == 'way':
                blocked_ways.add(member['ref'])
            elif member['type'] == 'node' and member.get('role') == 'via':
                blocked_via_nodes.add(member['ref'])
    blocked_nodes = set(blocked_via_nodes)
    for ident, node in nodes.items():
        tags = node.get('tags', {})
        barrier = tags.get('barrier')
        if policy.access_reason(tags) or (barrier not in {None, 'no', 'cattle_grid'} and
              (barrier not in {'gate', 'lift_gate', 'swing_gate'} or
               policy.effective_access(tags) not in policy.ALLOWED_ACCESS)):
            blocked_nodes.add(ident)
    return policy, blocked_ways, blocked_nodes


def compile_road_candidate(nodes, ways, restrictions, source_manifest_sha256):
    policy, blocked_ways, blocked_nodes = graph_inputs(nodes, ways, restrictions)
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326,25830,always_xy=True)
    projected = {}
    def position(n):
        if n not in projected:
            node = nodes[n]
            projected[n] = transformer.transform(node['lon'],node['lat'])
        return projected[n]
    arcs, used_nodes, used_ways = [], set(), set()
    boundary_evidence, boundary_nodes, boundary_ways = [], set(), set()
    for ident, way in sorted(ways.items()):
        if ident in blocked_ways or policy.way_reason(way.get('tags', {})):
            continue
        if any(n not in nodes for n in way['nodes']):
            raise ValueError('road way missing mapped node')
        for index, (a,b) in enumerate(zip(way['nodes'], way['nodes'][1:])):
            if a in blocked_nodes or b in blocked_nodes:
                continue
            p,q = position(a),position(b)
            if p == q or clip(p,q,BOUNDS) is None:
                continue
            directions = policy.directions(way.get('tags', {}))
            if not (inside_grid(p) and inside_grid(q)):
                boundary_evidence.append({'way':ident,'segment':index,'nodes':[a,b],
                                          'directions':list(directions)})
                boundary_nodes.update((a,b)); boundary_ways.add(ident)
                continue
            used_nodes.update((a,b))
            used_ways.add(ident)
            for direction in directions:
                source,target = (a,b) if direction == 'f' else (b,a)
                arcs.append({'way':ident,'segment':index,'direction':direction,'source':source,'target':target})
    return {'version':'extended-access-directed-road-candidate-v2',
            'status':'source_candidate_not_operational_access', 'crs':'EPSG:25830', 'bounds':list(BOUNDS),
            'source_manifest_sha256':source_manifest_sha256,
            'restriction_policy':'retain all source restrictions; remove all referenced ways and edges incident to via nodes',
            'boundary_policy':'only arcs with both original endpoints inside bounds; crossings are evidence only',
            'nodes':[dict(nodes[n], position_m=list(position(n))) for n in sorted(used_nodes)],
            'ways':[ways[w] for w in sorted(used_ways)],
            'boundary_evidence':boundary_evidence,
            'boundary_nodes':[dict(nodes[n], position_m=list(position(n))) for n in sorted(boundary_nodes)],
            'boundary_ways':[ways[w] for w in sorted(boundary_ways)],
            'restriction_relations':[restrictions[r] for r in sorted(restrictions)],
            'restriction_way_evidence':[ways[w] for w in sorted(blocked_ways & ways.keys())],
            'restriction_node_evidence':[nodes[n] for n in sorted({m['ref'] for r in restrictions.values()
                for m in r['members'] if m['type'] == 'node'} & nodes.keys())],
            'arcs':arcs}


def validate_road_candidate(candidate, nodes, ways, restrictions, source_manifest_sha256):
    """Check directed arcs independently against unsimplified source and GEOS."""
    from shapely.geometry import LineString, Point, box
    policy, blocked_ways, blocked_nodes = graph_inputs(nodes,ways,restrictions)
    transformer = Transformer.from_crs(4326,25830,always_xy=True)
    area = box(*BOUNDS)
    if (candidate.get('version'),candidate.get('status'),candidate.get('crs'),candidate.get('bounds'),
        candidate.get('source_manifest_sha256'),candidate.get('restriction_policy')) != (
        'extended-access-directed-road-candidate-v2','source_candidate_not_operational_access',
        'EPSG:25830',list(BOUNDS),source_manifest_sha256,
        'retain all source restrictions; remove all referenced ways and edges incident to via nodes'):
        raise ValueError('candidate metadata mismatch')
    if candidate.get('boundary_policy') != 'only arcs with both original endpoints inside bounds; crossings are evidence only':
        raise ValueError('boundary policy mismatch')
    if candidate.get('restriction_relations') != [restrictions[r] for r in sorted(restrictions)]:
        raise ValueError('restriction provenance mismatch')
    if candidate.get('restriction_way_evidence') != [ways[w] for w in sorted(blocked_ways & ways.keys())]:
        raise ValueError('restriction way evidence mismatch')
    referenced_nodes = {m['ref'] for r in restrictions.values() for m in r['members'] if m['type'] == 'node'}
    if candidate.get('restriction_node_evidence') != [nodes[n] for n in sorted(referenced_nodes & nodes.keys())]:
        raise ValueError('restriction node evidence mismatch')
    actual = {}
    for arc in candidate['arcs']:
        if set(arc) != {'way','segment','direction','source','target'}:
            raise ValueError('unexpected arc fields')
        key = (arc['way'],arc['segment'],arc['direction'])
        if key in actual:
            raise ValueError('duplicate arc')
        actual[key] = (arc['source'],arc['target'])
    expected, expected_nodes, expected_ways = {}, set(), set()
    expected_boundary, boundary_nodes, boundary_ways = [], set(), set()
    for ident, way in ways.items():
        if ident in blocked_ways or policy.way_reason(way.get('tags', {})):
            continue
        if any(n not in nodes for n in way['nodes']):
            raise ValueError('road way missing mapped node')
        for index,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
            if a in blocked_nodes or b in blocked_nodes:
                continue
            na,nb = nodes[a],nodes[b]
            p=transformer.transform(na['lon'],na['lat'])
            q=transformer.transform(nb['lon'],nb['lat'])
            if p == q or not LineString([p,q]).intersects(area):
                continue
            directions = policy.directions(way.get('tags', {}))
            if not (area.covers(Point(p)) and area.covers(Point(q))):
                expected_boundary.append({'way':ident,'segment':index,'nodes':[a,b],
                                          'directions':list(directions)})
                boundary_nodes.update((a,b)); boundary_ways.add(ident)
                continue
            expected_nodes.update((a,b)); expected_ways.add(ident)
            for direction in directions:
                expected[(ident,index,direction)] = (a,b) if direction == 'f' else (b,a)
    if actual != expected:
        raise ValueError('directed source topology or coverage mismatch')
    expected_boundary.sort(key=lambda item:(item['way'],item['segment']))
    if candidate.get('boundary_evidence') != expected_boundary:
        raise ValueError('boundary evidence mismatch')
    if candidate.get('boundary_ways') != [ways[w] for w in sorted(boundary_ways)]:
        raise ValueError('boundary source ways mismatch')
    boundary_records = [dict(nodes[n],position_m=list(transformer.transform(nodes[n]['lon'],nodes[n]['lat'])))
                        for n in sorted(boundary_nodes)]
    if candidate.get('boundary_nodes') != boundary_records:
        raise ValueError('boundary mapped node mismatch')
    if candidate['ways'] != [ways[w] for w in sorted(expected_ways)]:
        raise ValueError('source way tags or geometry mismatch')
    expected_node_records=[]
    for ident in sorted(expected_nodes):
        n=nodes[ident]
        expected_node_records.append(dict(n,position_m=list(transformer.transform(n['lon'],n['lat']))))
    if candidate['nodes'] != expected_node_records:
        raise ValueError('mapped node identity or tags mismatch')
    if set(candidate) != {'version','status','crs','bounds','source_manifest_sha256','restriction_policy',
                          'nodes','ways','boundary_policy','boundary_evidence','boundary_nodes','boundary_ways',
                          'restriction_relations','restriction_way_evidence',
                          'restriction_node_evidence','arcs'}:
        raise ValueError('unexpected candidate fields')
    weak = {n: [] for n in expected_nodes}
    directed = {n: [] for n in expected_nodes}
    for a,b in expected.values():
        weak[a].append(b); weak[b].append(a); directed[a].append(b)
    unseen = set(expected_nodes)
    components = []
    while unseen:
        start = next(iter(unseen))
        unseen.remove(start)
        stack = [start]
        component = {start}
        while stack:
            for neighbor in weak[stack.pop()]:
                if neighbor in unseen:
                    unseen.remove(neighbor); component.add(neighbor); stack.append(neighbor)
        components.append(component)
    largest = max(components, key=lambda c:(len(c),-min(c))) if components else set()
    reachable = set()
    start = min(largest) if largest else None
    if start is not None:
        reachable.add(start); stack = [start]
        while stack:
            for neighbor in directed[stack.pop()]:
                if neighbor not in reachable:
                    reachable.add(neighbor); stack.append(neighbor)
    return {'nodes':len(expected_nodes), 'ways':len(expected_ways), 'directed_arcs':len(expected),
            'non_routable_boundary_segments':len(expected_boundary),
            'weak_components':len(components), 'largest_weak_component_nodes':len(largest),
            'directed_reachable_nodes_from_smallest_id_in_largest_component':len(reachable),
            'preserved_restrictions':len(restrictions), 'excluded_restriction_way_ids':len(blocked_ways),
            'excluded_barrier_or_via_node_ids':len(blocked_nodes)}


def coastline_face_diagnostic(points, bounds):
    """Inspect a directed source line and boundary faces, without classifying cells."""
    from shapely.geometry import LineString, Point, box
    from shapely.ops import polygonize, unary_union
    line = LineString(points)
    area = box(*bounds)
    if not line.is_valid or not line.is_simple or line.length <= 0:
        raise ValueError('coastline has invalid or self-intersecting geometry')
    if area.covers(Point(points[0])) or area.covers(Point(points[-1])):
        raise ValueError('coastline endpoint is not outside the domain')
    clipped = line.intersection(area)
    if clipped.is_empty or not clipped.is_simple:
        raise ValueError('coastline does not have simple in-domain geometry')
    contacts = line.intersection(area.boundary)
    if contacts.geom_type == 'Point':
        crossings = [contacts]
    elif contacts.geom_type == 'MultiPoint':
        crossings = list(contacts.geoms)
    else:
        raise ValueError('coastline touches boundary ambiguously or overlaps it')
    if len(crossings) < 2:
        raise ValueError('insufficient boundary crossings')
    # A point contact is a crossing only if the directed line changes from
    # strictly inside to strictly outside, or vice versa. Tangencies and
    # numerically ambiguous boundary contacts fail closed.
    distances = sorted(line.project(point) for point in crossings)
    for i, distance in enumerate(distances):
        before_gap = distance if i == 0 else distance-distances[i-1]
        after_gap = line.length-distance if i+1 == len(distances) else distances[i+1]-distance
        step = min(0.01, before_gap/4, after_gap/4)
        if step <= 0:
            raise ValueError('coastline boundary contact is ambiguous')
        before = line.interpolate(distance-step)
        after = line.interpolate(distance+step)
        before_inside, after_inside = area.contains(before), area.contains(after)
        before_outside, after_outside = not area.covers(before), not area.covers(after)
        if not ((before_inside and after_outside) or (before_outside and after_inside)):
            raise ValueError('coastline boundary contact is tangent or ambiguous')
    faces = list(polygonize(unary_union([area.boundary,clipped])))
    union = unary_union(faces)
    if (not faces or any(not f.is_valid for f in faces) or
        area.symmetric_difference(union).area > 1e-4 or
        abs(sum(f.area for f in faces)-area.area) > 1e-4):
        raise ValueError('boundary closure does not partition the domain')
    sides = [set() for _ in faces]
    tested, unresolved = 0, 0
    for a,b in zip(points,points[1:]):
        segment = LineString([a,b]); part = segment.intersection(area)
        if part.is_empty:
            continue
        if part.geom_type == 'Point':
            continue
        if part.geom_type != 'LineString' or part.length < 0.02:
            unresolved += 1
            continue
        mid = part.interpolate(0.5,normalized=True)
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=math.hypot(dx,dy)
        if not length:
            unresolved += 1
            continue
        found=False
        for eps in (0.01,0.001,0.0001):
            left=Point(mid.x-dy/length*eps,mid.y+dx/length*eps)
            right=Point(mid.x+dy/length*eps,mid.y-dx/length*eps)
            li=[i for i,f in enumerate(faces) if f.contains(left)]
            ri=[i for i,f in enumerate(faces) if f.contains(right)]
            if len(li)==1 and len(ri)==1 and li!=ri:
                sides[li[0]].add('left'); sides[ri[0]].add('right'); found=True; break
        if not found:
            unresolved += 1
        tested += 1
    if unresolved or any(len(side) != 1 for side in sides):
        raise ValueError('coastline faces have ambiguous or conflicting sides')
    summary = []
    for face, side in zip(faces,sides):
        point=face.representative_point()
        summary.append({'side':next(iter(side)),'area_m2':round(face.area,2),
                        'representative_m':[round(point.x,2),round(point.y,2)]})
    summary.sort(key=lambda f:(f['side'],f['area_m2'],f['representative_m']))
    return {'simple':True, 'boundary_crossings':len(crossings), 'faces':summary,
            'oriented_segments_tested':tested,'unresolved_segments':unresolved}


def load_surface_sources(manifest_path, sample_manifest_path):
    nodes,_,_,source_sha,audit = load_complete_road_sources(manifest_path,sample_manifest_path)
    if audit['conflicting_coastline_way_ids'] or audit['coastline_ways_excluded_from_endpoint_graph']:
        raise ValueError('coastline source identities conflict')
    manifest=json.loads(Path(manifest_path).read_bytes())
    if not manifest.get('started_at','').startswith('2026-'):
        raise ValueError('surface assessment requires the documented 2026 source epoch')
    samples=json.loads(Path(sample_manifest_path).read_bytes()) if sample_manifest_path else {'tiles':[]}
    if any('2026' not in tile.get('server_date','') for tile in samples['tiles']):
        raise ValueError('surface sample source epoch is not 2026')
    ways={}
    for tile in [t for t in manifest['tiles'] if t['status']=='success'] + samples['tiles']:
        source=json.loads(Path(tile['path']).read_bytes())
        for way in source['elements']:
            if way['type']!='way' or way.get('tags',{}).get('natural')!='coastline':
                continue
            item={k:way[k] for k in ('id','version','nodes','tags') if k in way}
            if item['id'] in ways and ways[item['id']] != item:
                raise ValueError('conflicting coastline source way')
            ways[item['id']]=item
    return nodes,ways,source_sha


def source_coastline_diagnostic(nodes,ways):
    starts=Counter(w['nodes'][0] for w in ways.values())
    ends=Counter(w['nodes'][-1] for w in ways.values())
    start_nodes=[n for n in starts if starts[n]==1 and ends[n]==0]
    by_start={w['nodes'][0]:w for w in ways.values()}
    if len(start_nodes)!=1 or len(by_start)!=len(ways):
        raise ValueError('coastline source is branched or has no unique start')
    current=start_nodes[0]; sequence=[current]; seen=set()
    while current in by_start:
        way=by_start[current]
        if way['id'] in seen:
            raise ValueError('coastline source repeats a way')
        seen.add(way['id']); sequence.extend(way['nodes'][1:]); current=way['nodes'][-1]
    if len(seen)!=len(ways) or any(n not in nodes for n in sequence):
        raise ValueError('coastline source is incomplete or disconnected')
    transformer=Transformer.from_crs(4326,25830,always_xy=True)
    points=[transformer.transform(nodes[n]['lon'],nodes[n]['lat']) for n in sequence]
    return coastline_face_diagnostic(points,BOUNDS)


def compile_surface_assessment(nodes, ways, source_sha):
    """Fail closed: API request footprints do not certify complete coastal ways."""
    try:
        diagnostic=source_coastline_diagnostic(nodes,ways)
    except ValueError as error:
        diagnostic={'status':'failed', 'reason':str(error)}
    return {'version':'extended-access-coastal-surface-assessment-v1',
            'status':'all_unknown_source_completeness_unverified',
            'source_manifest_sha256':source_sha, 'source_epoch':'2026',
            'grid':{'crs':'EPSG:25830','bounds':list(BOUNDS),'width':WIDTH,'height':HEIGHT,'cell_metres':CELL},
            'coastline_way_count':len(ways), 'diagnostic':diagnostic,
            'blockers':['OSM map bbox API can omit a crossing way with no node inside any request box',
                        'the multi-tile acquisition is non-atomic and does not establish real-world shoreline completeness',
                        'coastline land-side alone does not identify inland water'],
            'surface':[['unknown']*WIDTH for _ in range(HEIGHT)]}


def validate_surface_assessment(candidate,nodes,ways,source_sha):
    if candidate != compile_surface_assessment(nodes,ways,source_sha):
        raise ValueError('surface assessment differs from source-backed fail-closed result')
    return {'unknown_surface_cells':WIDTH*HEIGHT,'classified_cells':0,
            'boundary_crossings':candidate['diagnostic'].get('boundary_crossings'),
            'oriented_segments_tested':candidate['diagnostic'].get('oriented_segments_tested')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('compile', 'validate', 'audit-tiles', 'acquire-lattice', 'audit-lattice', 'compile-roads', 'validate-roads', 'assess-surface', 'validate-surface'))
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--sample-manifest', type=Path)
    parser.add_argument('--additional-bytes', type=int)
    parser.add_argument('--additional-seconds', type=int)
    args = parser.parse_args()
    if args.command in ('assess-surface','validate-surface'):
        if args.manifest is None or args.output is None:
            parser.error('surface commands require --manifest and --output')
        nodes,ways,source_sha = load_surface_sources(args.manifest,args.sample_manifest)
        if args.command == 'assess-surface':
            candidate = compile_surface_assessment(nodes,ways,source_sha)
            write_private_bytes(args.output,canonical(candidate)+b'\n')
            print(json.dumps({'status':candidate['status'],'diagnostic':candidate['diagnostic'],
                              'sha256':hash_bytes(args.output.read_bytes())},sort_keys=True))
        else:
            candidate=strict_json(args.output.read_bytes())
            print(json.dumps(validate_surface_assessment(candidate,nodes,ways,source_sha),sort_keys=True))
        return
    if args.command == 'acquire-lattice':
        if args.directory is None:
            parser.error('acquire-lattice requires --directory')
        print(json.dumps(acquire_lattice(args.directory, args.additional_bytes, args.additional_seconds), sort_keys=True))
        return
    if args.command in ('compile-roads','validate-roads'):
        if args.manifest is None or args.output is None:
            parser.error('road commands require --manifest and --output')
        nodes,ways,restrictions,source_sha,_ = load_complete_road_sources(args.manifest,args.sample_manifest)
        if args.command == 'compile-roads':
            candidate = compile_road_candidate(nodes,ways,restrictions,source_sha)
            write_private_bytes(args.output, gzip.compress(canonical(candidate)+b'\n',mtime=0))
            print(json.dumps({'nodes':len(candidate['nodes']),'ways':len(candidate['ways']),
                              'directed_arcs':len(candidate['arcs']),'restrictions':len(candidate['restriction_relations']),
                              'sha256':hash_bytes(args.output.read_bytes())},sort_keys=True))
        else:
            candidate = strict_json(gzip.decompress(args.output.read_bytes()))
            print(json.dumps(validate_road_candidate(candidate,nodes,ways,restrictions,source_sha),sort_keys=True))
        return
    if args.command == 'audit-lattice':
        if args.manifest is None:
            parser.error('audit-lattice requires --manifest')
        samples = json.loads(args.sample_manifest.read_bytes()) if args.sample_manifest else None
        print(json.dumps(audit_lattice(json.loads(args.manifest.read_bytes()), samples), sort_keys=True))
        return
    if args.command == 'audit-tiles':
        if args.manifest is None:
            parser.error('audit-tiles requires --manifest')
        print(json.dumps(audit_tiles(json.loads(args.manifest.read_bytes())), sort_keys=True))
        return
    if args.output is None:
        parser.error('compile and validate require --output')
    source = load_source(args.source)
    if args.command == 'compile':
        data = compile_observations(source)
        raw = canonical(data) + b'\n'
        write_private_bytes(args.output, gzip.compress(raw, mtime=0))
        print(json.dumps({'segments': len(data['segments']), 'observed_cells': len(data['observed_cells']), 'file_sha256': hash_bytes(args.output.read_bytes())}))
    else:
        data = strict_json(gzip.decompress(args.output.read_bytes()))
        print(json.dumps(validate(data, source), sort_keys=True))


if __name__ == '__main__':
    main()
