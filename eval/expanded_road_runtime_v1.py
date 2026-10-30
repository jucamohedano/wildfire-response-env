"""Private, topology-preserving conversion of the pinned expanded road candidate.

This graph is evidence, not a cell-access classification or an operational pack.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
import os
import secrets
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
sys.path.insert(0, str(ROOT))
from wildfire_core.mobility_routing import RoadNetwork, NETWORK_VERSION, MAX_NETWORK_BYTES
from envs.wildfire_response.incident_day.routing import ORIGIN_TOLERANCE_M
from envs.wildfire_response.regional.ops.generator import digest
from eval.extended_access_v1 import BOUNDS, strict_json, make_private_directories
from eval.prepare_coastal_roads import SPEEDS

PIN = '976875d58d2d134ba1f5d2aa62030f23b567f56d0bf98dd9820f88a2cb3ae343'
SOURCE_ID = 'd331a7fd511ba9ebbd767a85d78dccb7c7e68eaef6a4d4294ab183949e6586e9'
CATALOG_PIN = '2b264d9668f0585a560d82af716f794ed386d6a7cdc713b24987269d647b33fc'
CANDIDATE = Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')
CONVERSION_VERSION = 'expanded-road-runtime-v3'


def load_candidate(path=CANDIDATE):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise ValueError('Candidate compressed SHA256 pin mismatch')
    source = strict_json(gzip.decompress(raw))
    if (source.get('version'), source.get('status'), source.get('crs'), source.get('bounds'),
        source.get('source_manifest_sha256')) != ('extended-access-directed-road-candidate-v2',
        'source_candidate_not_operational_access', 'EPSG:25830', list(BOUNDS), SOURCE_ID):
        raise ValueError('Candidate contract mismatch')
    return source


def indexes(source):
    nodes = {n['id']: n['position_m'] for n in source['nodes']}
    ways = {w['id']: w for w in source['ways']}
    if len(nodes) != len(source['nodes']) or len(ways) != len(source['ways']):
        raise ValueError('duplicate source identity')
    arcs, incident = {}, defaultdict(set)
    for arc in source['arcs']:
        way, seg, direction = arc['way'], arc['segment'], arc['direction']
        key = (way, seg, direction)
        if key in arcs or way not in ways or direction not in ('f', 'r'):
            raise ValueError('invalid or duplicate source arc')
        ns = ways[way]['nodes']
        if type(seg) is not int or not 0 <= seg < len(ns)-1:
            raise ValueError('invalid source segment')
        a, b = ns[seg:seg+2]
        if (arc['source'], arc['target']) != ((a, b) if direction == 'f' else (b, a)) or a not in nodes or b not in nodes or a == b:
            raise ValueError('arc does not follow source way')
        if any(not (BOUNDS[0] <= p[0] <= BOUNDS[2] and BOUNDS[1] <= p[1] <= BOUNDS[3]) for p in (nodes[a], nodes[b])):
            raise ValueError('out-of-domain source node')
        arcs[key] = (arc['source'], arc['target'])
        incident[a].add((way, seg)); incident[b].add((way, seg))
    return nodes, ways, arcs, incident


def unrelated_segment_nodes(nodes, arcs, candidates):
    """Find source nodes close enough to attach to a segment with other IDs."""
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    segments = {}
    for (way, segment, _), (a, b) in arcs.items():
        segments[way, segment] = (a, b)
    keys = sorted(segments)
    geometries = [LineString([nodes[segments[key][0]], nodes[segments[key][1]]]) for key in keys]
    tree = STRtree(geometries)
    candidates = sorted(candidates)
    ambiguous = set()
    radius = ORIGIN_TOLERANCE_M * 1.01 + 1e-9
    for start in range(0, len(candidates), 4096):
        batch = candidates[start:start + 4096]
        matches = tree.query([Point(nodes[n]) for n in batch], predicate='dwithin', distance=radius)
        for point_index, segment_index in zip(*matches):
            node = batch[int(point_index)]
            if node not in segments[keys[int(segment_index)]]:
                ambiguous.add(node)
    return ambiguous


def crossing_nodes(nodes, ways, arcs, incident):
    # Protect otherwise hidden vertices first; a second check rejects the
    # resulting ambiguity because router origins do not carry edge identity.
    candidates = [n for n, touching in incident.items() if len(touching) == 2 and
                  len({w for w, _ in touching}) == 1 and
                  ways[next(iter(touching))[0]]['nodes'][0] != ways[next(iter(touching))[0]]['nodes'][-1]]
    return unrelated_segment_nodes(nodes, arcs, candidates)


def reject_ambiguous_retained(nodes, arcs, retained):
    # Conservative: inspect every source arc, including currently disabled
    # modes, so a later exercise policy cannot introduce an ambiguous origin.
    if unrelated_segment_nodes(nodes, arcs, retained):
        raise ValueError('retained node near unrelated source segment; ambiguous origin')


def ensure_new_output(path):
    if os.path.lexists(path):
        raise FileExistsError(f'refusing to overwrite versioned output: {path}')


def write_private_bytes(path, data):
    """Publish a mode-0600 file atomically, never replacing any existing entry."""
    path = Path(path)
    make_private_directories(path.parent)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = f'.{path.name}.{secrets.token_hex(16)}.tmp'
    created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dir_fd)
        created = True
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
        # link fails atomically if the destination exists, including symlinks.
        os.link(temp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    finally:
        if created:
            os.unlink(temp, dir_fd=dir_fd)
        os.close(dir_fd)


def can_contract(node, way, incident, ways, protected):
    touches = incident[node]
    return (node not in protected and len(touches) == 2 and all(w == way for w, _ in touches)
            and ways[way]['nodes'][0] != ways[way]['nodes'][-1])


def convert(source):
    nodes, ways, arcs, incident = indexes(source)
    protected = crossing_nodes(nodes, ways, arcs, incident)
    grouped = defaultdict(list)
    for way, seg, direction in arcs:
        grouped[way, direction].append(seg)
    edges = []
    for (way, direction), segments in sorted(grouped.items()):
        ordered = sorted(segments, reverse=direction == 'r')
        chunks, current = [], []
        for seg in ordered:
            if current:
                prior = current[-1]
                mid = arcs[way, prior, direction][1]
                if (seg != prior + (1 if direction == 'f' else -1)
                        or mid != arcs[way, seg, direction][0]
                        or not can_contract(mid, way, incident, ways, protected)):
                    chunks.append(current); current = []
            current.append(seg)
        if current:
            chunks.append(current)
        road_class = ways[way]['tags']['highway']
        if road_class not in SPEEDS:
            raise ValueError('unsupported road class')
        for chunk in chunks:
            ids = [arcs[way, chunk[0], direction][0]] + [arcs[way, s, direction][1] for s in chunk]
            coords = [nodes[n][:] for n in ids]
            length = sum(math.dist(a, b) for a, b in zip(coords, coords[1:]))
            if length <= 0:
                raise ValueError('zero length road')
            edges.append(dict(id=f'{way}:{chunk[0]}:{chunk[-1]}:{direction}',
                source=str(ids[0]), target=str(ids[-1]), coordinates=coords,
                length_m=length, speed_kph=SPEEDS[road_class], road_class=road_class,
                allowed_modes=[], source_way=way, first_segment=chunk[0], last_segment=chunk[-1], direction=direction))
    used = {int(e[k]) for e in edges for k in ('source', 'target')}
    reject_ambiguous_retained(nodes, arcs, used)
    graph = dict(version=NETWORK_VERSION, crs='EPSG:25830',
                 conversion_policy_version=CONVERSION_VERSION,
                 status='source-topology-only-no-operational-access', bounds=list(BOUNDS),
                 source_candidate_sha256=PIN, source_manifest_sha256=SOURCE_ID,
                 nodes=[dict(id=str(n), position=nodes[n][:]) for n in sorted(used)],
                 edges=sorted(edges, key=lambda e: e['id']))
    graph['sha256'] = digest(graph)
    return graph


def validate(graph, source):
    """Expand each graph edge independently; compare every directed source arc.

    Intermediate vertices may only be discarded when they cannot connect to a
    different segment or way. No simplification or synthetic geometry is used.
    """
    if graph.get('conversion_policy_version') != CONVERSION_VERSION:
        raise ValueError('conversion policy version mismatch')
    if (graph.get('status'), graph.get('bounds'), graph.get('source_candidate_sha256'),
        graph.get('source_manifest_sha256')) != ('source-topology-only-no-operational-access',
        list(BOUNDS), PIN, SOURCE_ID):
        raise ValueError('runtime source metadata mismatch')
    nodes, ways, arcs, incident = indexes(source)
    protected = crossing_nodes(nodes, ways, arcs, incident)
    expanded, used = {}, set()
    for edge in graph['edges']:
        w, first, last, direction = (edge[k] for k in ('source_way','first_segment','last_segment','direction'))
        if w not in ways or direction not in ('f','r') or type(first) is not int or type(last) is not int:
            raise ValueError('invalid edge lineage')
        step = 1 if direction == 'f' else -1
        if (last-first)*step < 0:
            raise ValueError('invalid lineage order')
        seq = list(range(first, last + step, step))
        try:
            pairs = [arcs[w, i, direction] for i in seq]
        except KeyError as exc:
            raise ValueError('edge uses absent directed source arc') from exc
        if any(a[1] != b[0] for a,b in zip(pairs,pairs[1:])):
            raise ValueError('disconnected chain')
        path = [pairs[0][0]] + [p[1] for p in pairs]
        if any(not can_contract(n, w, incident, ways, protected) for n in path[1:-1]):
            raise ValueError('chain hides a junction')
        cls = ways[w]['tags']['highway']
        if (edge['source'] != str(path[0]) or edge['target'] != str(path[-1]) or
            edge['coordinates'] != [nodes[n] for n in path] or edge['road_class'] != cls or
            edge['speed_kph'] != SPEEDS[cls] or edge.get('allowed_modes') != [] or edge['id'] != f'{w}:{first}:{last}:{direction}'):
            raise ValueError('edge geometry, identity or speed mismatch')
        length = sum(math.dist(nodes[a], nodes[b]) for a,b in pairs)
        if not math.isclose(edge['length_m'], length, rel_tol=1e-12, abs_tol=1e-8):
            raise ValueError('edge length mismatch')
        for i,pair in zip(seq,pairs):
            key = (w,i,direction)
            if key in expanded:
                raise ValueError('duplicate expanded arc')
            expanded[key] = pair
        used.update((path[0],path[-1]))
    if expanded != arcs:
        raise ValueError('source directed arcs lost or invented')
    if graph['nodes'] != [dict(id=str(n), position=nodes[n]) for n in sorted(used)]:
        raise ValueError('runtime node set mismatch')
    # No retained junction may be hidden within another chain.
    for edge in graph['edges']:
        way, first, last, direction = (edge[k] for k in ('source_way','first_segment','last_segment','direction'))
        step = 1 if direction == 'f' else -1
        interior = [arcs[way,i,direction][1] for i in range(first,last,step)]
        if any(n in used for n in interior):
            raise ValueError('runtime junction hidden in a chain')
    reject_ambiguous_retained(nodes, arcs, used)
    network = RoadNetwork(graph, graph['sha256'])
    return dict(nodes=len(graph['nodes']), edges=len(graph['edges']),
                source_nodes=len(nodes), source_arcs=len(arcs),
                coordinates=sum(len(e['coordinates']) for e in graph['edges']),
                protected_crossing_nodes=len(protected),
                largest_strong_component_nodes=len(network.main_component))


def post_distances(graph, catalog):
    if (catalog.get('version') != 'wildfire-incident-eval-catalog-v1' or
            catalog.get('sha256') != CATALOG_PIN or
            catalog.get('sha256') != digest({k:v for k,v in catalog.items() if k != 'sha256'})):
        raise ValueError('catalog pin mismatch')
    posts = {}
    for case in catalog['cases']:
        if case.get('sha256') != digest({k:v for k,v in case.items() if k != 'sha256'}):
            raise ValueError('scenario pin mismatch')
        for unit in case['resources']:
            if unit['kind'] in ('engine','crew'):
                p = tuple(unit['base_position'])
                posts[p] = posts.get(p, 0) + 1
    vertices = [n['position'] for n in graph['nodes']]
    distances = [min(math.dist(p,q) for q in vertices) for p in posts]
    return dict(unique_ground_posts=len(posts), ground_resource_occurrences=sum(posts.values()),
                nearest_runtime_node_m_min=round(min(distances),2),
                nearest_runtime_node_m_max=round(max(distances),2),
                posts_with_node_within_150m=sum(d <= 150 for d in distances),
                posts_with_node_over_150m=sum(d > 150 for d in distances))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='private /tmp JSON output')
    parser.add_argument('--catalog', type=Path, help='private original scenario catalog for aggregate proximity')
    args = parser.parse_args()
    if not args.output.is_absolute() or not args.output.resolve().is_relative_to(Path('/tmp')):
        parser.error('output must be under /tmp')
    ensure_new_output(args.output)
    source = load_candidate()
    graph = convert(source)
    stats = validate(graph, source)
    raw = json.dumps(graph, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_NETWORK_BYTES:
        raise ValueError('serialized network exceeds RoadNetwork limit')
    if args.catalog:
        stats['post_proximity'] = post_distances(graph, strict_json(args.catalog.read_bytes()))
    # Never print or write raw source data outside the private output.
    write_private_bytes(args.output, raw)
    print(json.dumps({'graph_sha256':graph['sha256'], 'bytes':len(raw), **stats}, sort_keys=True))


if __name__ == '__main__':
    main()
