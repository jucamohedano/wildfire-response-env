"""Private synthetic post re-anchoring and width-only counterfactual diagnostic."""
import argparse
import copy
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from eval.expanded_road_runtime_v1 import (CATALOG_PIN, MAX_NETWORK_BYTES, SOURCE_ID,
    load_candidate, strict_json, validate as validate_source_graph, write_private_bytes,
    indexes, reject_ambiguous_retained, ensure_new_output)
from eval.extended_access_v1 import SOURCE_SHA
from eval.expanded_road_exercise_v1 import (BASE_PATH, BASE_PIN, evaluate_edge, mode_path,
    restriction_sets, validate_exercise)
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork

EXERCISE_PIN = 'e092084313e3e056cf695310157ca43355fa65a7a73fdea21771636c2a557695'
EXERCISE_PATH = Path('/tmp/expanded-road-exercise-v3/network.json')
OLD_SOURCE_PATH = ROOT / 'data/coastal_mobility_v1/source_snapshot.json.gz'
CATALOG_PATH = Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json')
OUT = Path('/tmp/expanded-post-anchors-v4')
VERSION = 'expanded-synthetic-post-anchors-v4'
WIDTH_WAY = 47052550
PROFILE = {'version':'synthetic-width-screen-v1', 'vehicle_width_m':2.5,
    'side_clearance_m_each':0.5, 'required_width_m':3.5,
    'height_compliance':'unknown', 'weight_compliance':'unknown',
    'legal_access_and_turn_restrictions':'not verified',
    'scope':'width-only topology counterfactual; not an admissible mission'}


def check_catalog(catalog):
    if (catalog.get('version') != 'wildfire-incident-eval-catalog-v1' or
        catalog.get('sha256') != CATALOG_PIN or
        digest({k:v for k,v in catalog.items() if k!='sha256'}) != CATALOG_PIN or
        len(catalog.get('cases',[])) != 12):
        raise ValueError('untrusted catalog')
    for c in catalog['cases']:
        if (not c.get('synthetic') or c.get('historical_replay') is not False or
            c.get('sha256') != digest({k:v for k,v in c.items() if k!='sha256'})):
            raise ValueError('untrusted synthetic case')


def anchors(catalog, source, old_snapshot):
    check_catalog(catalog)
    old = {n['id']:n for n in old_snapshot['elements'] if n.get('type')=='node'}
    current = {n['id']:n for n in source['nodes']}
    first = catalog['cases'][0]
    posts = first['temporary_posts']
    mapping = {}
    for p in posts:
        raw=p['node_id']
        if not raw.startswith('osm:n') or not raw[5:].isdigit():
            raise ValueError('invalid original node identity')
        node=int(raw[5:]); center=p['center_id']
        if node not in old or node not in current or center in mapping:
            raise ValueError('source post node unavailable or duplicate')
        before, now=old[node],current[node]
        if (before['lat'],before['lon']) != (now['lat'],now['lon']):
            raise ValueError('same-ID source coordinates changed')
        previous=first['profile']['facilities'][center]['position']
        exact=now['position_m']
        if [round(x,1) for x in exact] != previous or math.dist(previous,exact) > math.sqrt(2)*.05+1e-8:
            raise ValueError('post does not match original source rounding')
        mapping[center]={'node_id':node,'old_position':previous,'source_position':exact,
                         'rounding_gap_m':math.dist(previous,exact)}
    if set(mapping) != {'center-0','center-1'}:
        raise ValueError('unexpected post identities')
    for case in catalog['cases']:
        if {(p['center_id'],p['node_id']) for p in case['temporary_posts']} != {
                (center, 'osm:n'+str(m['node_id'])) for center,m in mapping.items()}:
            raise ValueError('case post identity mismatch')
        for center,m in mapping.items():
            if case['profile']['facilities'][center]['position'] != m['old_position']:
                raise ValueError('case post position mismatch')
        for unit in case['resources']:
            if unit['kind'] in ('crew','engine'):
                m=mapping[unit['home_base']]
                if unit['position'] != m['old_position'] or unit['base_position'] != m['old_position']:
                    raise ValueError('ground resource position is not its original post')
    return mapping


