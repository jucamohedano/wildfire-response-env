"""Private source-backed synthetic predeployment candidates, not a host."""
import os
for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_name]='1'
import argparse
from collections import Counter,defaultdict
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval'),str(ROOT/'envs/wildfire_response/core/src')]
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import audit_original_tiles,load_pinned_inputs,private_destination
from eval.expanded_post_anchors_v2 import anchors,draft_catalog
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import ensure_new_output,write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION='expanded-synthetic-predeployed-handover-v1'
BASE=Path('/tmp/expanded-road-staging-v1-review-cache-fix')
BASE_CANDIDATE_PIN='ea74fde5e635ff0328dc39fdc027235a9f6673238335ccad6c4b2f00951d4e3c'
BASE_AUDIT_PIN='867b64973df6b8714d98ec85ecaf8c0455cedc4e073b550083b1ac895e0bc8ff'
OUT_NAME=re.compile(r'expanded-predeployed-handover-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')
MAX_NODES=12
MAX_SCREENS=100
MAX_VARIANTS=2
SETUP_MINUTES=5


def eligible_unit(unit):
    if (unit.get('kind') not in ('engine','crew') or unit.get('state')!='available'
        or unit.get('available_at')!=0 or type(unit.get('id')) is not str or not unit['id']
        or type(unit.get('work_remaining')) is not int or unit['work_remaining']<=0
        or type(unit.get('duty_end_at')) is not int or unit['duty_end_at']<=SETUP_MINUTES):return False
    total=unit.get('total_work_remaining')
    if total is not None and (type(total) is not int or total<=0):return False
    if any(type(unit.get(k)) is not int for k in ('capacity_litres','water_litres')):return False
    return (0<=unit['water_litres']<=unit['capacity_litres'] and
            (unit['kind']!='engine' or unit['water_litres']>0))


