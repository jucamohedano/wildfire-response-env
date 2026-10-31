"""Private, bounded, source-backed road staging feasibility; not a work-site approval."""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'eval'))
sys.path.insert(0,str(ROOT/'envs/wildfire_response/core/src'))
from eval.expanded_population_anchors_v1 import (load_pinned_inputs,audit_original_tiles,private_destination)
from eval.expanded_post_anchors_v2 import anchors as reconstruct_post_anchors, draft_catalog
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.expanded_road_segment_exercise_v1 import (load_water_sources,WorldCover,screen_edge,
    edge_source_nodes,GRAPH_PIN,SOURCE_PIN,V4_PIN,V2_PIN,_pinned_doc)
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import ensure_new_output,write_private_bytes,strict_json
from envs.wildfire_response.regional.ops.generator import digest

VERSION='expanded-road-staging-feasibility-v1'
CATALOG_PIN='2b264d9668f0585a560d82af716f794ed386d6a7cdc713b24987269d647b33fc'
PARENT_PIN='50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d'
DRAFT_PIN='ca5308c6e10904b5e025862e37b321bfaaa7c4d5c7d7a29c55320057f8d1d389'
POST_PIN='46299ebe3e2a1529121759771afd6b68c135ed62e7e94da01eca792f7ba19a87'
V3_PIN='3c84cab090a6056c382c59a44badfc03eaa61d8f2529a14b472af087f6cde86b'
STRICT_PIN='a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a'
COMPRESSED_ROAD_PIN='976875d58d2d134ba1f5d2aa62030f23b567f56d0bf98dd9820f88a2cb3ae343'
PRIVATE=Path('/tmp/wildfire-map-resolution-private')
PARENT=Path('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json')
DRAFTS=Path('/tmp/wildfire-expanded-source-diagnostic-v1-20261030/drafts.json')
POSTS=Path('/tmp/expanded-post-anchors-v4/scenario-drafts.json')
ROAD=Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')
MANIFEST=Path('/tmp/extended-access-lattice-v1/manifest.json')
SAMPLES=ROOT/'data/extended_access_v1/tile-acquisition.json'
RASTER_PYTHON=Path('/home/juancm/miniconda3/envs/detectree/bin/python')
OUT=Path('/tmp/expanded-road-staging-v1')
OUT_NAME=re.compile(r'expanded-road-staging-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')
MAX_STAGE_NODES=12
MAX_RESULTS=2
MAX_REROUTES=4
MAX_SCREENED_EDGES=100
SECTOR_RADIUS_CELLS=4.0
FIRE_REACH_CELLS=3.0
INCOMPLETE_SCREEN_REASONS=frozenset({
    'edge_screen_budget_exhausted','edge_evidence_unavailable',
    'osm_water_layer_missing','dera_water_layer_missing',
})


