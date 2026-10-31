"""Private population/destination node-anchor candidate; never an operational pack."""
import argparse
from collections import Counter, defaultdict
import copy
import gzip
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'envs/wildfire_response/core/src'))
from shapely.geometry import Point
from shapely.strtree import STRtree
from eval.expanded_guarded_routes_v1 import (_verify_reviewed_pair, REVIEWED_PAIR_MANIFEST,
                                              TRUSTED_PAIR_SHA256)
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_post_anchors_v2 import check_catalog, validate_split
from eval.expanded_road_exercise_v1 import validate_exercise, mode_path
from eval.expanded_road_runtime_v1 import (load_candidate, strict_json, ensure_new_output,
                                           write_private_bytes, CATALOG_PIN)
from eval.extended_access_v1 import SOURCE_SHA
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork

VERSION='expanded-population-anchors-v1'
OLD_NETWORK_PIN='12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16'
GRAPH_PIN='97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525'
SOURCE_PIN='e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1'
CATALOG=Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json')
GRAPH=Path('/tmp/expanded-post-anchors-v4/network.json')
PARENT=Path('/tmp/expanded-road-exercise-v3/network.json')
BASE=Path('/tmp/expanded-road-runtime-v3/network.json')
OLD=ROOT/'data/coastal_mobility_v1/network.json'
SNAPSHOT=ROOT/'data/coastal_mobility_v1/source_snapshot.json.gz'
OUT=Path('/tmp/expanded-population-anchors-v1')
PRIVATE_PARENT=Path('/tmp')
PRIVATE_NAME=re.compile(r'expanded-population-anchors-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')
# Load the existing lightweight guard directly: importing the package's
# __init__ would also initialize the unrelated expanded host.
_private_spec=importlib.util.spec_from_file_location('expanded_private_destination',
    ROOT/'envs/wildfire_expanded_incident_v1/private_output.py')
_private_module=importlib.util.module_from_spec(_private_spec)
_private_spec.loader.exec_module(_private_module)
private_destination=_private_module.private_destination
TOLERANCE_M=0.15
ROUNDING_M=math.sqrt(2)*0.05+1e-8


def private_candidate_destination(directory):
    """Restrict output to a dedicated private, non-Git direct child of /tmp."""
    directory=Path(directory)
    if (not directory.is_absolute() or directory.parent != PRIVATE_PARENT
            or not PRIVATE_NAME.fullmatch(directory.name)):
        raise ValueError('output must be a dedicated private directory directly under /tmp')
    # The existing guard requires an already existing 0700 directory, rejects
    # symlink ancestors and all Git worktrees, and requires a fresh file.
    return private_destination(directory/'candidate.json')


def reserve_private_directory(directory):
    directory=Path(directory)
    if (not directory.is_absolute() or directory.parent != PRIVATE_PARENT
            or not PRIVATE_NAME.fullmatch(directory.name)):
        raise ValueError('output must be a dedicated private directory directly under /tmp')
    ensure_new_output(directory)  # rejects regular files, directories and symlinks
    directory.mkdir(mode=0o700,exist_ok=False)
    try:
        private_candidate_destination(directory)
    except BaseException:
        try:
            directory.rmdir()  # only remove the empty directory we just created
        except OSError:
            pass
        raise
    return directory


def load_pinned_inputs():
    source=load_candidate()
    graph=strict_json(GRAPH.read_bytes())
    pair=_verify_reviewed_pair(graph,GRAPH_PIN,source,SOURCE_PIN)
    if pair!=TRUSTED_PAIR_SHA256:
        raise ValueError('unexpected reviewed pair')
    parent=strict_json(PARENT.read_bytes());base=strict_json(BASE.read_bytes())
    validate_exercise(parent,base,source)
    graph_stats=validate_split(graph,parent,source,{321315386,6119295366})
    catalog=strict_json(CATALOG.read_bytes());check_catalog(catalog)
    old=strict_json(OLD.read_bytes())
    if old.get('sha256')!=OLD_NETWORK_PIN or digest({k:v for k,v in old.items() if k!='sha256'})!=OLD_NETWORK_PIN:
        raise ValueError('original network pin mismatch')
    raw=SNAPSHOT.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:
        raise ValueError('original source snapshot pin mismatch')
    snapshot=strict_json(gzip.decompress(raw))
    return catalog,old,snapshot,source,graph,graph_stats