def paths_for_edge(edge, ways):
    w=ways[edge['source_way']]['nodes']; first=edge['first_segment']; last=edge['last_segment']
    step=1 if edge['direction']=='f' else -1
    seq=list(range(first,last+step,step))
    nodes=[w[seq[0]] if step==1 else w[seq[0]+1]]
    nodes.extend(w[i+1] if step==1 else w[i] for i in seq)
    return seq,nodes


def split_graph(graph, source, post_ids):
    ways={w['id']:w for w in source['ways']}; source_nodes={n['id']:n for n in source['nodes']}
    result=copy.deepcopy(graph); out=[]; added=set()
    for edge in graph['edges']:
        seq,nodes=paths_for_edge(edge,ways)
        cut=[0]+[i for i,n in enumerate(nodes[1:-1],1) if n in post_ids]+[len(seq)]
        if len(cut)==2:
            out.append(copy.deepcopy(edge)); continue
        for left,right in zip(cut,cut[1:]):
            ns=nodes[left:right+1]; ss=seq[left:right]; coords=[source_nodes[n]['position_m'][:] for n in ns]
            fragment=copy.deepcopy(edge)
            fragment.update(id=f"{edge['source_way']}:{ss[0]}:{ss[-1]}:{edge['direction']}",
                source=str(ns[0]),target=str(ns[-1]),coordinates=coords,
                first_segment=ss[0],last_segment=ss[-1],
                length_m=sum(math.dist(a,b) for a,b in zip(coords,coords[1:])))
            out.append(fragment)
        added.update(nodes[i] for i in cut[1:-1])
    existing={int(n['id']) for n in graph['nodes']}
    if not post_ids <= existing | added:
        raise ValueError('post node is not a retained source vertex')
    result['nodes'] += [dict(id=str(n),position=source_nodes[n]['position_m'][:]) for n in sorted(added-existing)]
    result['nodes'].sort(key=lambda n:int(n['id']))
    positions, _, arcs, _ = indexes(source)
    reject_ambiguous_retained(positions, arcs, {int(n['id']) for n in result['nodes']})
    result['edges']=sorted(out,key=lambda e:e['id'])
    result['post_anchor_policy_version']=VERSION
    result['post_anchor_node_ids']=sorted(post_ids)
    result['parent_exercise_graph_sha256']=graph['sha256']
    result['status']='opt-in-exercise-assumptions-post-anchors-not-verified-access'
    result['sha256']=digest({k:v for k,v in result.items() if k!='sha256'})
    return result


def validate_split(new, original, source, post_ids):
    ways={w['id']:w for w in source['ways']}; nodes={n['id']:n['position_m'] for n in source['nodes']}
    if (new.get('parent_exercise_graph_sha256') != original['sha256'] or
            new.get('post_anchor_policy_version') != VERSION or
            new.get('post_anchor_node_ids') != sorted(post_ids)):
        raise ValueError('anchor graph provenance mismatch')
    def expand(graph):
        result={}
        for e in graph['edges']:
            seq,ns=paths_for_edge(e,ways)
            if (e['coordinates'] != [nodes[n] for n in ns] or e['source'] != str(ns[0]) or
                e['target'] != str(ns[-1]) or e['id'] != f"{e['source_way']}:{seq[0]}:{seq[-1]}:{e['direction']}" or
                not math.isclose(e['length_m'],sum(math.dist(nodes[a],nodes[b]) for a,b in zip(ns,ns[1:])),abs_tol=1e-7,rel_tol=1e-12)):
                raise ValueError('edge geometry or lineage differs from source')
            for i,a,b in zip(seq,ns,ns[1:]):
                key=(e['source_way'],i,e['direction'])
                if key in result: raise ValueError('duplicate directed source arc')
                result[key]=(a,b,e['speed_kph'],e['road_class'],tuple(e['allowed_modes']),e['exercise_access_basis'])
        return result
    if expand(new)!=expand(original):
        raise ValueError('directed arcs or permissions changed')
    # Every output edge must be a contiguous subdivision of one original edge.
    # Arc-set equality alone permits merging across a retained junction and
    # silently removes a legal turn even while that junction remains a node.
    parent = {}
    for edge in original['edges']:
        seq, _ = paths_for_edge(edge, ways)
        for index in seq:
            key = (edge['source_way'], index, edge['direction'])
            if key in parent:
                raise ValueError('duplicate original source arc')
            parent[key] = edge['id']
    old_nodes={int(n['id']):n['position'] for n in original['nodes']}
    new_nodes={int(n['id']):n['position'] for n in new['nodes']}
    if set(new_nodes) != set(old_nodes) | post_ids or any(new_nodes[n]!=nodes[n] for n in new_nodes):
        raise ValueError('unexpected anchor node or position')
    positions, _, arcs, _ = indexes(source)
    reject_ambiguous_retained(positions, arcs, set(new_nodes))
    for edge in new['edges']:
        seq, path = paths_for_edge(edge, ways)
        parents = {parent.get((edge['source_way'], index, edge['direction'])) for index in seq}
        if len(parents) != 1 or None in parents:
            raise ValueError('output edge is not a subdivision of one original edge')
        if any(node in new_nodes for node in path[1:-1]):
            raise ValueError('output edge hides a retained node')
    network=RoadNetwork(new,new['sha256'])
    return {'nodes':len(network.nodes),'edges':len(network.edges),'post_nodes_added':len(set(new_nodes)-set(old_nodes))}


