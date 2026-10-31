"""Fail-closed routing for disconnected road segment intersections."""
import copy
from pathlib import Path
import json

import pytest
from shapely.geometry import LineString
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import CANDIDATE,convert,load_candidate
from envs.wildfire_response.regional.ops.generator import digest


def make_source(junction=False):
    points={1:[290100.,4064000.],2:[290300.,4064000.],
            3:[290200.,4063900.],4:[290200.,4064100.]}
    if junction:
        points[5]=[290200.,4064000.]
        ways=[dict(id=10,nodes=[1,5,2],tags={'highway':'residential'}),
              dict(id=20,nodes=[3,5,4],tags={'highway':'residential'})]
    else:
        ways=[dict(id=10,nodes=[1,2],tags={'highway':'residential'}),
              dict(id=20,nodes=[3,4],tags={'highway':'residential'})]
    arcs=[]
    for way in ways:
        for i,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
            for d in ('f','r'):
                arcs.append(dict(way=way['id'],segment=i,direction=d,
                                 source=a if d=='f' else b,target=b if d=='f' else a))
    return dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=ways,arcs=arcs)


def make_guard(source, *, disabled_way=None, allowed=None):
    graph=convert(source)
    for edge in graph['edges']:
        edge['allowed_modes']=[] if edge['source_way']==disabled_way else ['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    cells=[[1]*2 for _ in range(2)]
    inputs={'grid':{'width':2,'height':2,'crs':'EPSG:25830',
                    'transform':[500,0,290000,0,-500,4064500]},
            'fuel_classes':cells,'fuel_valid_mask':cells}
    return GuardedOriginRouter(graph,graph['sha256'],source,digest(source),inputs,cells,
                               walk_mask=cells,allowed_edge_ids=allowed)


@pytest.mark.parametrize('mode',['crew','engine'])
@pytest.mark.parametrize('egress',[False,True])
def test_crossing_blocks_before_router_for_each_mode_and_egress(mode,egress,monkeypatch):
    source=make_source(); guard=make_guard(source)
    origin=[290200.,4064000.]
    # Frozen router picks a single road at the disconnected crossing.
    matches=guard._router._origins(origin,mode)
    assert matches and {leg['edge_id'].split(':')[0] for _,legs in matches for leg in legs} == {'10'}
    def never(*args,**kwargs): raise AssertionError('ambiguous origin reached frozen router')
    monkeypatch.setattr(guard._router,'options',never)
    result=guard.options(origin,[0,1],mode=mode,burning=set(),wind=[0,0],
                         emergency_egress=egress,target_metric=[290100.,4063750.] if egress else None)
    assert result['status']=='unmapped' and result['options']==[]
    assert result['origin_guard']['reason']=='ambiguous_source_segments'
    assert guard.assess_origin(origin)['local_source_components']==2


def test_tolerance_margin_blocks_nearby_crossing_without_overblocking_clear_point():
    guard=make_guard(make_source())
    assert guard.assess_origin([290200.0000005,4064000.])['status']=='ambiguous'
    assert guard.assess_origin([290200.000005,4064000.])['status']=='unambiguous'


def test_overlapping_geometry_with_distinct_source_ids_is_ambiguous():
    source=make_source()
    # A separate road has identical geometry but no shared source nodes.
    source['nodes'][2]['position_m']=[290100.,4064000.]
    source['nodes'][3]['position_m']=[290300.,4064000.]
    positions={n['id']:n['position_m'] for n in source['nodes']}
    edges=[]
    for arc in source['arcs']:
        a,b=arc['source'],arc['target']; w=arc['way']; d=arc['direction']
        edges.append(dict(id=f'{w}:0:0:{d}',source=str(a),target=str(b),
            coordinates=[positions[a][:],positions[b][:]], length_m=200.,speed_kph=20,
            allowed_modes=['crew','engine'],source_way=w,first_segment=0,last_segment=0,direction=d))
    graph={'version':'coastal-road-network-v1','crs':'EPSG:25830',
           'nodes':[dict(id=str(i),position=p[:]) for i,p in positions.items()],'edges':edges}
    graph['sha256']=digest(graph)
    cells=[[1]*2 for _ in range(2)]
    inputs={'grid':{'width':2,'height':2,'crs':'EPSG:25830',
                    'transform':[500,0,290000,0,-500,4064500]},
            'fuel_classes':cells,'fuel_valid_mask':cells}
    guard=GuardedOriginRouter(graph,graph['sha256'],source,digest(source),inputs,cells,walk_mask=cells)
    assert guard.assess_origin([290200.,4064000.])['local_source_components']==2
    assert guard.options([290200.,4064000.],[0,1],burning=set(),wind=[0,0])['options']==[]


def test_disabled_or_non_whitelisted_crossing_still_cannot_attach(monkeypatch):
    source=make_source()
    guard=make_guard(source,disabled_way=20,allowed=['10:0:0:f','10:0:0:r'])
    monkeypatch.setattr(guard._router,'options',lambda *a,**k: pytest.fail('route delegated'))
    assert guard.options([290200.,4064000.],[0,1],burning=set(),wind=[0,0])['status']=='unmapped'


def test_shared_source_node_is_not_a_geometric_phantom(monkeypatch):
    source=make_source(junction=True); guard=make_guard(source)
    assessment=guard.assess_origin([290200.,4064000.])
    assert assessment['source_segment_matches']==4 and assessment['local_source_components']==1
    calls=[]
    def route(*args,**kwargs):
        calls.append((args,kwargs)); return {'status':'blocked','options':[]}
    monkeypatch.setattr(guard._router,'options',route)
    out=guard.options([290200.,4064000.],[0,1],mode='engine',burning=set(),wind=[0,0],
                      emergency_egress=True,target_metric=[290100.,4063750.],revision=2)
    assert out['status']=='blocked' and len(calls)==1
    assert calls[0][1]['emergency_egress'] is True and calls[0][1]['revision']==2


def test_unambiguous_road_and_opposite_directions_delegate(monkeypatch):
    source=make_source(); guard=make_guard(source)
    assessment=guard.assess_origin([290150.,4064000.])
    assert assessment['source_segment_matches']==1 and assessment['status']=='unambiguous'
    sentinel={'status':'reachable','options':[{'id':'test'}]}
    monkeypatch.setattr(guard._router,'options',lambda *args,**kwargs: sentinel)
    assert guard.options([290150.,4064000.],[0,1],burning=set(),wind=[0,0]) is sentinel


def test_self_crossing_single_compacted_edge_is_ambiguous(monkeypatch):
    points={1:[290100.,4063900.],2:[290300.,4064100.],
            3:[290100.,4064100.],4:[290300.,4063900.]}
    way=dict(id=10,nodes=[1,2,3,4],tags={'highway':'residential'})
    arcs=[]
    for i,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
        for d in ('f','r'):
            arcs.append(dict(way=10,segment=i,direction=d,
                             source=a if d=='f' else b,target=b if d=='f' else a))
    source=dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=[way],arcs=arcs)
    guard=make_guard(source)
    assert guard.assess_origin([290200.,4064000.])['local_source_components']==2
    monkeypatch.setattr(guard._router,'options',lambda *a,**k: pytest.fail('route delegated'))
    assert guard.options([290200.,4064000.],[0,1],burning=set(),wind=[0,0])['options']==[]


