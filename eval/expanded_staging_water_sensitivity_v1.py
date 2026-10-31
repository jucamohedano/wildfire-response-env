"""Private development-only sensitivity of staging to mapped-water proximity."""
import os
# Set before importing numeric/GIS libraries; children inherit the same limits.
for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_name]='1'
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval'),str(ROOT/'envs/wildfire_response/core/src')]
from shapely.geometry import LineString
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import audit_original_tiles,load_pinned_inputs,private_destination
from eval.expanded_post_anchors_v2 import anchors,draft_catalog
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import ensure_new_output,write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION='expanded-staging-water-sensitivity-v1'
BASE=Path('/tmp/expanded-road-staging-v1-review-cache-fix')
BASE_CANDIDATE_PIN='ea74fde5e635ff0328dc39fdc027235a9f6673238335ccad6c4b2f00951d4e3c'
BASE_AUDIT_PIN='867b64973df6b8714d98ec85ecaf8c0455cedc4e073b550083b1ac895e0bc8ff'
BUFFERS=(0,25,50)
OUT_NAME=re.compile(r'expanded-staging-water-sensitivity-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')


class WaterIndex:
    """Override only proximity queries; land, coast and raster corridor stay fixed."""
    def __init__(self,tree,buffer_m):
        self.tree=tree;self.buffer_m=buffer_m;self.geometries=tree.geometries
    def query(self,geometry,predicate=None,distance=None):
        if predicate=='dwithin':distance=self.buffer_m
        return self.tree.query(geometry,predicate=predicate,distance=distance)
    def nearest(self,geometry):return self.tree.nearest(geometry)


class CachedCover:
    def __init__(self,cover):self.cover=cover;self.cache={}
    def sample(self,corridor):
        key=hashlib.sha256(corridor.wkb).digest()
        if key not in self.cache:self.cache[key]=self.cover.sample(corridor)
        return self.cache[key]


def screen_at(buffer_m,edge,way,nodes,water,cover,supported,censored):
    if buffer_m not in BUFFERS:raise ValueError('unsupported sensitivity buffer')
    line=LineString(edge['coordinates'])
    if line.is_empty or not line.is_valid or not line.is_simple or line.length<=0:
        return {'status':'unknown','reason':'ambiguous_road_geometry'}
    # Validate nearby geometry even if the narrower query would otherwise skip it.
    # Intersections, including touching and polygon boundaries, are rejected at 0 m.
    for name in ('osm','dera'):
        tree=water[name]
        if len(tree.geometries)==0:return {'status':'unknown','reason':name+'_water_layer_missing'}
        # Bounding-box query avoids trusting a distance predicate on an invalid
        # geometry. Any invalid nearby candidate makes the edge ambiguous.
        for i in tree.query(line.buffer(50)):
            geom=tree.geometries[int(i)]
            if geom.is_empty or not geom.is_valid:
                return {'status':'unknown','reason':'ambiguous_mapped_water_geometry'}
    scoped=dict(water)
    for name in ('osm','dera'):scoped[name]=WaterIndex(water[name],buffer_m)
    result=segment.screen_edge(edge,way,nodes,scoped,cover,supported,censored)
    if result['status']=='assumed_eligible':
        # Fail closed if a library boundary convention ever misses touching water.
        for name in ('osm','dera'):
            if len(water[name].query(line,predicate='intersects')):
                raise ValueError('intersecting mapped water passed screening')
        result['water_screen_buffer_m']=buffer_m
        result['raster_and_land_corridor_m']=segment.WATER_BUFFER_M
    return result


def public_records(candidate,audit,drafts):
    """Reconstruct and hash-check the exact original handover inputs."""
    if not (len(candidate['cases'])==len(audit['case_audits'])==len(drafts['cases'])==12):
        raise ValueError('incomplete private case set')
    reports=[]
    for old,record,draft in zip(candidate['cases'],audit['case_audits'],drafts['cases']):
        if (old['original_case_id']!=record['case_id'] or old['fire_draft_id']!=draft['id'] or
            old['fire_draft_sha256']!=draft['sha256']):raise ValueError('private case alignment mismatch')
        obs=record.get('public_observation')
        report={'id':draft['id'],'case_sha256':draft['sha256'],
                'status':'handover' if obs else 'censored'}
        if obs:
            if obs['sector']!=draft['sector_grid']:raise ValueError('sector mismatch')
            report.update(diagnostic_identity_sha256=obs['diagnostic_identity_sha256'],
                          public_fire=obs['fire'],wind=obs['wind'])
        if digest(report)!=old['handover_public_observation_sha256']:
            raise ValueError('public observation pin mismatch')
        reports.append(report)
    return {'cases':reports}