def draft_catalog(catalog, mapping, network_sha):
    check_catalog(catalog)
    cases=[]
    for case in catalog['cases']:
        draft=copy.deepcopy(case)
        draft['id']='expanded-post-anchor-v4-'+case['id'].split('-')[-1]
        draft['version']=VERSION
        draft['parent_scenario_sha256']=case['sha256']
        replacements={tuple(m['old_position']):m['source_position'][:] for m in mapping.values()}
        for unit in draft['resources']:
            if unit['kind'] in ('crew','engine'):
                unit['position']=mapping[unit['home_base']]['source_position'][:]
                unit['base_position']=unit['position'][:]
        for center,m in mapping.items():
            draft['profile']['facilities'][center]['position']=m['source_position'][:]
        for collection in ('destinations','groups'):
            for item in draft[collection]:
                if tuple(item['position']) in replacements:
                    item['position']=replacements[tuple(item['position'])][:]
        # Old graph whitelist is invalid; no fresh operational whitelist is claimed.
        draft['profile']['allowed_road_edge_ids']=[]
        draft['profile']['version']='expanded-post-anchor-draft-profile-v4'
        draft['post_anchor_provenance']={'source_graph_sha256':network_sha,
            'identity':'same original OSM node; source coordinates undo 0.1m rounding',
            'status':'private draft; not runtime-qualified', 'posts':copy.deepcopy(mapping)}
        draft['sha256']=digest({k:v for k,v in draft.items() if k!='sha256'})
        cases.append(draft)
    result={'version':VERSION,'status':'private-unqualified-not-runnable',
        'parent_catalog_sha256':catalog['sha256'],'original_inputs_sha256':catalog['inputs_sha256'],
        'exercise_network_sha256':network_sha,'cases':cases}
    result['sha256']=digest(result)
    return result


def width_screen(source):
    way=next(w for w in source['ways'] if w['id']==WIDTH_WAY)
    tags=way['tags']; rw,rn=restriction_sets(source)
    width=tags.get('width')
    if not isinstance(width,str) or width.strip()!='5':
        raise ValueError('target way width evidence changed')
    if (PROFILE['vehicle_width_m']+2*PROFILE['side_clearance_m_each'] != PROFILE['required_width_m']):
        raise ValueError('invalid synthetic width profile')
    return {'profile':dict(PROFILE),'tagged_width_m':5.,'width_only_pass':5.>=PROFILE['required_width_m'],
        'retrieved_restriction_way_member':WIDTH_WAY in rw,
        'retrieved_restriction_via_nodes_on_way':sum(n in rn for n in way['nodes']),
        'source_access_tags_present':sorted(k for k in tags if k in ('access','vehicle','motor_vehicle','motorcar','hgv')),
        'source_height_weight_tags_present':sorted(k for k in tags if k.startswith(('maxheight','maxweight'))),
        'decision':'unknown; width alone does not establish height, weight or legal restriction compliance'}