def audit_original_tiles():
    """Independently check cached raw acquisitions against the pinned candidate."""
    env=dict(os.environ,PROJ_NETWORK='OFF',PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,str(ROOT/'eval/extended_access_v1.py'),'validate-roads',
        '--manifest','/tmp/extended-access-lattice-v1/manifest.json',
        '--sample-manifest',str(ROOT/'data/extended_access_v1/tile-acquisition.json'),
        '--output','/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz'],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=300)
    if result.returncode!=0:
        raise ValueError('independent raw-source road audit failed or required private inputs are missing')
    report=strict_json(result.stdout)
    if (report.get('nodes'),report.get('directed_arcs'),report.get('preserved_restrictions')) != (92278,168948,262):
        raise ValueError('unexpected independent raw-source audit')
    return report


class AnchorResolver:
    """Resolve only same-ID, uniquely matching original graph/source nodes."""
    def __init__(self,old,snapshot,source,graph,guard):
        self._old=defaultdict(list)
        for node in old['nodes']:
            self._old[tuple(node['position'])].append(node['id'])
        old_nodes=[e for e in snapshot['elements'] if e.get('type')=='node']
        self._old_source={e['id']:e for e in old_nodes}
        self._source={n['id']:n for n in source['nodes']}
        self._graph={n['id']:n['position'] for n in graph['nodes']}
        if (len(self._old_source)!=len(old_nodes) or len(self._source)!=len(source['nodes'])
                or len(self._graph)!=len(graph['nodes'])):
            raise ValueError('duplicate source or graph node identity')
        self._ids=sorted(self._graph,key=int)
        self._tree=STRtree([Point(self._graph[i]) for i in self._ids])
        self._incoming=defaultdict(list);self._outgoing=defaultdict(list)
        for edge in graph['edges']:
            if 'engine' in edge.get('allowed_modes',[]):
                self._incoming[edge['target']].append(edge)
                self._outgoing[edge['source']].append(edge)
        self._guard=guard; self._cache={}

    def resolve(self,position):
        if (not isinstance(position,list) or len(position)!=2 or
            any(type(x) not in (int,float) or not math.isfinite(x) for x in position)):
            raise ValueError('invalid original position')
        key=tuple(position)
        if key in self._cache: return copy.deepcopy(self._cache[key])
        result={'status':'unresolved','original_position':position[:],
                'cell_access':'unknown','legal_and_vehicle_suitability':'unverified'}
        def fail(reason):
            result['reason']=reason;self._cache[key]=copy.deepcopy(result);return result
        old_ids=self._old.get(key,[])
        if len(old_ids)!=1 or not old_ids[0].startswith('osm:n') or not old_ids[0][5:].isdigit():
            return fail('original_source_node_not_unique')
        old_id=old_ids[0];ident=int(old_id[5:]);current=self._source.get(ident);past=self._old_source.get(ident)
        if current is None or past is None:
            return fail('same_source_node_missing')
        if (current.get('lat'),current.get('lon')) != (past.get('lat'),past.get('lon')):
            return fail('same_source_node_coordinates_changed')
        exact=current['position_m'];gap=math.dist(position,exact)
        if [round(v,1) for v in exact]!=position or gap>ROUNDING_M:
            return fail('original_rounding_not_established')
        hits=[self._ids[int(i)] for i in self._tree.query(Point(position),predicate='dwithin',distance=TOLERANCE_M)]
        if len(hits)!=1:
            return fail('reviewed_graph_node_not_unique_within_tolerance')
        if hits[0]!=str(ident) or self._graph[hits[0]]!=exact:
            return fail('nearest_graph_node_is_not_original_source_node')
        assessment=self._guard.assess_origin(exact)
        if assessment['status']!='unambiguous' or assessment['local_source_components']!=1:
            return fail('source_topology_ambiguous')
        incoming=self._incoming[hits[0]];outgoing=self._outgoing[hits[0]]
        if not incoming or not outgoing:
            return fail('engine_mode_arrival_or_departure_not_supported')
        result.update(status='source_supported_exercise_anchor',original_node_id=old_id,
            graph_node_id=hits[0],source_position=exact[:],displacement_m=gap,
            engine_incoming_edges=len(incoming),engine_outgoing_edges=len(outgoing),
            engine_access_bases=dict(sorted(Counter(e['exercise_access_basis'] for e in incoming+outgoing).items())),
            source_component_count=assessment['local_source_components'],
            interpretation='synthetic exercise node identity; not validated cell or legal access')
        self._cache[key]=copy.deepcopy(result)
        return result


