"""Opt-in exercise assumptions on the separately pinned source-only road graph.

Empty modes mean unknown or excluded, never a claim that a physical road is absent.
RoadNetwork.path ignores modes: use a mode-aware router for exercise journeys.
"""
import argparse
from collections import Counter
import copy
import heapq
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from eval.expanded_road_runtime_v1 import (CATALOG_PIN, MAX_NETWORK_BYTES, convert,
    load_candidate, strict_json, validate as validate_source_graph, write_private_bytes, ensure_new_output)
from eval.prepare_coastal_roads import ALLOWED_ACCESS, directions, effective_access, way_reason
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork

VERSION = 'expanded-road-exercise-policy-v3'
BASE_PIN = '80b629ccb398e98ff5c65e866a7f6e397cce68bd53523f5357a21caaa7c499d9'
BASE_PATH = Path('/tmp/expanded-road-runtime-v3/network.json')
OUTPUT_DIR = Path('/tmp/expanded-road-exercise-v3')
PUBLIC_CLASSES = frozenset({'motorway','motorway_link','trunk','trunk_link','primary',
    'primary_link','secondary','secondary_link','tertiary','tertiary_link',
    'residential','unclassified','living_street'})
ACCESS_KEYS = ('access','vehicle','motor_vehicle','motorcar','hgv')
ASSUMPTIONS = {
    'scope': 'opt-in synthetic exercise; not legal, current or historical access',
    'ordinary_public_roads': 'assume normal motor use absent contrary or ambiguous tags',
    'modes': 'crew and engine mean ground vehicles; vehicle suitability is assumed, not established',
    'explicit_tags': 'a positive source tag is evidence of a tag, not a verified permission',
    'unknown': 'no exercise mode or cell accessibility is established',
    'speeds': 'class assumptions from prepare_coastal_roads.py',
} 


def restriction_sets(source):
    ways, nodes = set(), set()
    for relation in source.get('restriction_relations', []):
        for member in relation['members']:
            if member['type'] == 'way':
                ways.add(member['ref'])
            elif member['type'] == 'node' and member.get('role') == 'via':
                nodes.add(member['ref'])
    return ways, nodes


def restrictive_tags(tags):
    # Do not rely on motorcar override of a private/no general access tag.
    return (any(tags.get(k) not in ALLOWED_ACCESS | {None} for k in ACCESS_KEYS)
            or any('conditional' in key for key in tags))


def decide(way, node_records, restricted_ways, restricted_nodes, direction):
    tags = way.get('tags', {})
    if way['id'] in restricted_ways or any(n['id'] in restricted_nodes for n in node_records):
        return [], 'excluded_restriction'
    if (way_reason(tags) or restrictive_tags(tags) or
            direction not in directions(tags)):
        return [], 'excluded_source_policy'
    if 'width' in tags or 'incline' in tags:
        return [], 'unknown_vehicle_dimensions_or_grade'
    if tags.get('surface') in {'sand','mud','grass','ground','earth','dirt','rock','rocks','unpaved'}:
        return [], 'unknown_surface'
    for node in node_records:
        ntags = node.get('tags', {})
        if restrictive_tags(ntags) or way_reason_node(ntags) or 'width' in ntags or 'incline' in ntags:
            return [], 'unknown_node_access'
        if ntags.get('barrier') not in (None, 'no'):
            return [], 'unknown_barrier'
    road_class = tags['highway']
    explicit = effective_access(tags) in ALLOWED_ACCESS
    if road_class == 'track':
        # A general access=yes tag alone says too little about motor traffic.
        motor = any(tags.get(k) in ALLOWED_ACCESS for k in ('vehicle','motor_vehicle','motorcar'))
        if not motor:
            return [], 'unknown_track_access'
        modes = ['crew']
        if tags.get('hgv') in ALLOWED_ACCESS:
            modes.append('engine')
        return modes, 'explicit_motor_tags_track'
    if road_class == 'service':
        if not explicit:
            return [], 'unknown_service_access'
        return ['crew','engine'], 'explicit_access_tag_service'
    if road_class not in PUBLIC_CLASSES:
        return [], 'unknown_road_class'
    return ['crew','engine'], ('explicit_access_tag_public_road' if explicit else 'assumed_public_road')