def candidate_case(original,firecase,post,public,network,router,guard,source,water,cover,supported,censored,cache):
    """Bounded node hypotheses under the original 50 m mapped-water filter."""
    record={'original_case_id':original['id'],'original_case_sha256':original['sha256'],
        'fire_draft_id':firecase['id'],'fire_draft_sha256':firecase['sha256'],
        'post_draft_id':post['id'],'post_draft_sha256':post['sha256'],
        'handover_public_observation_sha256':digest(public),'status':'blocked',
        'variants':[],'search_incomplete':False,'reasons':Counter()}
    def finish():
        if record['variants']:record['status']='synthetic_predeployment_candidates'
        elif record['search_incomplete']:record['status']='search_incomplete_unknown'
        record['reasons']=dict(sorted(record['reasons'].items()))
        return record
    if public.get('status')!='handover':
        record['search_incomplete']=True;record['reasons']['public_handover_unavailable']+=1;return finish()
    units=sorted((u for u in post['resources'] if eligible_unit(u)),key=lambda u:u['id'])
    node_by_pos=defaultdict(list)
    for node,position in network.nodes.items():node_by_pos[tuple(position)].append(node)
    units=[u for u in units if len(node_by_pos.get(tuple(u['base_position']),[]))==1
           and u['position']==u['base_position'] and guard.assess_origin(u['base_position'])['status']=='unambiguous']
    if not units:record['reasons']['no_finite_anchored_ground_unit']+=1;return finish()
    fire=public['public_fire'];wind=public['wind']
    all_nodes=staging.candidate_nodes(network,router,guard,fire,wind,firecase['sector_grid'],supported)
    record['nodes_in_scope']=len(all_nodes);nodes=all_nodes[:MAX_NODES]
    if len(all_nodes)>MAX_NODES:
        record['search_incomplete']=True;record['reasons']['bounded_node_limit']+=1
    if not nodes:record['reasons']['no_qualified_road_node_near_public_fire_and_sector']+=1;return finish()
    byway={w['id']:w for w in source['ways']};bynode={n['id']:n for n in source['nodes']}
    incoming=defaultdict(list)
    for eid,edge in network.edges.items():
        if staging._has_both_modes(edge):incoming[edge['target']].append(eid)
    hazards=router._hazards(router._excluded(fire),wind)
    def find_edge(ids):
        for eid in sorted(ids):
            edge=network.edges[eid]
            if (not staging._has_both_modes(edge) or not router._safe_line(edge['coordinates'],hazards,'engine')
                or guard.assess_origin(network.nodes[edge['source']])['status']!='unambiguous'
                or guard.assess_origin(network.nodes[edge['target']])['status']!='unambiguous'):
                continue
            if eid not in cache and len(cache)>=MAX_SCREENS:
                record['search_incomplete']=True;record['reasons']['bounded_water_screen_limit']+=1;return None
            screened=staging._edge_screen(eid,network,byway,bynode,water,cover,supported,censored,cache)
            if screened['status']=='assumed_eligible':return eid
            reason=screened['reason'];record['reasons'][reason]+=1
            if reason in staging.INCOMPLETE_SCREEN_REASONS:
                record['search_incomplete']=True
        return None
    for node in nodes:
        inbound=find_edge(incoming[node]);outbound=find_edge(network.adjacency[node])
        if not inbound or not outbound:continue
        unit=units[0];position=network.nodes[node][:]
        variant={'version':VERSION,'id':firecase['id']+'-synthetic-predeployed-'+unit['id']+'-'+node,
            'role':'private_unreviewed_synthetic_scenario_proposal_not_runnable',
            'original_case_sha256':original['sha256'],'fire_draft_sha256':firecase['sha256'],
            'post_draft_sha256':post['sha256'],'public_handover_sha256':digest(public),
            'assumption':'unit is already at this exact road node before handover; no prehandover trip is asserted',
            'resource_id':unit['id'],'resource_kind':unit['kind'],'source_node_id':node,
            'initial_position':position,'original_base_position':unit['base_position'][:],
            'original_base_node_id':node_by_pos[tuple(unit['base_position'])][0],
            'initial_phase':'setup_pending','setup_minutes':SETUP_MINUTES,'setup_due_minute':SETUP_MINUTES,
            'pre_handover_route':'unknown','handover_route':None,'work_target':'unknown',
            'work_authorization':'none','cell_access':'unknown',
            'resource_budget':{k:unit.get(k) for k in ('work_remaining','total_work_remaining','duty_end_at',
                                                      'water_litres','capacity_litres')},
            'evidence':{'incoming_edge':inbound,'outgoing_edge':outbound,'water_buffer_m':50,
                        'basis':'outside partial public fire/wind guard, finite coarse terrain and supported fuel',
                        'full_fire_safety':'unknown','return_to_base':'unknown','work_site':'unknown'}}
        variant['sha256']=digest(variant);record['variants'].append(variant)
        if len(record['variants'])>=MAX_VARIANTS:
            if len(all_nodes)>nodes.index(node)+1:
                record['search_incomplete']=True;record['reasons']['bounded_variant_limit']+=1
            break
    if not record['variants'] and not record['reasons']:record['reasons']['no_screened_incident_edges']+=1
    return finish()


