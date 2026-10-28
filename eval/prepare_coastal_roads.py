"""Acquire and prepare the bounded coastal OSM road fixture on CPU, offline by default."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/coastal_mobility_v1'
BBOX = (-5.42, 36.36, -4.96, 36.65)
GRAPH_BBOX = (-5.345, 36.36, -5.13, 36.625)
VERSION = 'coastal-road-network-v1'
SOURCE = dict(name='OpenStreetMap coastal Málaga extract',
              url='https://www.openstreetmap.org/copyright',
              attribution='© OpenStreetMap contributors', license='ODbL-1.0')
SPEEDS = dict(motorway=50, motorway_link=25, trunk=45, trunk_link=25,
              primary=40, primary_link=20, secondary=35, secondary_link=20,
              tertiary=30, tertiary_link=20, unclassified=20, residential=20,
              living_street=10, service=10, track=8)
ALLOWED_ACCESS = {'yes', 'permissive', 'designated', 'official'}
KEEP_TAGS = {'highway', 'access', 'vehicle', 'motor_vehicle', 'motorcar',
             'oneway', 'oneway:vehicle', 'oneway:motor_vehicle', 'oneway:motorcar',
             'junction', 'area', 'service', 'surface', 'tracktype', 'smoothness',
             'barrier', 'ford', 'bridge', 'tunnel', 'layer', 'width', 'maxwidth',
             'maxheight', 'maxweight', 'hgv', 'name', 'ref', 'type', 'restriction'}
ACTIVE_GRID = dict(west=297000, north=4054500, cols=37, rows=36, cell_size_m=500)
TARGETS = ([301250, 4046750], [306250, 4046250], [303250, 4045250])
STAGING_ANCHORS = (
    ('Estepona exercise anchor', [307629.249, 4033428.189]),
    ('Casares exercise anchor', [296199.169, 4035654.952]),
    ('San Luis de Sabinillas coastal streets', [298150, 4027350]),
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def network_digest(network):
    return digest(canonical({key: value for key, value in network.items() if key != 'sha256'}))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def write_json(path, value, compact=False):
    path.write_bytes(canonical(value) + b'\n' if compact else
                     (json.dumps(value, sort_keys=True, ensure_ascii=False,
                                 indent=2, allow_nan=False) + '\n').encode('utf-8'))


def acquire(cache_only=False):
    runtime = DATA / '.runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    elements = {}
    requests = []
    started = utc_now()
    west, south, east, north = BBOX
    def acquire_tile(bounds, tile, depth=0):
        url = 'https://api.openstreetmap.org/api/0.6/map.json?bbox=' + ','.join(map(str, bounds))
        raw_path = runtime / f'osm-{tile}.json.gz'
        metadata_path = runtime / f'osm-{tile}.request.json'
        def subdivide():
            left, bottom, right, top = bounds
            if right - left > top - bottom:
                middle = round((left + right) / 2, 7)
                halves = ([left, bottom, middle, top], [middle, bottom, right, top])
            else:
                middle = round((bottom + top) / 2, 7)
                halves = ([left, bottom, right, middle], [left, middle, right, top])
            for index, half in enumerate(halves):
                acquire_tile(half, f'{tile}-{index}', depth + 1)
        if not raw_path.exists() and any(runtime.glob(f'osm-{tile}-*.request.json')):
            subdivide()
            return
        if raw_path.exists() and metadata_path.exists():
            raw = gzip.decompress(raw_path.read_bytes())
            metadata = json.loads(metadata_path.read_bytes())
            if metadata['url'] != url or metadata['sha256'] != digest(raw):
                raise ValueError('Cached acquisition does not match its request/hash')
        else:
            if cache_only:
                raise ValueError(f'Missing cached OSM tile {tile}')
            request = urllib.request.Request(url, headers={
                'User-Agent': 'wildfire-response-env-coastal-fixture/1.0',
                'Accept': 'application/json', 'Accept-Encoding': 'identity'})
            requested = utc_now()
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read(80_000_001)
                    if len(raw) > 80_000_000:
                        raise ValueError('OSM tile exceeds acquisition size limit')
                    metadata = dict(url=url, bbox_wgs84=bounds, requested_at=requested,
                                    acquired_at=utc_now(), server_date=response.headers.get('Date'),
                                    bytes=len(raw), sha256=digest(raw),
                                    raw_cache=str(raw_path.relative_to(ROOT)))
            except urllib.error.HTTPError as error:
                message = error.read().decode('utf-8', errors='replace')
                if error.code != 400 or 'too many nodes' not in message or depth >= 4:
                    raise
                subdivide()
                return
            document = json.loads(raw)
            if not isinstance(document.get('elements'), list):
                raise ValueError('Invalid OSM response')
            raw_path.write_bytes(gzip.compress(raw, mtime=0))
            write_json(metadata_path, metadata)
        document = json.loads(raw)
        for element in document['elements']:
            key = (element['type'], element['id'])
            previous = elements.get(key)
            if previous is None or element.get('version', 0) > previous.get('version', 0):
                elements[key] = element
        requests.append(metadata)
        print(f'acquired tile {tile}: {len(raw)} bytes', file=sys.stderr, flush=True)

    for column in range(4):
        for row in range(2):
            bounds = [round(west + (east - west) * column / 4, 6),
                      round(south + (north - south) * row / 2, 6),
                      round(west + (east - west) * (column + 1) / 4, 6),
                      round(south + (north - south) * (row + 1) / 2, 6)]
            acquire_tile(bounds, f'{column}-{row}')
    ways = [element for (kind, _), element in elements.items()
            if kind == 'way' and 'highway' in element.get('tags', {}) and any(
                clip_segment([elements[('node', first)]['lon'], elements[('node', first)]['lat']],
                             [elements[('node', second)]['lon'], elements[('node', second)]['lat']],
                             GRAPH_BBOX) is not None
                for first, second in zip(element['nodes'], element['nodes'][1:]))]
    node_ids = {node_id for way in ways for node_id in way['nodes']}
    restrictions = [element for (kind, _), element in elements.items()
                    if kind == 'relation' and element.get('tags', {}).get('type') == 'restriction']
    kept = []
    timestamps = []
    for element in sorted(ways + [elements[('node', node_id)] for node_id in node_ids] + restrictions,
                          key=lambda item: (item['type'], item['id'])):
        record = {key: element[key] for key in ('type', 'id', 'lat', 'lon', 'nodes', 'members') if key in element}
        record['tags'] = {key: value for key, value in element.get('tags', {}).items()
                          if key in KEEP_TAGS or ':conditional' in key or key.startswith(
                              ('access:', 'vehicle:', 'motor_vehicle:', 'motorcar:', 'oneway:',
                               'maxweight:', 'maxwidth:', 'maxheight:', 'hgv:', 'restriction:'))}
        kept.append(record)
        if element.get('timestamp'):
            timestamps.append(element['timestamp'])
    snapshot = dict(version='coastal-osm-source-v1', bbox_wgs84=list(GRAPH_BBOX), elements=kept)
    snapshot_bytes = gzip.compress(canonical(snapshot) + b'\n', mtime=0)
    (DATA / 'source_snapshot.json.gz').write_bytes(snapshot_bytes)
    acquisition = dict(version='coastal-road-acquisition-v1', source=SOURCE, requests=requests,
                       acquisition_started_at=min([started] + [item['requested_at'] for item in requests]),
                       acquisition_completed_at=max(item['acquired_at'] for item in requests),
                       source_element_timestamp_min=min(timestamps),
                       source_element_timestamp_max=max(timestamps),
                       source_date_note='OSM current map API at acquisition; non-atomic tiles, not a 2021 historical reconstruction. Highest element version wins overlaps. Initial 4x2 tiling is bisected when the API rejects more than 50000 nodes.',
                       snapshot=dict(path='source_snapshot.json.gz', bytes=len(snapshot_bytes),
                                     sha256=digest(snapshot_bytes), canonical_sha256=digest(canonical(snapshot))),
                       official_source_attempt=dict(url='https://centrodedescargas.cnig.es/CentroDescargas/redes-transporte',
                           result='Automated HTTPS acquisition failed: unexpected TLS EOF; web fetch HTTP 502. No official geometry was acquired.',
                           attempted_on=min(item['requested_at'] for item in requests)[:10]),
                       overpass_attempt=dict(result='overpass-api.de HTTP 406; public kumi/private.coffee mirrors timed out. Used the official OSM map API instead.'))
    write_json(DATA / 'acquisition.json', acquisition)
    towns = []
    for name in ('Estepona', 'Casares', 'Manilva', 'Marbella'):
        candidates = [element for (kind, _), element in elements.items()
                      if kind == 'node' and element.get('tags', {}).get('name') == name
                      and element.get('tags', {}).get('place') in {'town', 'city', 'village'}]
        extra_source = {}
        if not candidates and name == 'Marbella' and (runtime / 'marbella-node.json').exists():
            raw_label = (runtime / 'marbella-node.json').read_bytes()
            candidates = [element for element in json.loads(raw_label)['elements']
                          if element['type'] == 'node' and element.get('tags', {}).get('name') == name
                          and element.get('tags', {}).get('place') == 'city']
            headers = (runtime / 'marbella-node.headers').read_text()
            extra_source = dict(acquisition_server_date=next(
                (line.partition(':')[2].strip() for line in headers.splitlines()
                 if line.lower().startswith('date:')), None),
                raw_sha256=digest(raw_label),
                request_url='https://api.openstreetmap.org/api/0.6/node/1081751552.json',
                resolution='OSM municipal relation 340756 admin_centre node, discovered through Nominatim. The geocoder municipality centroid was not used.')
        if candidates:
            element = min(candidates, key=lambda item: item['id'])
            towns.append(dict(name=name, lonlat=[element['lon'], element['lat']],
                osm_node_id=element['id'], place=element['tags']['place'],
                source_url=f"https://www.openstreetmap.org/node/{element['id']}",
                source_element_timestamp=element.get('timestamp'), **extra_source,
                meaning='OSM named settlement label point, not a population location, count or deployment'))
        else:
            towns.append(dict(name=name, status='not_in_bounded_osm_extract'))
    from pyproj import Transformer, network

    network.set_network_enabled(False)
    transformer = Transformer.from_crs(4326, 25830, always_xy=True)
    for town in towns:
        if 'lonlat' in town:
            town['position'] = projected_position(transformer, town['lonlat'])
    write_json(DATA / 'town_labels.json', dict(source=SOURCE, crs='EPSG:25830',
        acquisition_completed_at=acquisition['acquisition_completed_at'], towns=towns))


def effective_access(tags):
    for key in ('motorcar', 'motor_vehicle', 'vehicle', 'access'):
        if key in tags:
            return tags[key]
    return None


def access_reason(tags):
    if effective_access(tags) not in ALLOWED_ACCESS | {None}:
        return 'restricted_motor_access'
    if tags.get('hgv') not in ALLOWED_ACCESS | {None}:
        return 'restricted_hgv_access'
    if any(key.startswith(('access:', 'vehicle:', 'motor_vehicle:', 'motorcar:', 'hgv:'))
           or (key.startswith('oneway:') and key not in
               {'oneway:motorcar', 'oneway:motor_vehicle', 'oneway:vehicle'})
           for key in tags if ':bicycle' not in key and ':foot' not in key):
        return 'conditional_or_directional_access'
    if any(key.startswith(('maxwidth', 'maxheight', 'maxweight')) for key in tags):
        return 'unevaluated_vehicle_dimensions'
    if tags.get('ford', 'no') != 'no':
        return 'ford'
    if tags.get('smoothness') in {'bad', 'very_bad', 'horrible', 'very_horrible', 'impassable'}:
        return 'poor_smoothness'
    return None


def way_reason(tags):
    road_class = tags.get('highway')
    if road_class not in SPEEDS or tags.get('area') == 'yes':
        return 'non_motor_or_unsupported_class'
    reason = access_reason(tags)
    if reason:
        return reason
    if road_class == 'service' and tags.get('service') in {'driveway', 'parking_aisle', 'drive-through'}:
        return 'private_or_parking_service_class'
    if road_class == 'track':
        surface = tags.get('surface')
        if tags.get('tracktype') not in {None, 'grade1', 'grade2'}:
            return 'rough_track_grade'
        if surface in {'sand', 'mud', 'grass', 'ground', 'earth', 'dirt', 'rock', 'rocks'}:
            return 'rough_track_surface'
        if tags.get('tracktype') not in {'grade1', 'grade2'} and surface not in {
                'paved', 'asphalt', 'concrete', 'concrete:plates', 'concrete:lanes',
                'compacted', 'gravel', 'fine_gravel', 'paving_stones'}:
            return 'ambiguous_track_surface'
    try:
        directions(tags)
    except ValueError:
        return 'ambiguous_oneway'
    return None


def directions(tags):
    value = next((tags[key] for key in ('oneway:motorcar', 'oneway:motor_vehicle',
                 'oneway:vehicle', 'oneway') if key in tags), None)
    if value is None:
        value = 'yes' if tags.get('junction') == 'roundabout' or tags.get('highway') == 'motorway' else 'no'
    if value in {'yes', '1', 'true'}:
        return ('f',)
    if value == '-1':
        return ('r',)
    if value in {'no', '0', 'false'}:
        return ('f', 'r')
    raise ValueError('Unknown/reversible/alternating one-way rule')


def clip_segment(start, end, bbox):
    west, south, east, north = bbox
    delta_lon, delta_lat = end[0] - start[0], end[1] - start[1]
    lower, upper = 0.0, 1.0
    for denominator, numerator in ((-delta_lon, start[0] - west),
                                   (delta_lon, east - start[0]),
                                   (-delta_lat, start[1] - south),
                                   (delta_lat, north - start[1])):
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


def projected_position(transformer, lonlat):
    return [round(value, 1) for value in transformer.transform(*lonlat)]


def simplify_line(coordinates, tolerance=2.0):
    keep = {0, len(coordinates) - 1}
    pending = [(0, len(coordinates) - 1)]
    while pending:
        begin, finish = pending.pop()
        if finish - begin < 2:
            continue
        distance, index = max((nearest_on_line(coordinates[index],
                              [coordinates[begin], coordinates[finish]])[0], index)
                              for index in range(begin + 1, finish))
        if distance > tolerance:
            keep.add(index)
            pending.extend(((begin, index), (index, finish)))
    return [coordinates[index] for index in sorted(keep)]


def nearby_cell_centre(position):
    col = math.floor((position[0] - ACTIVE_GRID['west']) / ACTIVE_GRID['cell_size_m'])
    row = math.floor((ACTIVE_GRID['north'] - position[1]) / ACTIVE_GRID['cell_size_m'])
    if not (1 <= col < ACTIVE_GRID['cols'] - 1 and 1 <= row < ACTIVE_GRID['rows'] - 1):
        return None
    centre = [ACTIVE_GRID['west'] + (col + .5) * ACTIVE_GRID['cell_size_m'],
              ACTIVE_GRID['north'] - (row + .5) * ACTIVE_GRID['cell_size_m']]
    distance = math.dist(position, centre)
    return (row, col, distance) if distance <= 150 else None


def compact_network(network):
    edges = {edge['id']: edge for edge in network['edges']}
    lineage = {edge_id: {edge_id.rsplit(':', 1)[0]} for edge_id in edges}
    incident = defaultdict(set)
    for edge_id, edge in edges.items():
        incident[edge['source']].add(edge_id)
        incident[edge['target']].add(edge_id)
    pending = list(incident)
    heapq.heapify(pending)
    removed_nodes = set()
    protected_nodes = {node['id'] for node in network['nodes'] if nearby_cell_centre(node['position'])}
    while pending:
        node_id = heapq.heappop(pending)
        if node_id in protected_nodes:
            continue
        edge_ids = incident[node_id]
        adjacent = [edges[edge_id] for edge_id in sorted(edge_ids)]
        neighbors = {edge[key] for edge in adjacent for key in ('source', 'target')} - {node_id}
        if len(neighbors) != 2 or len(adjacent) not in {2, 4}:
            continue
        if any(edge['source'] == edge['target'] for edge in adjacent):
            continue
        if len({(edge['road_class'], edge['speed_kph']) for edge in adjacent}) != 1:
            continue
        incoming = [edge for edge in adjacent if edge['target'] == node_id]
        outgoing = [edge for edge in adjacent if edge['source'] == node_id]
        pairs = [(first, second) for first in incoming for second in outgoing
                 if first['source'] != second['target']]
        if len(pairs) * 2 != len(adjacent):
            continue
        if {edge['id'] for pair in pairs for edge in pair} != edge_ids:
            continue
        merged = []
        for first, second in pairs:
            original_ids = lineage[first['id']] | lineage[second['id']]
            identity = digest(canonical([sorted(original_ids), sorted(neighbors)]))[:20]
            direction = 'f' if first['source'] < second['target'] else 'r'
            coordinates = first['coordinates'] + second['coordinates'][1:]
            edge = dict(id=f'chain:{identity}:{direction}', source=first['source'],
                        target=second['target'], coordinates=coordinates,
                        length_m=round(first['length_m'] + second['length_m'], 3),
                        speed_kph=first['speed_kph'], road_class=first['road_class'])
            merged.append((edge, original_ids))
        for edge_id in list(edge_ids):
            edge = edges.pop(edge_id)
            incident[edge['source']].discard(edge_id)
            incident[edge['target']].discard(edge_id)
            del lineage[edge_id]
        for edge, original_ids in merged:
            if edge['id'] in edges:
                raise ValueError('Compaction edge ID collision')
            edges[edge['id']] = edge
            lineage[edge['id']] = original_ids
            incident[edge['source']].add(edge['id'])
            incident[edge['target']].add(edge['id'])
        removed_nodes.add(node_id)
        for neighbor in sorted(neighbors):
            heapq.heappush(pending, neighbor)
    before_vertices = sum(len(edge['coordinates']) for edge in edges.values())
    simplified = {}
    for edge in edges.values():
        coordinates = tuple(map(tuple, edge['coordinates']))
        key = min(coordinates, tuple(reversed(coordinates)))
        if key not in simplified:
            simplified[key] = simplify_line(list(map(list, key)))
        edge['coordinates'] = simplified[key] if coordinates == key else list(reversed(simplified[key]))
        edge['length_m'] = round(sum(math.dist(first, second) for first, second in
                                    zip(edge['coordinates'], edge['coordinates'][1:])), 3)
    network['nodes'] = [node for node in network['nodes'] if node['id'] not in removed_nodes]
    network['edges'] = sorted(edges.values(), key=lambda edge: edge['id'])
    return dict(contracted_degree_two_nodes=len(removed_nodes),
                vertices_before_simplification=before_vertices,
                vertices_after_simplification=sum(len(edge['coordinates']) for edge in edges.values()))


def retain_main_component(network):
    neighbors = defaultdict(set)
    for edge in network['edges']:
        neighbors[edge['source']].add(edge['target'])
        neighbors[edge['target']].add(edge['source'])
    remaining, components = set(neighbors), []
    while remaining:
        seed = min(remaining)
        component, pending = {seed}, [seed]
        remaining.remove(seed)
        while pending:
            node_id = pending.pop()
            for neighbor in neighbors[node_id] & remaining:
                remaining.remove(neighbor)
                component.add(neighbor)
                pending.append(neighbor)
        components.append(component)
    components.sort(key=lambda component: (-len(component), min(component)))
    if not components:
        raise ValueError('No usable road component')
    keep = components[0]
    excluded_edges = sum(edge['source'] not in keep for edge in network['edges'])
    network['nodes'] = [node for node in network['nodes'] if node['id'] in keep]
    network['edges'] = [edge for edge in network['edges'] if edge['source'] in keep]
    return dict(source_weak_components=len(components), excluded_disconnected_components=len(components) - 1,
                excluded_disconnected_nodes=sum(len(component) for component in components[1:]),
                excluded_disconnected_directed_edges=excluded_edges)


def build_network(snapshot, connected_only=False):
    from pyproj import Transformer, network

    network.set_network_enabled(False)
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:25830', always_xy=True)
    bbox = snapshot['bbox_wgs84']
    elements = snapshot['elements']
    nodes = {element['id']: element for element in elements if element['type'] == 'node'}
    blocked_nodes = set()
    for node_id, node in nodes.items():
        tags = node.get('tags', {})
        barrier = tags.get('barrier')
        if access_reason(tags) or (barrier not in {None, 'no', 'cattle_grid'} and
                                  (barrier not in {'gate', 'lift_gate', 'swing_gate'} or
                                   effective_access(tags) not in ALLOWED_ACCESS)):
            blocked_nodes.add(node_id)
    excluded = Counter()
    accepted = []
    for way in sorted((element for element in elements if element['type'] == 'way'),
                      key=lambda element: element['id']):
        reason = way_reason(way['tags'])
        if reason:
            excluded[reason] += 1
        else:
            if any(node_id not in nodes for node_id in way['nodes']):
                raise ValueError('Incomplete source way geometry; cannot invent missing nodes')
            accepted.append(way)
    counts = Counter(node_id for way in accepted for node_id in set(way['nodes']))
    graph_nodes, edges = {}, []
    blocked_segments = 0
    for way in accepted:
        runs, run = [], []
        way_id = way['id']
        for index, (start_id, end_id) in enumerate(zip(way['nodes'], way['nodes'][1:])):
            if start_id in blocked_nodes or end_id in blocked_nodes:
                clipped = None
                blocked_segments += 1
            else:
                start = [nodes[start_id]['lon'], nodes[start_id]['lat']]
                end = [nodes[end_id]['lon'], nodes[end_id]['lat']]
                clipped = clip_segment(start, end, bbox)
            if clipped is None:
                if len(run) > 1:
                    runs.append(run)
                run = []
                continue
            lower, upper = clipped
            start_token = f'osm:n{start_id}' if lower == 0 else f'clip:w{way_id}:{index}:in'
            end_token = f'osm:n{end_id}' if upper == 1 else f'clip:w{way_id}:{index}:out'
            start_position = projected_position(transformer, [start[axis] + lower * (end[axis] - start[axis]) for axis in (0, 1)])
            end_position = projected_position(transformer, [start[axis] + upper * (end[axis] - start[axis]) for axis in (0, 1)])
            if run and run[-1][0] != start_token:
                if len(run) > 1:
                    runs.append(run)
                run = []
            if not run:
                run.append((start_token, start_position, start_id if lower == 0 else None))
            run.append((end_token, end_position, end_id if upper == 1 else None))
        if len(run) > 1:
            runs.append(run)
        part_index = 0
        for run in runs:
            cuts = {0, len(run) - 1}
            cuts.update(index for index, (_, _, node_id) in enumerate(run)
                        if node_id is not None and counts[node_id] > 1)
            occurrences = Counter(token for token, _, _ in run)
            cuts.update(index for index, (token, _, _) in enumerate(run) if occurrences[token] > 1)
            cell_vertices = {}
            for index, (_, position, node_id) in enumerate(run):
                cell = nearby_cell_centre(position) if node_id is not None else None
                if cell:
                    row, col, distance = cell
                    cell_vertices[(row, col)] = min(cell_vertices.get((row, col), (math.inf, index)),
                                                    (distance, index))
            cuts.update(index for _, index in cell_vertices.values())
            if run[0][0] == run[-1][0] and len(cuts) == 2:
                cuts.add(len(run) // 2)
            ordered_cuts = sorted(cuts)
            for begin, finish in zip(ordered_cuts, ordered_cuts[1:]):
                points = [point for _, point, _ in run[begin:finish + 1]]
                coordinates = [points[0]]
                coordinates.extend(point for previous, point in zip(points, points[1:]) if previous != point)
                length = round(sum(math.dist(first, second) for first, second in zip(coordinates, coordinates[1:])), 3)
                if length <= 0:
                    continue
                source, target = run[begin][0], run[finish][0]
                for token, position in ((source, coordinates[0]), (target, coordinates[-1])):
                    if token in graph_nodes and graph_nodes[token] != position:
                        raise ValueError('Conflicting source-node positions')
                    graph_nodes[token] = position
                for direction in directions(way['tags']):
                    reverse = direction == 'r'
                    edges.append(dict(id=f'osm:w{way_id}:{part_index:04d}:{direction}',
                        source=target if reverse else source, target=source if reverse else target,
                        coordinates=list(reversed(coordinates)) if reverse else coordinates,
                        length_m=length, speed_kph=SPEEDS[way['tags']['highway']],
                        road_class=way['tags']['highway']))
                part_index += 1
    result = dict(version=VERSION, crs='EPSG:25830',
                  nodes=[dict(id=node_id, position=position) for node_id, position in sorted(graph_nodes.items())],
                  edges=sorted(edges, key=lambda edge: edge['id']), source=SOURCE)
    compaction = compact_network(result)
    component_filter = retain_main_component(result) if connected_only else {}
    result['sha256'] = network_digest(result)
    stats = dict(source_highway_ways=sum(element['type'] == 'way' for element in elements),
                 accepted_ways_before_clipping=len(accepted), excluded_ways=dict(sorted(excluded.items())),
                 blocked_source_nodes=len(blocked_nodes), blocked_segments=blocked_segments,
                 nodes=len(result['nodes']), directed_edges=len(result['edges']), compaction=compaction,
                 component_filter=component_filter,
                 road_class_arc_counts=dict(sorted(Counter(edge['road_class'] for edge in result['edges']).items())))
    return result, stats


def shortest_distances(network, start):
    adjacency = defaultdict(list)
    for edge in network['edges']:
        adjacency[edge['source']].append((edge['target'], edge['length_m']))
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        distance, node_id = heapq.heappop(queue)
        if distance != distances[node_id]:
            continue
        for target, length in adjacency[node_id]:
            candidate = distance + length
            if candidate < distances.get(target, math.inf):
                distances[target] = candidate
                heapq.heappush(queue, (candidate, target))
    return distances


def nearest_on_line(point, coordinates):
    best = (math.inf, None, None)
    offset = 0.0
    for start, end in zip(coordinates, coordinates[1:]):
        delta = [end[axis] - start[axis] for axis in (0, 1)]
        squared = sum(value * value for value in delta)
        fraction = max(0.0, min(1.0, sum((point[axis] - start[axis]) * delta[axis] for axis in (0, 1)) / squared)) if squared else 0
        position = [start[axis] + fraction * delta[axis] for axis in (0, 1)]
        distance = math.dist(point, position)
        if distance < best[0]:
            best = (distance, position, offset + fraction * math.sqrt(squared))
        offset += math.sqrt(squared)
    return best


def coverage_report(network, snapshot):
    from pyproj import Transformer

    inverse = Transformer.from_crs('EPSG:25830', 'EPSG:4326', always_xy=True)
    positions = {node['id']: node['position'] for node in network['nodes']}
    incident_classes = defaultdict(set)
    undirected = defaultdict(set)
    for edge in network['edges']:
        undirected[edge['source']].add(edge['target'])
        undirected[edge['target']].add(edge['source'])
        incident_classes[edge['source']].add(edge['road_class'])
        incident_classes[edge['target']].add(edge['road_class'])
    remaining, components = set(positions), []
    while remaining:
        seed = min(remaining)
        component, pending = {seed}, [seed]
        remaining.remove(seed)
        while pending:
            node_id = pending.pop()
            for neighbor in sorted(undirected[node_id] & remaining):
                remaining.remove(neighbor)
                component.add(neighbor)
                pending.append(neighbor)
        components.append(component)
    components.sort(key=lambda component: (-len(component), min(component)))
    main = components[0] if components else set()
    component_sizes = {node_id: len(component) for component in components for node_id in component}
    staging = []
    for name, projected in STAGING_ANCHORS:
        candidates = [node_id for node_id in main if node_id.startswith('osm:n') and
                      incident_classes[node_id] & {'residential', 'service', 'living_street', 'unclassified', 'tertiary'} and
                      not incident_classes[node_id] & {'motorway', 'motorway_link', 'trunk', 'trunk_link'}]
        if not candidates:
            continue
        node_id = min(candidates, key=lambda candidate: (math.dist(projected, positions[candidate]), candidate))
        nearest_any = min(positions, key=lambda candidate: (math.dist(projected, positions[candidate]), candidate))
        distances = shortest_distances(network, node_id)
        staging.append(dict(name=name, node_id=node_id, position=positions[node_id],
                            search_anchor_epsg25830=projected,
                            lonlat=[round(value, 7) for value in inverse.transform(*positions[node_id])],
                            directed_reachable_nodes=len(distances),
                            weak_component_nodes=component_sizes[node_id],
                            offset_from_search_anchor_m=round(math.dist(projected, positions[node_id]), 3),
                            nearest_any_node=dict(node_id=nearest_any,
                                position=positions[nearest_any],
                                offset_m=round(math.dist(projected, positions[nearest_any]), 3),
                                weak_component_nodes=component_sizes[nearest_any])))
    target_reports = []
    for point in TARGETS:
        nearest = min(((*nearest_on_line(point, edge['coordinates']), edge['id'], edge)
                       for edge in network['edges']), key=lambda item: (item[0], item[3]))
        accessible = []
        for candidate in staging:
            distances = shortest_distances(network, candidate['node_id'])
            reachable = min(((*nearest_on_line(point, edge['coordinates']), edge['id'], edge)
                             for edge in network['edges'] if edge['source'] in distances),
                            key=lambda item: (item[0], item[3]))
            distance, position, offset, edge_id, edge = reachable
            accessible.append(dict(staging_node_id=candidate['node_id'], edge_id=edge_id,
                road_class=edge['road_class'], nearest_road_position=[round(value, 2) for value in position],
                off_network_distance_m=round(distance, 3),
                directed_road_distance_m=round(distances[edge['source']] + offset, 3)))
        reference = shortest_distances(network, staging[0]['node_id']) if staging else {}
        nearest_node = min(reference, key=lambda candidate: (math.dist(point, positions[candidate]), candidate)) if reference else None
        target_reports.append(dict(position=point, nearest_any_road_distance_m=round(nearest[0], 3),
            nearest_any_road_edge_id=nearest[3], access_from_staging=accessible,
            nearest_reachable_node=dict(node_id=nearest_node, position=positions[nearest_node],
                gap_m=round(math.dist(point, positions[nearest_node]), 3),
                directed_road_distance_m=round(reference[nearest_node], 3)) if nearest_node else None))
    eligible = []
    reference_distances = shortest_distances(network, staging[0]['node_id']) if staging else {}
    for row in range(1, ACTIVE_GRID['rows'] - 1):
        for col in range(1, ACTIVE_GRID['cols'] - 1):
            point = [ACTIVE_GRID['west'] + (col + 0.5) * ACTIVE_GRID['cell_size_m'],
                     ACTIVE_GRID['north'] - (row + 0.5) * ACTIVE_GRID['cell_size_m']]
            if not reference_distances:
                continue
            node_id = min(reference_distances, key=lambda candidate:
                          (math.dist(point, positions[candidate]), candidate))
            gap = math.dist(point, positions[node_id])
            if gap <= 150:
                eligible.append(dict(row=row, col=col, position=point, node_id=node_id,
                    node_position=positions[node_id], node_gap_m=round(gap, 3),
                    directed_road_distance_m=round(reference_distances[node_id], 3)))
    eastings = [point[0] for edge in network['edges'] for point in edge['coordinates']]
    northings = [point[1] for edge in network['edges'] for point in edge['coordinates']]
    return dict(bounds_epsg25830=[min(eastings), min(northings), max(eastings), max(northings)],
                weak_components=len(components), largest_weak_component_nodes=len(main),
                weak_component_size_histogram=dict(sorted(Counter(len(component) for component in components).items())),
                staging_candidates=staging, target_access=target_reports,
                interior_cell_centres_with_reachable_node_within_150m=eligible,
                interior_eligible_count=len(eligible),
                status='Partial conservative exercise coverage; each target reports its actual connected-road gap. No guaranteed 150 m suppression access or 1500 m walking access to arbitrary targets.',
                interpretation='Distances use nearest source-connected directed road geometry, then shortest road length to its projection. Off-network distance is Euclidean, not a traversable final route. Candidate coastal street nodes are exercise anchors, not verified fire stations, safe parking or approved staging grounds.')


def prepare(output=DATA):
    import pyproj

    acquisition = json.loads((DATA / 'acquisition.json').read_bytes())
    snapshot_bytes = (DATA / acquisition['snapshot']['path']).read_bytes()
    if digest(snapshot_bytes) != acquisition['snapshot']['sha256']:
        raise ValueError('Pinned source snapshot SHA256 mismatch')
    snapshot = json.loads(gzip.decompress(snapshot_bytes))
    if digest(canonical(snapshot)) != acquisition['snapshot']['canonical_sha256']:
        raise ValueError('Canonical source snapshot SHA256 mismatch')
    network, statistics = build_network(snapshot, connected_only=True)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'network.json', network, compact=True)
    provenance = dict(version='coastal-road-provenance-v1', source=SOURCE,
        license_url='https://opendatacommons.org/licenses/odbl/1-0/', acquisition=acquisition,
        preparation=dict(script='eval/prepare_coastal_roads.py', script_sha256=digest(Path(__file__).read_bytes()),
                         pyproj_version=pyproj.__version__, proj_version=pyproj.proj_version_str,
                         crs='EPSG:25830', input_crs='EPSG:4326', always_xy=True,
                         proj_network_enabled=False, coordinate_rounding_m=0.1,
                         length_rounding_m=0.001,
                         geometry_simplification='Douglas-Peucker in projected metres, maximum 2 m deviation, retaining graph endpoints; same-class/speed unbranched degree-two chains contracted without changing directed reachability. Retain the nearest original source vertex per source-way run and interior grid cell when within 150 m; protect such access nodes during contraction. Length recomputed on final geometry.',
                         clip_bbox_wgs84=snapshot['bbox_wgs84'], active_grid=ACTIVE_GRID,
                         canonical_json='UTF-8, default ensure_ascii=true, sort_keys=true, separators=(comma,colon), allow_nan=false; SHA256 excludes only top-level sha256; file has a trailing LF'),
        filtering=dict(accepted_classes=sorted(SPEEDS), speed_kph_by_class=SPEEDS,
            speeds='Synthetic conservative class defaults for exercises; not observed/historical speed, legal maximum, or calibrated fire-engine travel time.',
            access='Most specific motorcar > motor_vehicle > vehicle > access; only absent/yes/permissive/designated/official allowed. Explicit HGV restrictions, conditional/directional access and vehicle dimension limits excluded.',
            tracks='Only grade1/grade2 or explicit paved/solid gravel/compacted surface, excluding grade3-5 and explicit soft/rough surfaces. Unknown ungraded tracks excluded.',
            barriers='Segments incident to restricted nodes, fords or barriers excluded. Cattle grids allowed; gates require explicit motor access permission. No assumed emergency override.',
            turn_restrictions='Turn restrictions are retained in the source snapshot but not encoded in this edge-only exercise graph. One-way directions and access filtering are enforced. Routes require independent legal/operational validation.',
            topology='Connect only identical OSM node IDs, split at shared nodes/repeats/endpoints/bbox intersections, then contract unbranched same-class/speed chains. Retain the largest weak component, which contains the coastal exercise anchors, and exclude disconnected islands without invented bridges. No geometric crossing joins, nearest-neighbor bridges or random sampling. Boundary nodes are specific to their source way and segment.',
            service='Driveways, parking aisles and drive-through service roads excluded.',
            oneway='Explicit most-specific motorcar/motor_vehicle/vehicle/general oneway; -1 reversed; implicit motorway and roundabout forward; unknown/reversible/alternating excluded. Bidirectional arcs have explicit reversed coordinates.'),
        statistics=statistics, coverage=coverage_report(network, snapshot),
        outputs=dict(network=dict(path='network.json', bytes=(output / 'network.json').stat().st_size,
                                  sha256=network['sha256'], file_sha256=digest((output / 'network.json').read_bytes()))),
        limitations=[
            'Current OSM source, not IGN/CNIG or a historical incident road inventory; community coverage may be incomplete or incorrect.',
            'Road class, surface and access tags do not certify fire-truck width, bearing capacity, gradients, turning space, bridge capacity or current legal/emergency access.',
            'No live closures, gates, fire/smoke, traffic or dispatch data; no external calls during episodes. Operational use requires field verification.',
            'Turn-restriction relations cannot be represented by the current graph contract and are not enforced; a graph route is not a legally validated road itinerary.',
            'Conservative exclusions can fragment or remove real approaches. Disconnected components are omitted from the engine graph but retained in the minimized source snapshot. Weak connectivity does not guarantee directed reachability.',
            'No fabricated geometry or off-network target connectors. A Euclidean road-access gap is not a drivable route.',
            'Clipping can remove routes that leave and re-enter the bbox; source topology/grade separation is trusted without geometric repair.',
            'Multi-tile API acquisition is not an atomic OSM snapshot. Raw tile hashes and acquisition windows are retained; the included minimized snapshot pins exact offline regeneration.'])
    write_json(output / 'provenance.json', provenance)
    print(json.dumps(dict(sha256=network['sha256'], statistics=statistics,
                          bounds=provenance['coverage']['bounds_epsg25830'],
                          staging=provenance['coverage']['staging_candidates'],
                          targets=provenance['coverage']['target_access']), indent=2))
    return network, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquire', action='store_true', help='Explicit network acquisition; raw replies stay in ignored .runtime')
    parser.add_argument('--snapshot-from-cache', action='store_true', help='Recreate the minimized source snapshot from existing raw cache without network')
    parser.add_argument('--output', type=Path, default=DATA, help='Prepared graph/provenance destination (default: pinned fixture directory)')
    args = parser.parse_args()
    if args.acquire or args.snapshot_from_cache:
        acquire(cache_only=args.snapshot_from_cache)
    prepare(args.output)


if __name__ == '__main__':
    main()