def _handover_worker():
    """Reconstruct source v4 and the existing source-air public handover view."""
    fd=int(os.environ['STAGING_PRIVATE_HANDOVER_FD'])
    sys.path[:0]=[str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_containment_v1/src')]
    from wildfire_expanded_incident_v1.exercise_v4 import prepare_exercise_v4
    from wildfire_expanded_incident_v1.weather_fire_v1 import WeatherMixedFireDiagnosticV1
    strict=_pinned_doc(PRIVATE/'resolution-v1.json',STRICT_PIN)
    water=_pinned_doc(PRIVATE/'resolution-v2.json',V2_PIN)
    v3=_pinned_doc(PRIVATE/'inland-mixed-exercise-v3.json',V3_PIN)
    v4=_pinned_doc(PRIVATE/'small-water-exercise-v4.json',V4_PIN)
    parent=_pinned_doc(PARENT,PARENT_PIN)
    catalog=_pinned_doc(Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json'),CATALOG_PIN)
    inputs=prepare_exercise_v4(strict,water,v3,v4,parent,catalog,strict_pin=STRICT_PIN,water_pin=V2_PIN,
        v3_pin=V3_PIN,v4_pin=V4_PIN,parent_pin=PARENT_PIN,catalog_pin=CATALOG_PIN,
        road_path=ROAD,road_pin=COMPRESSED_ROAD_PIN,coast_path=PRIVATE/'dera-coast.geojson',
        water_path=PRIVATE/'dera-water.geojson',worldcover_path=PRIVATE/'worldcover-2021.tif',
        osm_manifest_path=MANIFEST,osm_samples_path=SAMPLES,dera_rivers_path=PRIVATE/'dera-rivers.geojson',
        scenario='nominal',crossing_proxy=.9,opt_in_mixed_experiment=True)
    drafts=build_drafts(catalog,parent['grid'],water['grid'])
    if drafts['sha256']!=DRAFT_PIN or drafts!=_pinned_doc(DRAFTS,DRAFT_PIN):
        raise ValueError('source fire drafts differ from original-catalog reconstruction')
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    reports=[]
    worker_source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for case in drafts['cases']:
        fire=WeatherMixedFireDiagnosticV1(inputs,drafts,expected_drafts_sha256=DRAFT_PIN,
            case_id=case['id'],containment_config=config,pressure_scale=1,opt_in_weather_fire=True)
        while fire._status=='active' and fire._weather_kind=='warm':fire.step()
        if fire._status!='active':
            reports.append({'id':case['id'],'case_sha256':case['sha256'],'status':'censored'})
            continue
        # Exactly the SourceAircraftV1 public handover rule; never return the
        # complete private burning set or post-handover weather schedule.
        burning=sorted(fire._fire.burning())
        public=burning[:max(1,len(burning)//2)]
        reports.append({'id':case['id'],'case_sha256':case['sha256'],'status':'handover',
                        'diagnostic_identity_sha256':digest(fire._identity),
                        'public_fire':[list(p) for p in public],'wind':case['wind'][:]})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=worker_source:
        raise RuntimeError('handover worker source changed during warm-up')
    value={'version':'expanded-road-staging-public-handover-v1','drafts_sha256':DRAFT_PIN,
           'worker_source_sha256':worker_source,'inputs_identity':dict(inputs.identity),'cases':reports}
    value['sha256']=digest(value)
    with os.fdopen(os.dup(fd),'w') as stream:
        json.dump(value,stream,sort_keys=True,separators=(',',':'),allow_nan=False)


def reconstruct_public_handover():
    if not RASTER_PYTHON.is_file():raise ValueError('validated v4 GIS runtime unavailable')
    with tempfile.TemporaryFile(mode='w+b',dir='/tmp') as private_handover:
        result=subprocess.run([str(RASTER_PYTHON),str(Path(__file__).resolve()),'--private-handover-worker'],
            cwd=ROOT,env=dict(os.environ,PROJ_NETWORK='OFF',PYTHONDONTWRITEBYTECODE='1',
                              STAGING_PRIVATE_HANDOVER_FD=str(private_handover.fileno())),
            pass_fds=(private_handover.fileno(),),capture_output=True,text=True,timeout=600)
        if result.returncode!=0:raise ValueError('private v4 public-handover reconstruction failed')
        private_handover.seek(0)
        value=strict_json(private_handover.read().decode())
    if (value.get('version')!='expanded-road-staging-public-handover-v1' or
        value.get('drafts_sha256')!=DRAFT_PIN or
        value.get('sha256')!=digest({k:v for k,v in value.items() if k!='sha256'})):
        raise ValueError('private public-handover reconstruction mismatch')
    return value


def supported_cells(v2,v4):
    mixed={(v['column'],v['row']) for v in v4['cells']}
    censored={(v['column'],v['row']) for v in v4['additional_dera_censors']}
    if len(mixed)!=len(v4['cells']) or len(censored)!=len(v4['additional_dera_censors']) or mixed & censored:
        raise ValueError('ambiguous v4 mixed or censored cells')
    supported=set();terrain=v2['terrain_candidate']
    for y,row in enumerate(v2['cells']):
        for x,cell in enumerate(row):
            h=terrain[y][x]
            if ((x,y) not in censored and (cell['surface']=='land' or (x,y) in mixed)
                and cell['fuel_status']=='source' and type(cell['source_fuel_class']) is int
                and 1<=cell['source_fuel_class']<=10 and type(h) in (int,float) and math.isfinite(h)):
                supported.add((x,y))
    return supported,censored


def _has_both_modes(edge):
    return 'engine' in edge.get('allowed_modes',[]) and 'crew' in edge.get('allowed_modes',[])


def candidate_nodes(network,router,guard,public_fire,wind,sector,supported):
    if not public_fire:return []
    fire=router._excluded(public_fire);hazards=router._hazards(fire,wind)
    incoming=Counter()
    for edge in network.edges.values():
        if _has_both_modes(edge):incoming[edge['target']]+=1
    candidates=[]
    for node,position in network.nodes.items():
        cell=router._cell(position)
        if (not router._inside(cell) or cell not in supported or cell in hazards or
            math.dist(cell,sector)>SECTOR_RADIUS_CELLS):continue
        fire_distance=min(math.dist(cell,p) for p in fire)
        if (fire_distance>FIRE_REACH_CELLS or incoming[node]==0 or
            not any(_has_both_modes(network.edges[e]) for e in network.adjacency[node])):continue
        if guard.assess_origin(position)['status']!='unambiguous':continue
        candidates.append((fire_distance,math.dist(cell,sector),int(node),node))
    return [v[-1] for v in sorted(candidates)]


def revalidate_path(network,router,guard,edge_ids,origin,target,mode,burning,wind,*,allowed_edges,excluded_cells=()):
    """Recheck exact full-edge route against current fire/wind; no off-road access."""
    if mode not in ('crew','engine') or not isinstance(burning,(list,tuple,set,frozenset)):
        raise ValueError('invalid mode or fire collection')
    fire=router._excluded(burning);excluded=router._excluded(excluded_cells)
    if (not isinstance(edge_ids,(list,tuple)) or not edge_ids or
        any(not isinstance(eid,str) for eid in edge_ids) or
        origin not in network.nodes or target not in network.nodes):
        return False
    if (guard.assess_origin(network.nodes[origin])['status']!='unambiguous' or
        guard.assess_origin(network.nodes[target])['status']!='unambiguous'):
        return False
    hazards=router._hazards(fire,wind)|excluded;cursor=origin
    for eid in edge_ids:
        edge=network.edges.get(eid)
        if (eid not in allowed_edges or edge is None or edge['source']!=cursor or
            not _has_both_modes(edge) or mode not in edge['allowed_modes'] or
            guard.assess_origin(network.nodes[edge['target']])['status']!='unambiguous' or
            not router._safe_line(edge['coordinates'],hazards,'engine')):
            return False
        cursor=edge['target']
    return cursor==target


def _edge_screen(eid,network,source_ways,source_nodes,water,cover,supported,censored,cache):
    if eid in cache:return cache[eid]
    if len(cache)>=MAX_SCREENED_EDGES:return {'status':'unknown','reason':'edge_screen_budget_exhausted'}
    edge=network.edges[eid];way,refs=edge_source_nodes(edge,source_ways,source_nodes)
    try:
        screen=screen_edge(edge,way,refs,water,cover,supported,censored)
    except ValueError:
        screen={'status':'unknown','reason':'edge_evidence_unavailable'}
    if screen.get('reason') in ('osm_mapped_water_or_buffer_conflict','dera_mapped_water_or_buffer_conflict'):
        from shapely.geometry import LineString
        kind=screen['reason'].split('_',1)[0];line=LineString(edge['coordinates']);tree=water[kind]
        screen['nearest_mapped_water_m']=round(line.distance(tree.geometries[int(tree.nearest(line))]),3)
        screen['mapped_intersections']=len(tree.query(line,predicate='intersects'))
        screen['mapped_features_within_50m']=len(tree.query(line,predicate='dwithin',distance=50))
    # The reused screen's scope parameter includes pinned v2 land as well as
    # v4 mixed cells here. Its old label must not claim all were mixed.
    if 'touched_mixed_cells' in screen:
        screen['touched_supported_cells']=screen.pop('touched_mixed_cells')
    cache[eid]={'status':screen['status'],'reason':screen['reason'],'evidence':screen,
        'edge_id':eid,'source_way':edge['source_way'],'first_segment':edge['first_segment'],
        'last_segment':edge['last_segment'],'direction':edge['direction'],
        'geometry_sha256':digest(edge['coordinates']),'source_node':edge['source'],'target_node':edge['target'],
        'allowed_modes':edge['allowed_modes'][:]}
    return cache[eid]


def plan_case(case,original,post,public,network,router,guard,source,water,cover,supported,censored,screen_cache):
    result={'original_case_id':original['id'],'original_case_sha256':original['sha256'],
        'fire_draft_id':case['id'],'fire_draft_sha256':case['sha256'],
        'handover_public_observation_sha256':digest(public),'status':'blocked','candidates':[]}
    audit={'case_id':original['id'],'reasons':Counter(),'search_incomplete':False,
           'candidate_nodes_considered':0,'unit_attempts':0}
    def finish():
        if result['candidates']:
            result['status']='hypothetical_road_staging_candidate'
        elif audit['search_incomplete']:
            result['status']='search_incomplete_unknown'
        audit['reasons']=dict(sorted(audit['reasons'].items()))
        return result,audit
    if public.get('status')!='handover':
        audit['reasons']['handover_censored_or_unavailable']+=1
        audit['search_incomplete']=True
        return finish()
    fire=public['public_fire'];wind=public['wind'];sector=case['sector_grid']
    audit['public_observation']={'fire':copy.deepcopy(fire),'wind':wind[:],'sector':sector[:],
        'diagnostic_identity_sha256':public['diagnostic_identity_sha256'],
        'kind':'partial_source_air_handover_after_synthetic_warmup'}
    all_nodes=candidate_nodes(network,router,guard,fire,wind,sector,supported)
    nodes=all_nodes[:MAX_STAGE_NODES]
    audit['candidate_nodes_in_scope']=len(all_nodes)
    audit['candidate_nodes_considered']=len(nodes)
    if len(all_nodes)>MAX_STAGE_NODES:
        audit['search_incomplete']=True;audit['reasons']['bounded_node_limit']+=1
    if not nodes:
        audit['reasons']['no_node_within_bounded_observed_fire_sector_scope']+=1;return finish()
    units=[u for u in post['resources'] if u['kind'] in ('engine','crew') and
           u.get('state')=='available' and u.get('available_at')==0]
    if not units:
        audit['reasons']['no_initially_available_ground_unit']+=1;return finish()
    units=sorted(units,key=lambda u:u['id'])
    node_by_pos={tuple(p):ident for ident,p in network.nodes.items()}
    source_ways={w['id']:w for w in source['ways']};source_nodes={n['id']:n for n in source['nodes']}
    base_allowed={eid for eid,e in network.edges.items() if _has_both_modes(e)}
    unknown={eid for eid,item in screen_cache.items() if item['status']!='assumed_eligible'}
    # A previously unavailable screen is excluded from the graph below. We
    # cannot prove it is irrelevant to this case without exploring that edge,
    # so carry the uncertainty forward even when the router never sees it.
    for eid in unknown & base_allowed:
        reason=screen_cache[eid]['reason']
        if reason in INCOMPLETE_SCREEN_REASONS:
            audit['search_incomplete']=True
            audit['reasons']['cached_'+reason]+=1
    hazards=router._hazards(router._excluded(fire),wind)
    for stage in nodes:
        for unit in units:
            base=node_by_pos.get(tuple(unit['position']))
            if base is None or unit['position']!=unit['base_position']:
                audit['reasons']['unit_anchor_unavailable']+=1;continue
            if base==stage:
                audit['reasons']['stage_same_as_base_not_a_dispatch']+=1;continue
            if router._cell(network.nodes[base]) in hazards:
                audit['reasons']['observed_hazard_at_base']+=1;continue
            if guard.assess_origin(network.nodes[base])['status']!='unambiguous':
                audit['reasons']['ambiguous_origin']+=1;continue
            audit['unit_attempts']+=1
            for attempt in range(MAX_REROUTES):
                router.allowed_edge_ids=frozenset(base_allowed-unknown)
                outbound,done=router._roads([(base,[])],hazards,{stage},'engine')
                if not done:
                    audit['search_incomplete']=True;audit['reasons']['road_visit_limit']+=1
                if stage not in outbound:
                    audit['reasons']['no_directed_outbound_within_guard']+=1;break
                inbound,done=router._roads([(stage,[])],hazards,{base},'engine')
                if not done:
                    audit['search_incomplete']=True;audit['reasons']['road_visit_limit']+=1
                if base not in inbound:
                    audit['reasons']['no_directed_return_within_guard']+=1;break
                out=[leg['edge_id'] for leg in outbound[stage]]
                back=[leg['edge_id'] for leg in inbound[base]]
                if not out or not back:
                    audit['reasons']['empty_route']+=1;break
                rejected=[]
                for eid in dict.fromkeys(out+back):
                    screened=_edge_screen(eid,network,source_ways,source_nodes,water,cover,supported,censored,screen_cache)
                    if screened['status']!='assumed_eligible':
                        audit['reasons'][screened['reason']]+=1;rejected.append(eid)
                        if screened['reason'] in INCOMPLETE_SCREEN_REASONS:
                            audit['search_incomplete']=True
                if rejected:
                    new=set(rejected)-unknown;unknown.update(rejected)
                    if not new:break
                    if len(screen_cache)>=MAX_SCREENED_EDGES:
                        audit['search_incomplete']=True;break
                    continue
                allowed=set(out+back)
                if not (revalidate_path(network,router,guard,out,base,stage,unit['kind'],fire,wind,allowed_edges=allowed)
                    and revalidate_path(network,router,guard,back,stage,base,unit['kind'],fire,wind,allowed_edges=allowed)):
                    audit['reasons']['guarded_revalidation_failed']+=1;break
                cell=router._cell(network.nodes[stage]);fire_set=router._excluded(fire)
                result['candidates'].append({'unit_id':unit['id'],'unit_kind':unit['kind'],
                    'source_post_node':base,'staging_node':stage,'outbound_edges':out,'return_edges':back,
                    'staging_cell':list(cell),'observed_fire_distance_cells':min(math.dist(cell,f) for f in fire_set),
                    'sector_distance_cells':math.dist(cell,sector),
                    'status':'observed-planning-hypothesis-revalidate-current-fire',
                    'work_site_support':'unknown','cell_access':'unknown'})
                break
            else:
                audit['search_incomplete']=True;audit['reasons']['bounded_reroute_limit']+=1
            if len(result['candidates'])>=MAX_RESULTS:break
        if len(result['candidates'])>=MAX_RESULTS:break
    if not result['candidates'] and not audit['reasons']:
        audit['reasons']['no_supported_route_in_bounded_search']+=1
    return finish()


def run_candidate(catalog,drafts,posts,handover,network,router,guard,source,water,cover,supported,censored):
    if len(catalog['cases'])!=12 or len(drafts['cases'])!=12 or len(posts['cases'])!=12 or len(handover['cases'])!=12:
        raise ValueError('expected twelve source cases')
    screens={};cases=[];audits=[]
    for original,case,post,public in zip(catalog['cases'],drafts['cases'],posts['cases'],handover['cases']):
        if (case['parent_scenario_sha256']!=original['sha256'] or post['parent_scenario_sha256']!=original['sha256']
            or public['id']!=case['id'] or public['case_sha256']!=case['sha256']):
            raise ValueError('case, public observation or parent lineage mismatch')
        item,audit=plan_case(case,original,post,public,network,router,guard,source,water,cover,supported,censored,screens)
        cases.append(item);audits.append(audit)
    used={eid for c in cases for item in c['candidates'] for k in ('outbound_edges','return_edges') for eid in item[k]}
    if any(screens[e]['status']!='assumed_eligible' for e in used):raise ValueError('unreviewed edge in candidate')
    return cases,audits,screens,used


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-no-unmapped-crossings',action='store_true')
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args=parser.parse_args()
    if not args.opt_in_no_unmapped_crossings:parser.error('explicit synthetic no-unmapped-crossing opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent!=Path('/tmp') or not OUT_NAME.fullmatch(args.output_dir.name)):
        parser.error('output must be a fresh dedicated directory directly under /tmp')
    ensure_new_output(args.output_dir)
    source_paths={'tool':Path(__file__),
        'road_water':ROOT/'eval/expanded_road_segment_exercise_v1.py',
        'origin_guard':ROOT/'eval/expanded_origin_guard_v1.py',
        'population_anchors':ROOT/'eval/expanded_population_anchors_v1.py',
        'source_drafts':ROOT/'eval/extended_scenario_drafts_v1.py',
        'road_source':ROOT/'eval/expanded_road_runtime_v1.py',
        'map_compiler':ROOT/'eval/extended_map_resolved_v1.py',
        'source_air_public_rule':ROOT/'envs/wildfire_source_air_v1/host.py',
        'weather_fire':ROOT/'envs/wildfire_expanded_incident_v1/weather_fire_v1.py',
        'mixed_fire':ROOT/'envs/wildfire_expanded_incident_v1/mixed_v2.py',
        'mixed_converter':ROOT/'envs/wildfire_expanded_incident_v1/exercise_v4.py',
        'source_gate':ROOT/'envs/wildfire_expanded_incident_v1/source_gate_v1.py',
        'weather_proxy':ROOT/'eval/incident_weather_v1.py',
        'input_adapter':ROOT/'envs/wildfire_extended_inputs_v1/adapter_v1.py',
        'fire_adapter':ROOT/'envs/wildfire_extended_inputs_v1/adapter_fire_v1.py',
        'routing':ROOT/'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/routing.py',
        'mobility':ROOT/'envs/wildfire_response/core/src/wildfire_core/mobility_routing.py'}
    sources={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in source_paths.items()}
    audit_original_tiles();catalog,old,snapshot,source,graph,_=load_pinned_inputs()
    parent=_pinned_doc(PARENT,PARENT_PIN);v2=_pinned_doc(PRIVATE/'resolution-v2.json',V2_PIN)
    v4=_pinned_doc(PRIVATE/'small-water-exercise-v4.json',V4_PIN)
    drafts=build_drafts(catalog,parent['grid'],v2['grid'])
    if drafts['sha256']!=DRAFT_PIN or drafts!=_pinned_doc(DRAFTS,DRAFT_PIN):raise ValueError('fire draft pin mismatch')
    posts=draft_catalog(catalog,reconstruct_post_anchors(catalog,source,snapshot),GRAPH_PIN)
    if posts['sha256']!=POST_PIN or posts!=_pinned_doc(POSTS,POST_PIN):raise ValueError('ground post reconstruction mismatch')
    handover=reconstruct_public_handover()
    if len(handover['cases'])!=12 or handover.get('worker_source_sha256')!=sources['tool']:
        raise ValueError('handover count or worker implementation mismatch')
    supported,censored=supported_cells(v2,v4)
    grid=drafts['grid'];heights=v2['terrain_candidate'];zeros=[[0]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,walk_mask=zeros,
                              allowed_edge_ids=[e['id'] for e in graph['edges']])
    router=guard._router;network=router.network
    water=load_water_sources();cover=WorldCover(PRIVATE/'worldcover-2021.tif')
    try:cases,audits,screens,used=run_candidate(catalog,drafts,posts,handover,network,router,guard,source,water,cover,supported,censored)
    finally:cover.close()
    if any(hashlib.sha256(v.read_bytes()).hexdigest()!=sources[k] for k,v in source_paths.items()):
        raise RuntimeError('source dependency changed during staging assessment')
    doc={'version':VERSION,'status':'private-unreviewed-road-staging-hypothesis',
        'assumptions':{'road_water':'explicit no-unmapped-crossing on listed screened source edges only',
            'road_water_confidence':'low_uncalibrated','flood_legal_vehicle_2021':'unknown',
            'cell_access':'unknown','walking_and_connectors':'not permitted','ground_work_site':'unknown',
            'fire_reference':'existing source-air partial public observation after synthetic warm-up; stale planning information',
            'fire_proxy':'nominal v4 mixed-fire factors and 0.9 crossing proxy; uncalibrated',
            'selection':'within four cells of sector and three cells of an initially reported fire cell; outside initial observed fire/wind buffer',
            'bounds':{'stage_nodes':MAX_STAGE_NODES,'results_per_case':MAX_RESULTS,
                      'reroutes_per_pair':MAX_REROUTES,'screened_edges':MAX_SCREENED_EDGES},
            'required_before_dispatch':'current authoritative fire/wind, closures, movement, work-site and return checks'},
        'pins':{'original_catalog':CATALOG_PIN,'parent_inputs':PARENT_PIN,'fire_drafts':DRAFT_PIN,
            'post_drafts':POST_PIN,'source':SOURCE_PIN,'graph':GRAPH_PIN,'water_v2':V2_PIN,'mixed_v4':V4_PIN,
            'public_handover':handover['sha256'],'source_implementations':sources,
            'handover_input_identity':handover['inputs_identity']},
        'edges':[screens[e] for e in sorted(used)],'cases':cases}
    doc['sha256']=digest(doc)
    audit={'version':VERSION,'status':'private-case-audit-not-a-simulation-outcome',
        'candidate_sha256':doc['sha256'],'handover_sha256':handover['sha256'],
        'screened_edge_counts':dict(Counter(x['reason'] for x in screens.values())),
        'screened_edges':[screens[e] for e in sorted(screens)],'case_audits':audits}
    audit['sha256']=digest(audit)
    args.output_dir.mkdir(mode=0o700,exist_ok=False)
    for name,value in (('candidate.json',doc),('case-audit.json',audit)):
        path=private_destination(args.output_dir/name)
        write_private_bytes(path,json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    print('Private staging candidate and audit written.')

if __name__=='__main__':
    if sys.argv[1:]==['--private-handover-worker']:_handover_worker()
    else:main()