class SetupGuard:
    """Small testable handover adapter, with no movement or work capability."""
    def __init__(self,variant,network,router,origin_guard,*,burning,wind,excluded_cells=(),opt_in=False):
        if opt_in is not True or variant.get('version')!=VERSION or variant.get('sha256')!=digest({k:v for k,v in variant.items() if k!='sha256'}):
            raise ValueError('synthetic intact variant and explicit opt-in required')
        node=variant['source_node_id'];position=variant['initial_position']
        if (node not in network.nodes or network.nodes[node]!=position or
            origin_guard.assess_origin(position)['status']!='unambiguous' or
            variant['initial_phase']!='setup_pending' or variant['setup_minutes']!=SETUP_MINUTES or
            variant['setup_due_minute']!=SETUP_MINUTES or variant['handover_route'] is not None or
            variant['work_authorization']!='none'):
            raise ValueError('invalid exact-node initial setup state')
        if not isinstance(burning,(list,tuple,set,frozenset)):raise ValueError('reusable current fire required')
        hazards=router._hazards(router._excluded(burning),wind)|router._excluded(excluded_cells)
        if router._cell(position) in hazards:raise ValueError('initial node fails current fire/wind guard')
        self._position=position[:];self._router=router;self._minute=0;self._phase='setup_pending'
    def tick(self,minute,*,burning,wind,excluded_cells=()):
        if type(minute) is not int or minute!=self._minute+1:raise ValueError('each minute must be checked in order')
        if not isinstance(burning,(list,tuple,set,frozenset)):raise ValueError('reusable current fire required')
        hazards=self._router._hazards(self._router._excluded(burning),wind)|self._router._excluded(excluded_cells)
        self._minute=minute
        if self._router._cell(self._position) in hazards:self._phase='safety_unknown'
        elif self._phase=='setup_pending' and minute>=SETUP_MINUTES:self._phase='setup_complete_work_not_authorized'
        return self.snapshot()
    def snapshot(self):
        return {'minute':self._minute,'position':self._position[:],'phase':self._phase,'work_authorized':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-synthetic-predeployment',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if not args.opt_in_synthetic_predeployment:parser.error('explicit synthetic predeployment opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent!=Path('/tmp') or
        not OUT_NAME.fullmatch(args.output_dir.name)):parser.error('fresh dedicated /tmp directory required')
    ensure_new_output(args.output_dir)
    if not hasattr(os,'sched_getaffinity'):raise ValueError('single CPU affinity unavailable')
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    old=segment._pinned_doc(BASE/'candidate.json',BASE_CANDIDATE_PIN)
    old_audit=segment._pinned_doc(BASE/'case-audit.json',BASE_AUDIT_PIN)
    if old_audit['candidate_sha256']!=BASE_CANDIDATE_PIN:raise ValueError('baseline mismatch')
    if hashlib.sha256(Path(staging.__file__).read_bytes()).hexdigest()!=old['pins']['source_implementations']['tool']:
        raise ValueError('baseline staging source mismatch')
    dependencies={'predeployment':Path(__file__),'staging':Path(staging.__file__),
        'road_water':Path(segment.__file__),'guard':ROOT/'eval/expanded_origin_guard_v1.py',
        'post_anchor':ROOT/'eval/expanded_post_anchors_v2.py','draft':ROOT/'eval/extended_scenario_drafts_v1.py',
        'operations':ROOT/'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/operations.py'}
    hashes={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in dependencies.items()}
    baseline_impl=old['pins']['source_implementations']
    if (segment.WATER_BUFFER_M!=50.0 or hashes['road_water']!=baseline_impl['road_water'] or
        hashes['guard']!=baseline_impl['origin_guard'] or hashes['draft']!=baseline_impl['source_drafts']):
        raise ValueError('baseline water policy or source implementations changed')
    audit_original_tiles();catalog,_,snapshot,source,graph,_=load_pinned_inputs()
    parent=segment._pinned_doc(staging.PARENT,staging.PARENT_PIN)
    v2=segment._pinned_doc(staging.PRIVATE/'resolution-v2.json',staging.V2_PIN)
    v4=segment._pinned_doc(staging.PRIVATE/'small-water-exercise-v4.json',staging.V4_PIN)
    drafts=build_drafts(catalog,parent['grid'],v2['grid'])
    if drafts['sha256']!=staging.DRAFT_PIN or drafts!=segment._pinned_doc(staging.DRAFTS,staging.DRAFT_PIN):
        raise ValueError('fire draft mismatch')
    posts=draft_catalog(catalog,anchors(catalog,source,snapshot),staging.GRAPH_PIN)
    if posts['sha256']!=staging.POST_PIN or posts!=segment._pinned_doc(staging.POSTS,staging.POST_PIN):
        raise ValueError('post draft mismatch')
    public=staging.reconstruct_public_handover()
    if public['sha256']!=old['pins']['public_handover'] or len(public['cases'])!=12:
        raise ValueError('actual v4 handover differs from pinned baseline')
    supported,censored=staging.supported_cells(v2,v4)
    zeros=[[0]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,staging.GRAPH_PIN,source,staging.SOURCE_PIN,{'grid':drafts['grid']},
        v2['terrain_candidate'],walk_mask=zeros,allowed_edge_ids=[e['id'] for e in graph['edges']])
    network=guard._router.network;router=guard._router;water=segment.load_water_sources()
    cover=segment.WorldCover(staging.PRIVATE/'worldcover-2021.tif');cache={};records=[]
    try:
        if not (len(catalog['cases'])==len(drafts['cases'])==len(posts['cases'])==12):
            raise ValueError('case count mismatch')
        for original,firecase,post,view in zip(catalog['cases'],drafts['cases'],posts['cases'],public['cases']):
            if (firecase['parent_scenario_sha256']!=original['sha256'] or post['parent_scenario_sha256']!=original['sha256']
                or view['id']!=firecase['id'] or view['case_sha256']!=firecase['sha256']):
                raise ValueError('original case or handover identity mismatch')
            records.append(candidate_case(original,firecase,post,view,network,router,guard,source,water,cover,supported,censored,cache))
    finally:cover.close()
    if any(hashlib.sha256(v.read_bytes()).hexdigest()!=hashes[k] for k,v in dependencies.items()):
        raise ValueError('implementation changed during candidate generation')
    if len(old['cases'])!=len(records):raise ValueError('baseline case count mismatch')
    transitions=Counter()
    for record,baseline in zip(records,old['cases']):
        if (record['original_case_id']!=baseline['original_case_id'] or
            record['original_case_sha256']!=baseline['original_case_sha256'] or
            record['handover_public_observation_sha256']!=baseline['handover_public_observation_sha256']):
            raise ValueError('baseline case or public handover identity mismatch')
        record['original_staging_status']=baseline['status']
        transitions[baseline['status']+' -> '+record['status']]+=1
    result={'version':VERSION,'status':'private_synthetic_proposal_not_host_integrated',
        'assumptions':['predeployed without a reconstructed journey','no unmapped water crossing only on screened edges',
            'flood, legal, historical 2021, complete fire, actual staging safety, work site and return route unknown'],
        'pins':{'baseline':BASE_CANDIDATE_PIN,'baseline_audit':BASE_AUDIT_PIN,'catalog':staging.CATALOG_PIN,
            'graph':staging.GRAPH_PIN,'source':staging.SOURCE_PIN,'fire_drafts':staging.DRAFT_PIN,
            'post_drafts':staging.POST_PIN,'public_handover':public['sha256'],'water_v2':staging.V2_PIN,
            'mixed_v4':staging.V4_PIN,'implementations':hashes},
        'bounds':{'nodes_per_case':MAX_NODES,'variants_per_case':MAX_VARIANTS,'edge_screens':MAX_SCREENS},
        'summary':{'case_statuses':dict(Counter(r['status'] for r in records)),
                   'cases_with_incomplete_search':sum(r['search_incomplete'] for r in records),
                   'candidate_variants':sum(len(r['variants']) for r in records),
                   'original_staging_to_predeployment':dict(sorted(transitions.items()))},
        'edge_evidence':[cache[k] for k in sorted(cache)],'cases':records}
    result['sha256']=digest(result)
    args.output_dir.mkdir(mode=0o700,exist_ok=False)
    write_private_bytes(private_destination(args.output_dir/'candidate.json'),
        json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    print('Private synthetic predeployment candidates written.')


if __name__=='__main__':main()