def test_unambiguous_origin_reaches_real_frozen_router():
    source=make_source(); guard=make_guard(source)
    result=guard.options([290150.,4064000.],[0,1],mode='engine',
                         burning=set(),wind=[0,0],target_metric=[290300.,4064000.])
    assert result['status']=='reachable' and result['options']
    legs=result['options'][0]['journey']['legs']
    assert legs and all(leg['edge_id'].startswith('10:') for leg in legs)


def test_unknown_mode_and_invalid_position_fail_closed():
    guard=make_guard(make_source())
    with pytest.raises(ValueError,match='Invalid mode'):
        guard.options([290150.,4064000.],[0,1],mode='aircraft',burning=set(),wind=[0,0])
    with pytest.raises(ValueError):
        guard.assess_origin([float('nan'),4064000.])


def test_source_inputs_are_pinned_and_copied():
    source=make_source(); guard=make_guard(source)
    source['nodes'][0]['position_m'][0] += 10
    assert guard.assess_origin([290200.,4064000.])['status']=='ambiguous'
    with pytest.raises(ValueError,match='source pin'):
        GuardedOriginRouter({},'0'*64,source,'0'*64,{},[])


def test_tampered_graph_lineage_fails_closed():
    source=make_source(); graph=convert(source)
    graph['edges'][0]['source_way']=20
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    with pytest.raises(ValueError,match='source lineage'):
        GuardedOriginRouter(graph,graph['sha256'],source,digest(source),{},[])


@pytest.mark.skipif(not CANDIDATE.exists() or not Path('/tmp/expanded-post-anchors-v4/network.json').exists(),
                    reason='private pinned expanded graph unavailable')
def test_reported_crossing_in_full_pinned_graph(monkeypatch):
    source=load_candidate(); assert digest(source)=='e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1'
    graph=json.loads(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes())
    assert graph['sha256']=='97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525'
    edges={e['id']:e for e in graph['edges']}
    a=edges['1148795467:0:4:f']; b=edges['23359994:0:9:f']
    assert all('engine' in e['allowed_modes'] for e in (a,b))
    cross=LineString(a['coordinates']).intersection(LineString(b['coordinates']))
    assert cross.geom_type=='Point' and not cross.is_empty
    origin=[cross.x,cross.y]
    cells=[[1]*84 for _ in range(59)]
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    guard=GuardedOriginRouter(graph,graph['sha256'],source,digest(source),
        {'grid':grid,'fuel_classes':cells,'fuel_valid_mask':cells},cells,walk_mask=cells)
    assert guard._router._origins(origin,'engine')
    assert guard.assess_origin(origin)['local_source_components']>=2
    monkeypatch.setattr(guard._router,'options',lambda *a,**k: pytest.fail('route delegated'))
    result=guard.options(origin,[1,1],mode='engine',burning=set(),wind=[0,0])
    assert result['status']=='unmapped' and result['options']==[]