def run_setting(buffer_m,*args):
    # The frozen staging module is modified only in this isolated process and
    # restored even on failure. No prior policy, artifacts or routes are written.
    prior=staging.screen_edge
    staging.screen_edge=lambda edge,way,nodes,water,cover,supported,censored:screen_at(
        buffer_m,edge,way,nodes,water,cover,supported,censored)
    try:return staging.run_candidate(*args)
    finally:staging.screen_edge=prior


def route_key(route):
    return (route['unit_id'],route['staging_node'],tuple(route['outbound_edges']),tuple(route['return_edges']))


def compare_runs(runs,network,source,water,cover,supported,censored):
    """Private route counts and conservative attribution to mapped geometries."""
    ways={w['id']:w for w in source['ways']};nodes={n['id']:n for n in source['nodes']}
    check_cache={}
    def check(eid,buffer):
        if (eid,buffer) not in check_cache:
            edge=network.edges[eid];way,refs=segment.edge_source_nodes(edge,ways,nodes)
            try:check_cache[eid,buffer]=screen_at(buffer,edge,way,refs,water,cover,supported,censored)
            except ValueError:check_cache[eid,buffer]={'status':'unknown','reason':'edge_evidence_unavailable'}
        return check_cache[eid,buffer]
    # Separately inventory every water-conflicted edge actually screened in the
    # original 50 m search, even if no alternative staging route is found.
    baseline_conflicts=[]
    for eid,item in sorted(runs[50][2].items()):
        if item['reason'] not in ('osm_mapped_water_or_buffer_conflict','dera_mapped_water_or_buffer_conflict'):
            continue
        line=LineString(network.edges[eid]['coordinates']);near=[];intersections=0
        for name in ('osm','dera'):
            tree=water[name]
            intersections+=len(tree.query(line,predicate='intersects'))
            for i in tree.query(line,predicate='dwithin',distance=50):
                geom=tree.geometries[int(i)];distance=line.distance(geom)
                near.append({'feature':name+':'+hashlib.sha256(geom.wkb).hexdigest(),
                             'distance_m':distance})
        baseline_conflicts.append({'edge_id':eid,'reason':item['reason'],'features':near,
            'mapped_intersections':intersections,
            'hypothetical_screens':{str(b):check(eid,b)['status'] for b in (0,25)}})
    conflict_sets=[{f['feature'] for f in e['features']} for e in baseline_conflicts]
    conflict_union=set.union(*conflict_sets) if conflict_sets else set()
    conflict_common=set.intersection(*conflict_sets) if conflict_sets and all(conflict_sets) else set()
    baseline_summary={'water_conflicted_directed_edges':len(baseline_conflicts),
        'distinct_nearby_mapped_features':len(conflict_union),
        'all_conflicts_share_one_nonintersecting_feature':bool(baseline_conflicts and len(conflict_union)==1
            and conflict_union==conflict_common and all(e['mapped_intersections']==0 for e in baseline_conflicts)),
        'scope':'only edges screened in the bounded original 50m search'}
    summaries={};case_records=[]
    for idx,base in enumerate(runs[50][0]):
        record={'case_id':base['original_case_id'],'case_sha256':base['original_case_sha256'],'comparisons':{}}
        before={route_key(r) for r in base['candidates']}
        for b in (0,25):
            case=runs[b][0][idx]
            if (case['original_case_id'],case['original_case_sha256'])!=(base['original_case_id'],base['original_case_sha256']):
                raise ValueError('case ordering differs across settings')
            after={route_key(r) for r in case['candidates']}
            added=after-before;removed=before-after;per_route=[]
            for route in sorted(added):
                evidence=[];features=set()
                for eid in set(route[2]+route[3]):
                    lower=check(eid,b);baseline=check(eid,50)
                    if (lower['status']!='assumed_eligible' or baseline['reason'] not in
                        ('osm_mapped_water_or_buffer_conflict','dera_mapped_water_or_buffer_conflict')):continue
                    line=LineString(network.edges[eid]['coordinates']);near=[]
                    for name in ('osm','dera'):
                        tree=water[name]
                        for i in tree.query(line,predicate='dwithin',distance=50):
                            geom=tree.geometries[int(i)];distance=line.distance(geom)
                            if b<distance<=50:
                                identity=name+':'+hashlib.sha256(geom.wkb).hexdigest()
                                near.append({'feature':identity,'distance_m':distance});features.add(identity)
                    evidence.append({'edge_id':eid,'nearby_features':near})
                per_route.append({'route_sha256':digest(route),'changed_edges':evidence,'features':sorted(features)})
            record['comparisons'][str(b)]={'added':len(added),'removed':len(removed),
                'routes_differ':bool(added or removed),'route_evidence':per_route,
                'baseline_search_incomplete':runs[50][1][idx]['search_incomplete'],
                'comparison_search_incomplete':runs[b][1][idx]['search_incomplete']}
        case_records.append(record)
    for b in (0,25):
        entries=[r['comparisons'][str(b)] for r in case_records]
        routes=[r for item in entries for r in item['route_evidence']]
        feature_sets=[set(r['features']) for r in routes]
        common=set.intersection(*feature_sets) if feature_sets and all(feature_sets) else set()
        union=set.union(*feature_sets) if feature_sets else set()
        summaries[str(b)]={'added_routes':sum(r['added'] for r in entries),
            'removed_routes':sum(r['removed'] for r in entries),
            'cases_with_changed_routes':sum(r['routes_differ'] for r in entries),
            'searches_incomplete':sum(r['baseline_search_incomplete'] or r['comparison_search_incomplete'] for r in entries),
            'all_added_routes_share_single_nonintersecting_mapped_feature':bool(routes and len(union)==1 and common==union),
            'distinct_associated_features':len(union),
            'note':'association is not proof of causation; bounded searches may differ'}
    return summaries,case_records,baseline_summary,baseline_conflicts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-development-sensitivity',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if not args.opt_in_development_sensitivity:parser.error('explicit development opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent!=Path('/tmp') or
        not OUT_NAME.fullmatch(args.output_dir.name)):
        parser.error('output must be a fresh dedicated directory directly under /tmp')
    ensure_new_output(args.output_dir)
    if not hasattr(os,'sched_getaffinity'):raise ValueError('single-CPU affinity unavailable')
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    candidate=segment._pinned_doc(BASE/'candidate.json',BASE_CANDIDATE_PIN)
    audit=segment._pinned_doc(BASE/'case-audit.json',BASE_AUDIT_PIN)
    if audit['candidate_sha256']!=BASE_CANDIDATE_PIN:raise ValueError('baseline audit mismatch')
    if hashlib.sha256(Path(staging.__file__).read_bytes()).hexdigest()!=candidate['pins']['source_implementations']['tool']:
        raise ValueError('baseline staging implementation changed')
    paths={'road_water':'eval/expanded_road_segment_exercise_v1.py',
        'origin_guard':'eval/expanded_origin_guard_v1.py','population_anchors':'eval/expanded_population_anchors_v1.py',
        'source_drafts':'eval/extended_scenario_drafts_v1.py','road_source':'eval/expanded_road_runtime_v1.py',
        'map_compiler':'eval/extended_map_resolved_v1.py','source_air_public_rule':'envs/wildfire_source_air_v1/host.py',
        'weather_fire':'envs/wildfire_expanded_incident_v1/weather_fire_v1.py',
        'mixed_fire':'envs/wildfire_expanded_incident_v1/mixed_v2.py',
        'mixed_converter':'envs/wildfire_expanded_incident_v1/exercise_v4.py',
        'source_gate':'envs/wildfire_expanded_incident_v1/source_gate_v1.py','weather_proxy':'eval/incident_weather_v1.py',
        'input_adapter':'envs/wildfire_extended_inputs_v1/adapter_v1.py',
        'fire_adapter':'envs/wildfire_extended_inputs_v1/adapter_fire_v1.py',
        'routing':'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/routing.py',
        'mobility':'envs/wildfire_response/core/src/wildfire_core/mobility_routing.py'}
    source_hashes={key:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for key,path in paths.items()}
    if any(candidate['pins']['source_implementations'].get(k)!=v for k,v in source_hashes.items()):
        raise ValueError('baseline dependency implementation changed')
    own_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    audit_original_tiles();catalog,old,snapshot,source,graph,_=load_pinned_inputs()
    parent=segment._pinned_doc(staging.PARENT,staging.PARENT_PIN)
    v2=segment._pinned_doc(staging.PRIVATE/'resolution-v2.json',staging.V2_PIN)
    v4=segment._pinned_doc(staging.PRIVATE/'small-water-exercise-v4.json',staging.V4_PIN)
    drafts=build_drafts(catalog,parent['grid'],v2['grid'])
    if drafts['sha256']!=staging.DRAFT_PIN or drafts!=segment._pinned_doc(staging.DRAFTS,staging.DRAFT_PIN):
        raise ValueError('draft mismatch')
    posts=draft_catalog(catalog,anchors(catalog,source,snapshot),staging.GRAPH_PIN)
    if posts['sha256']!=staging.POST_PIN or posts!=segment._pinned_doc(staging.POSTS,staging.POST_PIN):
        raise ValueError('post reconstruction mismatch')
    handover=public_records(candidate,audit,drafts)
    supported,censored=staging.supported_cells(v2,v4)
    zeros=[[0]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,staging.GRAPH_PIN,source,staging.SOURCE_PIN,{'grid':drafts['grid']},
        v2['terrain_candidate'],walk_mask=zeros,allowed_edge_ids=[e['id'] for e in graph['edges']])
    router=guard._router;network=router.network;water=segment.load_water_sources()
    basecover=segment.WorldCover(staging.PRIVATE/'worldcover-2021.tif');cover=CachedCover(basecover)
    try:
        inputs=(catalog,drafts,posts,handover,network,router,guard,source,water,cover,supported,censored)
        runs={b:run_setting(b,*inputs) for b in (50,25,0)}
        if runs[50][0]!=candidate['cases'] or runs[50][1]!=audit['case_audits']:
            raise ValueError('50m sensitivity baseline differs from pinned staging run')
        summary,cases,baseline_summary,baseline_conflicts=compare_runs(runs,network,source,water,cover,supported,censored)
    finally:basecover.close()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=own_hash:
        raise ValueError('sensitivity implementation changed during run')
    if (hashlib.sha256(Path(staging.__file__).read_bytes()).hexdigest()!=candidate['pins']['source_implementations']['tool'] or
        any(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=source_hashes[key] for key,path in paths.items())):
        raise ValueError('source dependency changed during run')
    doc={'version':VERSION,'scope':'private development sensitivity only; not route authorization',
        'assumptions':'hypothetical no-unmapped-crossing only on individually screened source edges; flood, legal, historical and work-site safety unknown',
        'buffers_m':list(BUFFERS),'invariant_raster_and_land_corridor_m':segment.WATER_BUFFER_M,
        'comparison':'50m vs 25m and 0m; bounded source-road staging searches; no automatic choice',
        'pins':{'baseline_candidate':BASE_CANDIDATE_PIN,'baseline_audit':BASE_AUDIT_PIN,
                'staging_source':candidate['pins']['source_implementations']['tool'],
                'sensitivity_source':own_hash,'original_catalog':staging.CATALOG_PIN,
                'graph':staging.GRAPH_PIN,'source':staging.SOURCE_PIN,'v2':staging.V2_PIN,'v4':staging.V4_PIN},
        'summary':summary,'baseline_water_conflict_summary':baseline_summary,
        'baseline_water_conflicts':baseline_conflicts,'cases':cases}
    doc['sha256']=digest(doc)
    args.output_dir.mkdir(mode=0o700,exist_ok=False)
    path=private_destination(args.output_dir/'audit.json')
    write_private_bytes(path,json.dumps(doc,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    print('Private development sensitivity audit written.')


if __name__=='__main__':main()