def way_reason_node(tags):
    # Apply the same node restrictions as the source builder, with stricter
    # handling of every tagged barrier below.
    from eval.prepare_coastal_roads import access_reason
    return access_reason(tags)


def evaluate_edge(edge, ways, nodes, restricted_ways, restricted_nodes):
    wid, first, last, direction = (edge[k] for k in
        ('source_way','first_segment','last_segment','direction'))
    way = ways[wid]
    step = 1 if direction == 'f' else -1
    segments = range(first, last + step, step)
    ids = set()
    for index in segments:
        ids.update(way['nodes'][index:index+2])
    return decide(way, [nodes[n] for n in ids], restricted_ways, restricted_nodes, direction)


def make_exercise(base, source, *, opt_in=False):
    if opt_in is not True:
        raise ValueError('exercise assumptions require explicit opt-in')
    validate_source_graph(base, source)
    ways = {w['id']: w for w in source['ways']}
    nodes = {n['id']: n for n in source['nodes']}
    restricted_ways, restricted_nodes = restriction_sets(source)
    graph = copy.deepcopy(base)
    graph['status'] = 'opt-in-exercise-assumptions-not-verified-access'
    graph['exercise_policy_version'] = VERSION
    graph['source_graph_sha256'] = base['sha256']
    graph['exercise_assumptions'] = dict(ASSUMPTIONS)
    for edge in graph['edges']:
        modes, reason = evaluate_edge(edge, ways, nodes, restricted_ways, restricted_nodes)
        edge['allowed_modes'] = modes
        edge['exercise_access_basis'] = reason
    graph['sha256'] = digest({k:v for k,v in graph.items() if k != 'sha256'})
    return graph


def validate_exercise(graph, base, source):
    validate_source_graph(base, source)
    if (graph.get('exercise_policy_version') != VERSION or
            graph.get('status') != 'opt-in-exercise-assumptions-not-verified-access' or
            graph.get('source_graph_sha256') != base['sha256'] or
            graph.get('exercise_assumptions') != ASSUMPTIONS):
        raise ValueError('exercise provenance mismatch')
    if (len(graph['edges']) != len(base['edges']) or graph['nodes'] != base['nodes']):
        raise ValueError('exercise topology mismatch')
    ways = {w['id']: w for w in source['ways']}
    nodes = {n['id']: n for n in source['nodes']}
    rw, rn = restriction_sets(source)
    counts = Counter()
    for edge, original in zip(graph['edges'], base['edges']):
        unmodified = {k:v for k,v in edge.items() if k != 'exercise_access_basis'}
        unmodified['allowed_modes'] = []
        if unmodified != original:
            raise ValueError('exercise edge differs from source topology')
        modes, reason = evaluate_edge(original, ways, nodes, rw, rn)
        if edge['allowed_modes'] != modes or edge.get('exercise_access_basis') != reason:
            raise ValueError('exercise mode policy mismatch')
        counts[reason] += 1
    network = RoadNetwork(graph, graph['sha256'])
    return dict(nodes=len(network.nodes), edges=len(network.edges), basis_counts=dict(sorted(counts.items())),
                crew_edges=sum('crew' in e['allowed_modes'] for e in graph['edges']),
                engine_edges=sum('engine' in e['allowed_modes'] for e in graph['edges']))


def mode_path(network, origin, destination, mode):
    """Shortest topology path respecting modes; never snaps or invents connectors."""
    if mode not in ('crew','engine') or origin not in network.nodes or destination not in network.nodes:
        raise ValueError('invalid mode or endpoint')
    heap, costs, parents = [(0.,origin)], {origin:0.}, {}
    while heap:
        cost, node = heapq.heappop(heap)
        if cost != costs[node]:
            continue
        if node == destination:
            path=[]
            while node != origin:
                edge = parents[node]; path.append(edge); node=network.edges[edge]['source']
            return list(reversed(path))
        for eid in network.adjacency[node]:
            edge = network.edges[eid]
            if mode not in edge['allowed_modes']:
                continue
            nxt=edge['target']; new=cost+edge['length_m']/edge['speed_kph']
            if new < costs.get(nxt, math.inf):
                costs[nxt]=new; parents[nxt]=eid; heapq.heappush(heap,(new,nxt))
    raise ValueError('no permitted exercise path')