def graph_routes(graph, mapping):
    net=RoadNetwork(graph,graph['sha256']); a,b=[str(mapping[c]['node_id']) for c in ('center-0','center-1')]
    results={}
    for mode in ('crew','engine'):
        directions=[]
        for start,end in ((a,b),(b,a)):
            try:
                path=mode_path(net,start,end,mode)
                directions.append({'available':True,'edges':len(path),
                    'distance_m':round(sum(net.edges[e]['length_m'] for e in path),3),
                    'width_way_edges':sum(net.edges[e]['source_way']==WIDTH_WAY for e in path),
                    'basis_counts':dict(Counter(net.edges[e]['exercise_access_basis'] for e in path)),
                    'status':'topology-only; height, weight and legal compliance unverified'})
            except ValueError:
                directions.append({'available':False})
        results[mode]=directions
    return results


def write_private_bundle(directory, files):
    """Claim a new version directory; never change any existing artifact."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    for name, raw in files.items():
        if Path(name).name != name:
            raise ValueError('bundle output must be a simple filename')
        write_private_bytes(directory / name, raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in',action='store_true')
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args=parser.parse_args()
    if not args.opt_in: parser.error('--opt-in is required')
    if (not args.output_dir.is_absolute() or not args.output_dir.resolve().is_relative_to(Path('/tmp'))
            or args.output_dir.resolve() == Path('/tmp')):
        parser.error('output directory must be a new directory under /tmp')
    ensure_new_output(args.output_dir)
    source=load_candidate(); base=strict_json(BASE_PATH.read_bytes()); graph=strict_json(EXERCISE_PATH.read_bytes())
    if base.get('sha256') != BASE_PIN or graph.get('sha256') != EXERCISE_PIN:
        raise ValueError('source graph pin mismatch')
    validate_exercise(graph,base,source)
    old_raw=OLD_SOURCE_PATH.read_bytes()
    if hashlib.sha256(old_raw).hexdigest()!=SOURCE_SHA: raise ValueError('original source snapshot pin mismatch')
    old=strict_json(gzip.decompress(old_raw)); catalog=strict_json(CATALOG_PATH.read_bytes())
    mapping=anchors(catalog,source,old); ids={m['node_id'] for m in mapping.values()}
    expanded=split_graph(graph,source,ids); stats=validate_split(expanded,graph,source,ids)
    drafts=draft_catalog(catalog,mapping,expanded['sha256']); routes=graph_routes(expanded,mapping); screen=width_screen(source)
    diagnostic={'version':VERSION,'status':'private-topology-and-width-diagnostic',
        'original_catalog_sha256':catalog['sha256'],'original_source_snapshot_sha256':SOURCE_SHA,
        'source_manifest_sha256':SOURCE_ID,'parent_exercise_graph_sha256':graph['sha256'],
        'anchor_graph_sha256':expanded['sha256'],'scenario_drafts_sha256':drafts['sha256'],
        'routes':routes,'width_screen':screen}
    diagnostic['sha256']=digest(diagnostic)
    files={}
    for name,doc in [('network.json',expanded),('scenario-drafts.json',drafts),('diagnostic.json',diagnostic)]:
        raw=json.dumps(doc,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
        if name=='network.json' and len(raw)>MAX_NETWORK_BYTES: raise ValueError('network too large')
        files[name]=raw
    write_private_bundle(args.output_dir,files)
    print(json.dumps({'graph_sha256':expanded['sha256'],'drafts_sha256':drafts['sha256'],'diagnostic_sha256':diagnostic['sha256'],**stats,
        'post_rounding_gaps_m':[round(mapping[c]['rounding_gap_m'],6) for c in ('center-0','center-1')],
        'routes':routes,'width_screen':screen},sort_keys=True))

if __name__=='__main__':main()
