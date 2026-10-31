"""Opt-in, conservative exercise permissions over source-preserving road geometry."""
import copy
import json
from pathlib import Path

import pytest
from eval.expanded_road_exercise_v1 import (BASE_PATH, BASE_PIN, assess_posts, decide,
    make_exercise, mode_path, validate_exercise)
from eval.expanded_road_runtime_v1 import CANDIDATE, convert, load_candidate
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.incident_day.routing import HazardRouter
from wildfire_core.mobility_routing import RoadNetwork


def source():
    points={1:[290100.,4064000.],2:[290200.,4064000.],3:[290300.,4064000.],
            4:[290300.,4063900.],5:[290450.,4063800.],6:[290450.,4063900.]}
    ways=[{'id':10,'nodes':[1,2,3],'tags':{'highway':'residential'}},
          {'id':20,'nodes':[3,4],'tags':{'highway':'primary','oneway':'yes'}},
          {'id':30,'nodes':[5,6],'tags':{'highway':'service'}}]
    arcs=[]
    for w in ways:
        for i,(a,b) in enumerate(zip(w['nodes'],w['nodes'][1:])):
            for direction in (('f',) if w['id']==20 else ('f','r')):
                arcs.append(dict(way=w['id'],segment=i,direction=direction,
                    source=a if direction=='f' else b,target=b if direction=='f' else a))
    return dict(nodes=[dict(id=k,position_m=v) for k,v in points.items()], ways=ways,
                arcs=arcs, restriction_relations=[])


def test_explicit_optin_and_road_kinds_preserve_baseline():
    src=source(); baseline=convert(src); before=copy.deepcopy(baseline)
    with pytest.raises(ValueError,match='opt-in'):
        make_exercise(baseline,src)
    graph=make_exercise(baseline,src,opt_in=True)
    assert baseline == before
    report=validate_exercise(graph,baseline,src)
    assert report['basis_counts']['assumed_public_road'] == 3
    assert report['basis_counts']['unknown_service_access'] == 2
    net=RoadNetwork(graph,graph['sha256'])
    for mode in ('crew','engine'):
        assert mode_path(net,'1','4',mode)
        with pytest.raises(ValueError,match='no permitted'):
            mode_path(net,'4','1',mode)
        with pytest.raises(ValueError,match='no permitted'):
            mode_path(net,'1','6',mode)  # same coordinate, different source nodes


def test_hazard_router_enforces_modes_direction_and_exact_origins():
    src=source(); base=convert(src); graph=make_exercise(base,src,opt_in=True)
    net=RoadNetwork(graph,graph['sha256'])
    grid={'width':2,'height':2,'crs':'EPSG:25830',
          'transform':[500,0,290000,0,-500,4064500]}
    cells=[[1]*2 for _ in range(2)]
    router=HazardRouter(net,{'grid':grid,'fuel_classes':cells,'fuel_valid_mask':cells},cells,walk_mask=cells)
    for mode in ('crew','engine'):
        allowed,_=router._roads([('1',[])],frozenset(),{'4'},mode)
        assert '4' in allowed
        reverse,_=router._roads([('4',[])],frozenset(),{'1'},mode)
        assert '1' not in reverse
        isolated,_=router._roads([('1',[])],frozenset(),{'6'},mode)
        assert '6' not in isolated
        assert not router._origins([290100.,4064000.01],mode)