def build_candidate(catalog,resolver,network):
    check_catalog(catalog)
    cache={}
    def topology(group,destination):
        if group['status']!='source_supported_exercise_anchor' or destination['status']!='source_supported_exercise_anchor':
            return 'unknown_anchor'
        a,b=group['graph_node_id'],destination['graph_node_id']
        if a==b:return 'same_source_node'
        if (a,b) not in cache:
            try:
                mode_path(network,a,b,'engine');cache[a,b]='directed_path_in_exercise_graph'
            except ValueError:
                cache[a,b]='no_directed_path_in_exercise_graph'
        return cache[a,b]
    cases=[]
    for original in catalog['cases']:
        groups=[];destinations=[]
        for group in original['groups']:
            groups.append({'id':group['id'],'original_status':group['status'],
                'original_destination_id':group['destination_id'],
                'position':resolver.resolve(group['position']),'home':resolver.resolve(group['home'])})
        for destination in original['destinations']:
            destinations.append({'id':destination['id'],'position':resolver.resolve(destination['position'])})
        connectivity=[{'group_id':g['id'],'destination_id':d['id'],
                       'engine_graph_topology':topology(g['position'],d['position'])}
                      for g in groups for d in destinations]
        cases.append({'original_case_id':original['id'],'original_case_sha256':original['sha256'],
                      'groups':groups,'destinations':destinations,'topology':connectivity})
    result={'version':VERSION,'status':'private-unqualified-not-runnable',
        'original_catalog_sha256':CATALOG_PIN,'reviewed_pair_sha256':TRUSTED_PAIR_SHA256,
        'original_graph_sha256':OLD_NETWORK_PIN,'source_sha256':SOURCE_PIN,
        'exercise_graph_sha256':GRAPH_PIN,'source_snapshot_compressed_sha256':SOURCE_SHA,
        'assumptions':{'position_tolerance_m':TOLERANCE_M,'source_rounding_m_per_axis':0.1,
            'mapping':'same original OSM node only; no connectors or different-node relocation',
            'engine_modes':'exercise assumptions; legal suitability unverified',
            'cell_access':'unknown', 'scenario_use':'private unqualified synthetic adaptation'},
        'cases':cases}
    result['sha256']=digest(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args=parser.parse_args()
    output_dir=reserve_private_directory(args.output_dir)
    source_audit=audit_original_tiles()
    catalog,old,snapshot,source,graph,stats=load_pinned_inputs()
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    cells=[[0]*84 for _ in range(59)];heights=[[None]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,
                              walk_mask=cells,allowed_edge_ids=[e['id'] for e in graph['edges']])
    network=guard._router.network
    resolver=AnchorResolver(old,snapshot,source,graph,guard)
    candidate=build_candidate(catalog,resolver,network)
    summaries=[anchor for case in candidate['cases'] for group in case['groups']
               for anchor in (group['position'],group['home'])]
    summaries += [d['position'] for case in candidate['cases'] for d in case['destinations']]
    status=Counter(a['status'] for a in summaries)
    displacements=[a['displacement_m'] for a in summaries if a['status']=='source_supported_exercise_anchor']
    # Recheck after the audit and immediately before the existing atomic,
    # write-once writer; an existing file or link is never overwritten.
    target=private_candidate_destination(output_dir)
    write_private_bytes(target,json.dumps(candidate,sort_keys=True,separators=(',', ':'),allow_nan=False).encode())
    print(json.dumps({'candidate_sha256':candidate['sha256'],'cases':len(candidate['cases']),
        'groups':sum(len(c['groups']) for c in candidate['cases']),
        'destinations':sum(len(c['destinations']) for c in candidate['cases']),
        'anchor_fields_status':dict(status),'max_displacement_m':round(max(displacements),6) if displacements else None,
        'graph_nodes':stats['nodes'],'audited_source_arcs':source_audit['directed_arcs'],
        'topology':dict(Counter(t['engine_graph_topology'] for c in candidate['cases'] for t in c['topology']))},sort_keys=True))

if __name__=='__main__':main()
