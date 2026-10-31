"""Private predeployment hypotheses never teleport or authorize ground work."""
import copy
from pathlib import Path
import subprocess
import sys

import pytest
from shapely.geometry import LineString,box
from shapely.strtree import STRtree
from eval import expanded_predeployed_handover_v1 as pre
from eval.expanded_road_runtime_v1 import convert
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork


class Guard:
    def assess_origin(self,p):return {'status':'unambiguous'}


class Cover:
    def sample(self,corridor):return {10:1,50:1}


def fixture():
    a=[290750.,4062250.];b=[292250.,4062250.]
    source={'nodes':[{'id':1,'position_m':a},{'id':2,'position_m':b}],
        'ways':[{'id':10,'nodes':[1,2],'tags':{'highway':'residential'}}],
        'arcs':[{'way':10,'segment':0,'direction':'f','source':1,'target':2},
                {'way':10,'segment':0,'direction':'r','source':2,'target':1}]}
    graph=convert(source)
    for edge in graph['edges']:edge['allowed_modes']=['engine','crew']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    network=RoadNetwork(graph,graph['sha256'])
    grid={'width':8,'height':8,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    router=HazardRouter(network,{'grid':grid},[[100]*8 for _ in range(8)],walk_mask=[[0]*8 for _ in range(8)])
    far=LineString([(300000,4050000),(300100,4050000)])
    water={'osm':STRtree([far]),'dera':STRtree([far]),'bounds':box(290000,4035000,332000,4064500),
           'land':box(290000,4035000,332000,4064500),'shore':far}
    unit={'id':'unit','kind':'engine','state':'available','available_at':0,'work_remaining':30,
          'total_work_remaining':30,'duty_end_at':60,'water_litres':100,'capacity_litres':100,
          'position':a,'base_position':a}
    original={'id':'original','sha256':'a'*64};fire={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    post={'id':'post','sha256':'d'*64,'resources':[unit]}
    public={'status':'handover','public_fire':[[2,2]],'wind':[0,0]}
    supported={(x,y) for x in range(8) for y in range(8)}
    return original,fire,post,public,network,router,Guard(),source,water,Cover(),supported,set(),{}


def test_candidate_uses_same_source_node_without_inventing_a_trip():
    args=fixture();result=pre.candidate_case(*args)
    assert result['status']=='synthetic_predeployment_candidates' and result['variants']
    v=result['variants'][0];network=args[4]
    assert v['initial_position']==network.nodes[v['source_node_id']]
    assert v['original_base_position']==args[2]['resources'][0]['base_position']
    assert v['handover_route'] is None and v['pre_handover_route']=='unknown'
    assert v['resource_budget']['work_remaining']==30 and v['evidence']['water_buffer_m']==50
    assert v['work_target']=='unknown' and v['work_authorization']=='none'
    assert v['sha256']==digest({k:x for k,x in v.items() if k!='sha256'})


def test_50m_water_conflict_is_not_relaxed():
    args=list(fixture());water=args[8]
    water['osm']=STRtree([LineString([(290800,4062280),(292000,4062280)])])
    result=pre.candidate_case(*args)
    assert result['status']=='blocked' and not result['variants']
    assert result['reasons']['osm_mapped_water_or_buffer_conflict']


def test_budget_and_unknown_handover_fail_closed(monkeypatch):
    args=fixture();monkeypatch.setattr(pre,'MAX_SCREENS',0)
    result=pre.candidate_case(*args)
    assert result['status']=='search_incomplete_unknown' and not result['variants']
    args=list(fixture());args[3]={'status':'censored'}
    assert pre.candidate_case(*args)['status']=='search_incomplete_unknown'


def test_no_finite_unit_is_blocked():
    args=list(fixture());args[2]['resources'][0]['work_remaining']=0
    assert pre.candidate_case(*args)['reasons']['no_finite_anchored_ground_unit']


def test_setup_guard_has_no_motion_or_work_even_after_setup():
    args=fixture();variant=pre.candidate_case(*args)['variants'][0]
    guard=pre.SetupGuard(variant,args[4],args[5],args[6],burning=[[2,2]],wind=[0,0],opt_in=True)
    start=guard.snapshot();assert start['phase']=='setup_pending' and not start['work_authorized']
    start['position'][0]+=100 # snapshots cannot move the unit
    for minute in range(1,7):
        state=guard.tick(minute,burning=[[2,2]],wind=[0,0])
        assert state['position']==variant['initial_position'] and not state['work_authorized']
        assert state['phase']==('setup_pending' if minute<5 else 'setup_complete_work_not_authorized')
    with pytest.raises(ValueError):guard.tick(8,burning=[],wind=[0,0])
    bad=copy.deepcopy(variant);bad['initial_position'][0]+=1
    bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
    with pytest.raises(ValueError,match='exact-node'):pre.SetupGuard(bad,args[4],args[5],args[6],burning=[],wind=[0,0],opt_in=True)


def test_current_hazard_stops_setup_and_cannot_restart_it():
    args=fixture();v=pre.candidate_case(*args)['variants'][0];router=args[5]
    unsafe=list(router._cell(v['initial_position']))
    with pytest.raises(ValueError,match='fire/wind'):
        pre.SetupGuard(v,args[4],router,args[6],burning=[unsafe],wind=[0,0],opt_in=True)
    guard=pre.SetupGuard(v,args[4],router,args[6],burning=[[2,2]],wind=[0,0],opt_in=True)
    assert guard.tick(1,burning=[unsafe],wind=[0,0])['phase']=='safety_unknown'
    for minute in range(2,7):state=guard.tick(minute,burning=[],wind=[0,0])
    assert state['phase']=='safety_unknown' and not state['work_authorized']


def test_rejected_hazard_update_does_not_advance_setup():
    args=fixture(); variant=pre.candidate_case(*args)['variants'][0]
    guard=pre.SetupGuard(variant,args[4],args[5],args[6],burning=[[2,2]],wind=[0,0],opt_in=True)
    with pytest.raises((ValueError, TypeError)):
        guard.tick(1,burning=[[2,2]],wind=['invalid',0])
    assert guard.snapshot()['minute']==0
    assert guard.tick(1,burning=[[2,2]],wind=[0,0])['phase']=='setup_pending'


def test_existing_operations_setup_timer_never_applies_work_early():
    args=fixture();v=pre.candidate_case(*args)['variants'][0]
    u=copy.deepcopy(args[2]['resources'][0]);u.update(position=v['initial_position'][:],
        state='on_scene',due_at=pre.SETUP_MINUTES,journey=None)
    ops=Operations([u],[],[],{'revalidate_setup_safety':True,'ground_setup_minutes':5})
    applied=[]
    for minute in range(6):
        ops.tick(minute,wind=0,daylight=True,hazard_check=lambda *a:True,
                 suppress=lambda unit:applied.append(minute) or False,emit=lambda *a,**k:None)
        assert ops.resources['unit']['position']==v['initial_position']
        if minute<5:assert not applied
    assert applied==[5] # work-site callback is required before real integration


def test_cli_requires_private_fresh_output_and_opt_in(tmp_path):
    root=Path(__file__).resolve().parents[1];script=root/'eval/expanded_predeployed_handover_v1.py'
    for args in (['--output-dir',str(tmp_path/'private')],
                 ['--opt-in-synthetic-predeployment','--output-dir',str(root/'assets'/'private')]):
        proc=subprocess.run([sys.executable,str(script),*args],cwd=root,capture_output=True,text=True)
        assert proc.returncode and not (tmp_path/'private').exists()


@pytest.mark.skipif(not Path('/tmp/expanded-predeployed-handover-v1-review-fixed/candidate.json').is_file(),
                    reason='private development candidates unavailable')
def test_private_candidate_pins_and_unchanged_fire():
    import hashlib,json
    path=Path('/tmp/expanded-predeployed-handover-v1-review-fixed/candidate.json')
    doc=json.loads(path.read_bytes())
    assert path.stat().st_mode & 0o777==0o600 and path.parent.stat().st_mode & 0o777==0o700
    assert doc['sha256']==digest({k:v for k,v in doc.items() if k!='sha256'})
    assert doc['pins']['implementations']['predeployment']==hashlib.sha256(Path(pre.__file__).read_bytes()).hexdigest()
    assert len(doc['cases'])==12 and doc['pins']['fire_drafts']==pre.staging.DRAFT_PIN
    assert sum(doc['summary']['original_staging_to_predeployment'].values())==12
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval.expanded_origin_guard_v1 import GuardedOriginRouter
    graph=json.loads(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes())
    posts=pre.segment._pinned_doc(pre.staging.POSTS,pre.staging.POST_PIN)
    v2=pre.segment._pinned_doc(pre.staging.PRIVATE/'resolution-v2.json',pre.staging.V2_PIN)
    v4=pre.segment._pinned_doc(pre.staging.PRIVATE/'small-water-exercise-v4.json',pre.staging.V4_PIN)
    baseline=pre.segment._pinned_doc(pre.BASE/'case-audit.json',pre.BASE_AUDIT_PIN)
    supported,_=pre.staging.supported_cells(v2,v4)
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    zero=[[0]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,pre.staging.GRAPH_PIN,load_candidate(),pre.staging.SOURCE_PIN,
        {'grid':grid},v2['terrain_candidate'],walk_mask=zero,allowed_edge_ids=[e['id'] for e in graph['edges']])
    router=guard._router;network=router.network
    inventory={item['edge_id']:item for item in doc['edge_evidence']}
    for record,post,old in zip(doc['cases'],posts['cases'],baseline['case_audits']):
        assert record['original_case_id']==old['case_id']
        for v in record['variants']:
            assert v['fire_draft_sha256']==record['fire_draft_sha256']
            assert v['original_case_sha256']==record['original_case_sha256']
            assert v['handover_route'] is None and v['work_authorization']=='none'
            assert network.nodes[v['source_node_id']]==v['initial_position']
            unit=next(u for u in post['resources'] if u['id']==v['resource_id'])
            assert pre.eligible_unit(unit) and unit['base_position']==v['original_base_position']
            assert router._cell(v['initial_position']) in supported
            view=old['public_observation'];hazards=router._hazards(router._excluded(view['fire']),view['wind'])
            assert router._cell(v['initial_position']) not in hazards
            for field in ('incoming_edge','outgoing_edge'):
                eid=v['evidence'][field];edge=network.edges[eid]
                assert inventory[eid]['status']=='assumed_eligible' and pre.staging._has_both_modes(edge)
                assert router._safe_line(edge['coordinates'],hazards,'engine')
            assert network.edges[v['evidence']['incoming_edge']]['target']==v['source_node_id']
            assert network.edges[v['evidence']['outgoing_edge']]['source']==v['source_node_id']