@pytest.mark.parametrize(('tags','expected'), [
    ({'highway':'residential'},(['crew','engine'],'assumed_public_road')),
    ({'highway':'residential','access':'yes'},(['crew','engine'],'explicit_access_tag_public_road')),
    ({'highway':'residential','access':'private','motorcar':'yes'},([], 'excluded_source_policy')),
    ({'highway':'residential','access':'no','motor_vehicle':'yes'},([], 'excluded_source_policy')),
    ({'highway':'residential','motor_vehicle:conditional':'no @ (winter)'},([], 'excluded_source_policy')),
    ({'highway':'residential','hgv':'no'},([], 'excluded_source_policy')),
    ({'highway':'residential','maxwidth':'2'},([], 'excluded_source_policy')),
    ({'highway':'residential','width':'1'},([], 'unknown_vehicle_dimensions_or_grade')),
    ({'highway':'residential','incline':'10%'},([], 'unknown_vehicle_dimensions_or_grade')),
    ({'highway':'residential','surface':'mud'},([], 'unknown_surface')),
    ({'highway':'service'},([], 'unknown_service_access')),
    ({'highway':'service','access':'yes'},(['crew','engine'],'explicit_access_tag_service')),
    ({'highway':'track','surface':'gravel','access':'yes'},([], 'unknown_track_access')),
    ({'highway':'track','surface':'gravel','motor_vehicle':'yes'},(['crew'],'explicit_motor_tags_track')),
    ({'highway':'track','surface':'gravel','motor_vehicle':'yes','hgv':'yes'},(['crew','engine'],'explicit_motor_tags_track')),
    ({'highway':'track','surface':'ground','motor_vehicle':'yes'},([], 'excluded_source_policy')),
    ({'highway':'path','access':'yes'},([], 'excluded_source_policy')),
])
def test_access_policy(tags,expected):
    assert decide({'id':10,'tags':tags},[{'id':1},{'id':2}],set(),set(),'f') == expected


@pytest.mark.parametrize(('node','reason'), [
    ({'id':2,'tags':{'barrier':'gate','access':'yes'}},'unknown_barrier'),
    ({'id':2,'tags':{'barrier':'cattle_grid'}},'unknown_barrier'),
    ({'id':2,'tags':{'access':'private','motorcar':'yes'}},'unknown_node_access'),
    ({'id':2,'tags':{'motor_vehicle:conditional':'no'}},'unknown_node_access'),
    ({'id':2,'tags':{'maxheight':'3.0'}},'unknown_node_access'),
])
def test_tagged_obstacles_remain_unknown(node,reason):
    assert decide({'id':10,'tags':{'highway':'residential'}},[{'id':1},node],set(),set(),'f') == ([],reason)


def test_turn_restrictions_and_wrong_oneway_blocked():
    way={'id':10,'tags':{'highway':'residential','oneway':'yes'}}
    nodes=[{'id':1},{'id':2}]
    assert decide(way,nodes,{10},set(),'f') == ([], 'excluded_restriction')
    assert decide(way,nodes,set(),{2},'f') == ([], 'excluded_restriction')
    assert decide(way,nodes,set(),set(),'r') == ([], 'excluded_source_policy')


@pytest.mark.parametrize('mutation',['modes','geometry','basis','connector'])
def test_policy_validation_rejects_changes(mutation):
    src=source(); base=convert(src); graph=make_exercise(base,src,opt_in=True)
    if mutation=='modes': graph['edges'][-1]['allowed_modes']=['engine']
    if mutation=='geometry': graph['edges'][0]['coordinates'][0][0] += 1
    if mutation=='basis': graph['edges'][0]['exercise_access_basis']='invented'
    if mutation=='connector': graph['edges'][0]['target']='6'
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    with pytest.raises(ValueError): validate_exercise(graph,base,src)


@pytest.mark.skipif(not CANDIDATE.exists() or not BASE_PATH.exists(),reason='private pinned source unavailable')
def test_full_source_graph_policy_and_real_post_proximity():
    src=load_candidate(); base=json.loads(BASE_PATH.read_bytes()); assert base['sha256']==BASE_PIN
    graph=make_exercise(base,src,opt_in=True)
    stats=validate_exercise(graph,base,src)
    assert stats['nodes']==15325 and stats['edges']==27284
    assert stats['crew_edges']>10000 and stats['engine_edges']>10000
    assert all('exercise_access_basis' in e for e in graph['edges'])
    catalog=Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json')
    if catalog.is_file():
        posts=assess_posts(graph,json.loads(catalog.read_bytes()))
        for mode in ('crew','engine'):
            assert not any(x['available'] for x in posts[mode]['directed_nearest_node_paths'])
            assert posts[mode]['actual_router_origin_matches'] == [0,0]