def assess_posts(graph, catalog):
    if (catalog.get('version') != 'wildfire-incident-eval-catalog-v1' or
        catalog.get('sha256') != CATALOG_PIN or
        digest({k:v for k,v in catalog.items() if k != 'sha256'}) != CATALOG_PIN):
        raise ValueError('original catalog pin mismatch')
    posts=set()
    for case in catalog['cases']:
        if case.get('sha256') != digest({k:v for k,v in case.items() if k != 'sha256'}):
            raise ValueError('case pin mismatch')
        posts.update(tuple(u['base_position']) for u in case['resources'] if u['kind'] in ('crew','engine'))
    posts=sorted(posts)
    if len(posts) != 2:
        raise ValueError('unexpected ground post count')
    network=RoadNetwork(graph,graph['sha256'])
    # Only use the router's geometric origin matching here; the synthetic
    # raster is not evidence of accessible cells and no mission is admitted.
    from envs.wildfire_response.incident_day.routing import HazardRouter
    grid = {'width':84, 'height':59, 'crs':'EPSG:25830',
            'transform':[500,0,290000,0,-500,4064500]}
    cells = [[1]*84 for _ in range(59)]
    router = HazardRouter(network, {'grid':grid, 'fuel_classes':cells,
                          'fuel_valid_mask':cells}, cells, walk_mask=cells)
    result={}
    for mode in ('crew','engine'):
        endpoints = {e[k] for e in graph['edges'] if mode in e['allowed_modes'] for k in ('source','target')}
        nearest=[min(endpoints,key=lambda n:(math.dist(p,network.nodes[n]),n)) for p in posts]
        gaps=[math.dist(p,network.nodes[n]) for p,n in zip(posts,nearest)]
        paths=[]
        for origin,target in ((0,1),(1,0)):
            try:
                ids=mode_path(network,nearest[origin],nearest[target],mode)
                paths.append(dict(available=True, edges=len(ids), distance_m=round(sum(network.edges[i]['length_m'] for i in ids),2)))
            except ValueError:
                paths.append(dict(available=False))
        result[mode]=dict(nearest_node_gaps_m=[round(v,3) for v in gaps],
            exact_post_node=[v <= 1e-6 for v in gaps],
            actual_router_origin_matches=[len(router._origins(p, mode)) for p in posts],
            directed_nearest_node_paths=paths)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in', action='store_true', help='authorize exercise assumptions')
    parser.add_argument('--output',type=Path,default=OUTPUT_DIR/'network.json')
    parser.add_argument('--catalog',type=Path,help='pinned private scenario catalog')
    args=parser.parse_args()
    if not args.opt_in:
        parser.error('--opt-in is required')
    if not args.output.is_absolute() or not args.output.resolve().is_relative_to(OUTPUT_DIR):
        parser.error('output must be in /tmp/expanded-road-exercise-v3')
    ensure_new_output(args.output)
    source=load_candidate()
    base=strict_json(BASE_PATH.read_bytes())
    if base.get('sha256') != BASE_PIN:
        raise ValueError('source-only network pin mismatch')
    graph=make_exercise(base,source,opt_in=True)
    stats=validate_exercise(graph,base,source)
    raw=json.dumps(graph,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
    if len(raw) > MAX_NETWORK_BYTES:
        raise ValueError('network exceeds serialized size limit')
    if args.catalog:
        stats['posts']=assess_posts(graph,strict_json(args.catalog.read_bytes()))
    write_private_bytes(args.output,raw)
    print(json.dumps(dict(graph_sha256=graph['sha256'],bytes=len(raw),**stats),sort_keys=True))


if __name__ == '__main__':
    main()
