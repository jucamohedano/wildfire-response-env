"""Bounded road-only staging; public observations are not work-site proof."""
import copy
from pathlib import Path
import os
import subprocess
import sys

import pytest
from eval.expanded_road_staging_v1 import (candidate_nodes,plan_case,revalidate_path,
    supported_cells,run_candidate,_edge_screen)
from eval.expanded_road_runtime_v1 import convert
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork


class Guard:
    def __init__(self,ambiguous=False):self.ambiguous=ambiguous
    def assess_origin(self,point):return {'status':'ambiguous' if self.ambiguous else 'unambiguous'}


def fixture():
    a=[290750.,4062250.];b=[292250.,4062250.]
    source={'nodes':[{'id':1,'position_m':a},{'id':2,'position_m':b}],
            'ways':[{'id':10,'nodes':[1,2],'tags':{'highway':'residential'}}],
            'arcs':[{'way':10,'segment':0,'direction':'f','source':1,'target':2},
                    {'way':10,'segment':0,'direction':'r','source':2,'target':1}]}
    graph=convert(source)
    for e in graph['edges']:e['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    network=RoadNetwork(graph,graph['sha256'])
    grid={'width':8,'height':8,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    router=HazardRouter(network,{'grid':grid},[[100]*8 for _ in range(8)],walk_mask=[[0]*8 for _ in range(8)])
    return source,network,router,Guard()


def test_terrain_scope_requires_source_fuel_and_finite_elevation():
    def cell(surface='land',status='source',fuel=1):return {'surface':surface,'fuel_status':status,'source_fuel_class':fuel}
    v2={'cells':[[cell(),cell('unknown'),cell(),cell('sea')],[cell(),cell(),cell(),cell()]],
        'terrain_candidate':[[100.,101.,None,100.],[100.,100.,float('nan'),100.]]}
    v4={'cells':[{'column':1,'row':0}], 'additional_dera_censors':[{'column':1,'row':1}]}
    v2['cells'][1][0]=cell(status='estimated',fuel=None)
    supported,censored=supported_cells(v2,v4)
    assert supported=={(0,0),(1,0),(3,1)} and censored=={(1,1)}
    v4['cells'].append({'column':1,'row':0})
    with pytest.raises(ValueError,match='ambiguous'):supported_cells(v2,v4)


def test_node_selection_uses_public_fire_sector_and_initial_wind():
    _,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    nodes=candidate_nodes(network,router,guard,[[2,2]],[0,0],[3,4],supported)
    assert nodes and all(x in {'1','2'} for x in nodes)
    assert candidate_nodes(network,router,guard,[],[0,0],[3,4],supported)==[]
    assert candidate_nodes(network,router,Guard(True),[[2,2]],[0,0],[3,4],supported)==[]
    assert '1' not in candidate_nodes(network,router,guard,[[2,2]],[0,1],[3,4],supported)


def test_fire_can_be_more_than_three_cells_from_sector():
    _,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    # Node 2 is (4,4), within three of fire (2,2) and four of sector (7,4).
    # Fire-to-sector distance exceeds three but is irrelevant to node eligibility.
    assert '2' in candidate_nodes(network,router,guard,[[2,2]],[0,0],[7,4],supported)


def test_dynamic_fire_wind_and_exclusion_revalidation_is_road_only():
    _,network,router,guard=fixture();eid='10:0:0:f'
    def check(*,fire={(2,2)},wind=(0,0),allowed={eid},excluded=(),mode='engine',ids=[eid],start='1',end='2'):
        return revalidate_path(network,router,guard,ids,start,end,mode,fire,wind,
                               allowed_edges=allowed,excluded_cells=excluded)
    assert check() and check(mode='crew')
    assert not check(wind=(0,1))  # same observation, shifted downwind buffer
    assert not check(fire={(3,4)})
    assert not check(excluded=[(3,4)])
    assert not check(allowed=set())
    assert not check(ids=['10:0:0:r'])  # no reverse traversal of forward edge
    assert not check(ids=[])
    assert not revalidate_path(network,router,Guard(True),[eid],'1','2','engine',set(),[0,0],allowed_edges={eid})
    with pytest.raises(ValueError):check(fire=iter([(2,2)]))


def test_missing_observed_fire_records_blocked_not_invented():
    source,network,router,guard=fixture()
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    post={'resources':[{'id':'e','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    public={'id':'draft','case_sha256':'b'*64,'status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[],'wind':[0,0]}
    supported={(x,y) for x in range(8) for y in range(8)}
    candidate,audit=plan_case(case,original,post,public,network,router,guard,source,None,None,supported,set(),{})
    assert candidate['status']=='blocked' and not candidate['candidates']
    assert audit['reasons']['no_node_within_bounded_observed_fire_sector_scope']==1
    assert audit['public_observation']['fire']==[]


def test_incomplete_case_collection_and_unapproved_cli_are_rejected(tmp_path):
    with pytest.raises(ValueError,match='twelve'):
        run_candidate({'cases':[]},{'cases':[]},{'cases':[]},{'cases':[]},None,None,None,None,None,None,set(),set())
    root=Path(__file__).resolve().parents[1]
    out=tmp_path/'unsupported'
    proc=subprocess.run([sys.executable,'eval/expanded_road_staging_v1.py','--output-dir',str(out)],
        cwd=root,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert proc.returncode and 'opt-in' in proc.stderr and not out.exists()


class Cover:
    def sample(self,corridor):return {10:1,50:1}


def water_fixture():
    from shapely.geometry import LineString,box
    from shapely.strtree import STRtree
    far=LineString([(300000,4050000),(300100,4050000)])
    return {'osm':STRtree([far]),'dera':STRtree([far]),
        'bounds':box(290000,4035000,332000,4064500),
        'land':box(290000,4035000,332000,4064500),'shore':far}


def test_screened_round_trip_is_a_staging_hypothesis_not_a_work_site():
    source,network,router,guard=fixture();water=water_fixture();supported={(x,y) for x in range(8) for y in range(8)}
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','case_sha256':'b'*64,'status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    cache={};candidate,audit=plan_case(case,original,post,public,network,router,guard,source,water,Cover(),supported,set(),cache)
    assert candidate['status']=='hypothetical_road_staging_candidate' and candidate['candidates']
    stage=candidate['candidates'][0]
    assert stage['source_post_node']=='1' and stage['staging_node']=='2'
    assert stage['outbound_edges']==['10:0:0:f'] and stage['return_edges']==['10:0:0:r']
    assert stage['work_site_support']=='unknown' and stage['cell_access']=='unknown'
    assert all(cache[e]['status']=='assumed_eligible' for e in stage['outbound_edges']+stage['return_edges'])
    # Current wind has changed: the previously proposed route is no longer safe
    # under the synthetic fire-buffer policy, with no fallback connector.
    assert not revalidate_path(network,router,guard,stage['outbound_edges'],'1','2','engine',[[2,2]],[0,1],
                               allowed_edges=set(stage['outbound_edges']))


def test_mapped_water_conflict_leaves_case_blocked():
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    source,network,router,guard=fixture();water=water_fixture();supported={(x,y) for x in range(8) for y in range(8)}
    water['osm']=STRtree([LineString([(291500.,4062000.),(291500.,4062500.)])])
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','case_sha256':'b'*64,'status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    candidate,audit=plan_case(case,original,post,public,network,router,guard,source,water,Cover(),supported,set(),{})
    assert candidate['status']=='blocked' and not candidate['candidates']
    assert audit['reasons'].get('osm_mapped_water_or_buffer_conflict',0)>0


def test_screen_budget_and_unavailable_geometry_fail_closed(monkeypatch):
    import eval.expanded_road_staging_v1 as module
    source,network,_,_=fixture();edge=network.edges['10:0:0:f']
    byway={x['id']:x for x in source['ways']};bynode={x['id']:x for x in source['nodes']}
    monkeypatch.setattr(module,'MAX_SCREENED_EDGES',0)
    assert _edge_screen(edge['id'],network,byway,bynode,None,None,set(),set(),{})['reason']=='edge_screen_budget_exhausted'
    monkeypatch.setattr(module,'MAX_SCREENED_EDGES',100)
    class Broken:
        def sample(self,corridor):raise ValueError('missing raster')
    result=_edge_screen(edge['id'],network,byway,bynode,water_fixture(),Broken(),
                        {(x,y) for x in range(8) for y in range(8)},set(),{})
    assert result['status']=='unknown' and result['reason']=='edge_evidence_unavailable'


def test_unavailable_screen_in_shared_cache_keeps_later_case_unknown():
    source,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    class Broken:
        def sample(self,corridor):raise ValueError('temporarily unavailable')
    cache={}
    first,first_audit=plan_case(case,original,post,public,network,router,guard,source,water_fixture(),Broken(),supported,set(),cache)
    later,later_audit=plan_case(case,original,post,public,network,router,guard,source,water_fixture(),Cover(),supported,set(),cache)
    fresh,_=plan_case(case,original,post,public,network,router,guard,source,water_fixture(),Cover(),supported,set(),{})
    assert first['status']=='search_incomplete_unknown' and first_audit['search_incomplete']
    assert later['status']=='search_incomplete_unknown' and later_audit['search_incomplete']
    assert later_audit['reasons']['cached_edge_evidence_unavailable']
    assert fresh['status']=='hypothetical_road_staging_candidate' and fresh['candidates']


@pytest.mark.parametrize('layer',['osm','dera'])
def test_missing_required_water_layer_is_incomplete_in_current_and_later_cases(layer):
    from shapely.strtree import STRtree
    source,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    water=water_fixture();water[layer]=STRtree([]);cache={}
    first,first_audit=plan_case(case,original,post,public,network,router,guard,source,water,Cover(),supported,set(),cache)
    later,later_audit=plan_case(case,original,post,public,network,router,guard,source,water,Cover(),supported,set(),cache)
    assert first['status']=='search_incomplete_unknown' and first_audit['reasons'][layer+'_water_layer_missing']
    assert later['status']=='search_incomplete_unknown' and later_audit['reasons']['cached_'+layer+'_water_layer_missing']
    assert not first['candidates'] and not later['candidates']


@pytest.mark.parametrize('limit', ['node','visit','reroute','screen','evidence','handover'])
def test_incomplete_search_is_unknown_instead_of_blocked(monkeypatch,limit):
    import eval.expanded_road_staging_v1 as module
    source,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','case_sha256':'b'*64,'status':'handover','diagnostic_identity_sha256':'c'*64,
            'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    if limit=='node':monkeypatch.setattr(module,'MAX_STAGE_NODES',0)
    elif limit=='visit':monkeypatch.setattr(router,'_roads',lambda *args:({},False))
    elif limit=='reroute':
        monkeypatch.setattr(module,'MAX_REROUTES',1)
        monkeypatch.setattr(module,'_edge_screen',lambda *args:{'status':'unknown','reason':'mapped_conflict'})
    elif limit=='screen':monkeypatch.setattr(module,'MAX_SCREENED_EDGES',0)
    elif limit=='evidence':
        monkeypatch.setattr(module,'_edge_screen',lambda *args:{'status':'unknown','reason':'edge_evidence_unavailable'})
    else:public['status']='censored'
    candidate,audit=plan_case(case,original,post,public,network,router,guard,source,water_fixture(),Cover(),supported,set(),{})
    assert not candidate['candidates'] and candidate['status']=='search_incomplete_unknown'
    assert audit['search_incomplete'] and audit['reasons']


def test_found_route_remains_candidate_when_other_nodes_not_searched(monkeypatch):
    import eval.expanded_road_staging_v1 as module
    source,network,router,guard=fixture();supported={(x,y) for x in range(8) for y in range(8)}
    monkeypatch.setattr(module,'candidate_nodes',lambda *args:['2','1'])
    monkeypatch.setattr(module,'MAX_STAGE_NODES',1)
    original={'id':'original','sha256':'a'*64};case={'id':'draft','sha256':'b'*64,'sector_grid':[3,4]}
    public={'id':'draft','status':'handover','diagnostic_identity_sha256':'c'*64,'public_fire':[[2,2]],'wind':[0,0]}
    post={'resources':[{'id':'unit','kind':'engine','state':'available','available_at':0,
                        'position':network.nodes['1'],'base_position':network.nodes['1']}]}
    candidate,audit=plan_case(case,original,post,public,network,router,guard,source,water_fixture(),Cover(),supported,set(),{})
    assert candidate['status']=='hypothetical_road_staging_candidate' and candidate['candidates']
    assert audit['search_incomplete'] and audit['reasons']['bounded_node_limit']


@pytest.mark.skipif(not (Path('/tmp/expanded-road-staging-v1-review-cache-fix/candidate.json').is_file() and
                         Path('/tmp/expanded-road-staging-v1-review-cache-fix/case-audit.json').is_file()),
                    reason='private staging candidate unavailable')
def test_full_pinned_private_case_audit_and_dynamic_revalidation():
    import json,hashlib
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval.expanded_origin_guard_v1 import GuardedOriginRouter
    from eval.expanded_road_staging_v1 import GRAPH_PIN,SOURCE_PIN
    root=Path('/tmp/expanded-road-staging-v1-review-cache-fix')
    doc=json.loads((root/'candidate.json').read_bytes());audit=json.loads((root/'case-audit.json').read_bytes())
    assert doc['sha256']==digest({k:v for k,v in doc.items() if k!='sha256'})
    assert audit['sha256']==digest({k:v for k,v in audit.items() if k!='sha256'})
    assert audit['candidate_sha256']==doc['sha256']
    assert doc['pins']['source_implementations']['tool']==hashlib.sha256(
        (Path(__file__).resolve().parents[1]/'eval/expanded_road_staging_v1.py').read_bytes()).hexdigest()
    assert len(doc['cases'])==len(audit['case_audits'])==12
    graph=json.loads(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes());source=load_candidate()
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    heights=[[None]*84 for _ in range(59)];zeros=[[0]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,
                              walk_mask=zeros,allowed_edge_ids=[e['id'] for e in graph['edges']])
    network=guard._router.network;router=guard._router
    inventory={e['edge_id']:e for e in doc['edges']}
    for edge in inventory.values():
        assert edge['status']=='assumed_eligible'
        assert digest(network.edges[edge['edge_id']]['coordinates'])==edge['geometry_sha256']
    for case,record in zip(doc['cases'],audit['case_audits']):
        assert case['original_case_id']==record['case_id']
        assert case['status'] in ('blocked','search_incomplete_unknown','hypothetical_road_staging_candidate')
        if case['status']=='blocked':assert not case['candidates'] and record['reasons']
        if not case['candidates'] and record['search_incomplete']:
            assert case['status']=='search_incomplete_unknown'
        for stage in case['candidates']:
            assert stage['work_site_support']=='unknown' and stage['cell_access']=='unknown'
            obs=record['public_observation'];allowed=set(stage['outbound_edges']+stage['return_edges'])
            assert allowed<=set(inventory)
            assert revalidate_path(network,router,guard,stage['outbound_edges'],stage['source_post_node'],
                stage['staging_node'],stage['unit_kind'],obs['fire'],obs['wind'],allowed_edges=allowed)
            assert revalidate_path(network,router,guard,stage['return_edges'],stage['staging_node'],
                stage['source_post_node'],stage['unit_kind'],obs['fire'],obs['wind'],allowed_edges=allowed)


def test_cli_refuses_worktree_assets_without_creating_output():
    root=Path(__file__).resolve().parents[1]
    directory=root/'assets'/'expanded-road-staging-v1-test-private'
    proc=subprocess.run([sys.executable,'eval/expanded_road_staging_v1.py',
        '--opt-in-no-unmapped-crossings','--output-dir',str(directory)],cwd=root,
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert proc.returncode and 'dedicated directory' in proc.stderr and not directory.exists()
