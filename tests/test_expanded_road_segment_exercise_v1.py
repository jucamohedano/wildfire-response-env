"""Synthetic edge screens and pinned population-path evidence."""
import copy
import json
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.strtree import STRtree
from eval.expanded_road_segment_exercise_v1 import (OUT, WATER_BUFFER_M, screen_edge,
    edge_source_nodes, screen_paths, make_candidate)
from eval.expanded_road_runtime_v1 import convert,load_candidate,CANDIDATE
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork


class Cover:
    def __init__(self,hist=None):self.hist={10:4,50:3} if hist is None else hist
    def sample(self,corridor):return dict(self.hist)


def fixture():
    a=[290100.,4064000.];b=[290300.,4064000.]
    nodes=[{'id':1,'position_m':a},{'id':2,'position_m':b}]
    way={'id':10,'nodes':[1,2],'tags':{'highway':'residential'}}
    arcs=[{'way':10,'segment':0,'direction':'f','source':1,'target':2},
          {'way':10,'segment':0,'direction':'r','source':2,'target':1}]
    source={'nodes':nodes,'ways':[way],'arcs':arcs}
    graph=convert(source)
    for e in graph['edges']:e['allowed_modes']=['engine','crew']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    edge=next(e for e in graph['edges'] if e['direction']=='f')
    far=LineString([(300000,4050000),(300100,4050000)])
    water={'osm':STRtree([far]),'dera':STRtree([far]),'bounds':box(290000,4035000,332000,4064500),
           'land':box(290000,4035000,332000,4064500),'shore':far}
    return source,graph,edge,way,nodes,water,{(0,0),(0,1)}


def test_clear_screen_is_explicit_hypothesis_and_not_cell_access():
    source,graph,edge,way,nodes,water,mixed=fixture()
    result=screen_edge(edge,way,nodes,water,Cover(),mixed,set())
    assert result['status']=='assumed_eligible'
    assert result['unmapped_crossing']=='assumed_absent_for_this_exact_edge_only'
    assert result['confidence']=='low_uncalibrated_synthetic_assumption'
    assert result['flood_legal_vehicle_and_2021']=='unknown'
    assert WATER_BUFFER_M==50


@pytest.mark.parametrize('layer',['osm','dera'])
@pytest.mark.parametrize('distance',[0,30,50])
def test_mapped_crossings_or_ambiguous_buffer_are_rejected(layer,distance):
    source,graph,edge,way,nodes,water,mixed=fixture()
    conflict=LineString([(290200.,4063900.+distance),(290200.,4064100.+distance)]) if distance==0 else \
        LineString([(290100.,4064000.+distance),(290300.,4064000.+distance)])
    water[layer]=STRtree([conflict])
    result=screen_edge(edge,way,nodes,water,Cover(),mixed,set())
    assert result['status']=='unknown' and result['reason']==layer+'_mapped_water_or_buffer_conflict'


def test_mapped_water_polygon_intersection_is_rejected():
    _,_,edge,way,nodes,water,mixed=fixture()
    water['osm']=STRtree([Polygon([(290190,4063990),(290210,4063990),(290210,4064010),(290190,4064010)])])
    assert screen_edge(edge,way,nodes,water,Cover(),mixed,set())['status']=='unknown'


@pytest.mark.parametrize('tag',['bridge','tunnel','ford'])
def test_tagged_structures_never_bypass_water_or_unknown(tag):
    _,_,edge,way,nodes,water,mixed=fixture();way=copy.deepcopy(way);way['tags'][tag]='yes'
    result=screen_edge(edge,way,nodes,water,Cover(),mixed,set())
    assert result['status']=='unknown' and result['reason']=='tagged_structure_or_ford'


def test_node_ford_and_disallowed_mode_are_unknown():
    _,_,edge,way,nodes,water,mixed=fixture();nodes=copy.deepcopy(nodes);nodes[0]['tags']={'ford':'yes'}
    assert screen_edge(edge,way,nodes,water,Cover(),mixed,set())['status']=='unknown'
    edge=copy.deepcopy(edge);edge['allowed_modes']=[]
    assert screen_edge(edge,way,nodes,water,Cover(),mixed,set())['reason']=='engine_mode_not_supported'


@pytest.mark.parametrize('hist',[{}, {80:1}, {90:1}, {0:1}, {10:1,80:1}])
def test_ambiguous_worldcover_never_qualifies(hist):
    _,_,edge,way,nodes,water,mixed=fixture()
    result=screen_edge(edge,way,nodes,water,Cover(hist),mixed,set())
    assert result['status']=='unknown' and result['reason']=='worldcover_ambiguous_or_water'


def test_missing_land_coast_or_mixed_scope_fails_closed():
    _,_,edge,way,nodes,water,mixed=fixture()
    no_land=dict(water,land=box(300000,4040000,301000,4041000))
    assert screen_edge(edge,way,nodes,no_land,Cover(),mixed,set())['reason']=='coast_or_land_face_ambiguous'
    near_coast=dict(water,shore=LineString([(290100,4064100),(290300,4064100)]))
    assert screen_edge(edge,way,nodes,near_coast,Cover(),mixed,set())['reason']=='coast_or_land_face_ambiguous'
    assert screen_edge(edge,way,nodes,water,Cover(),set(),set())['reason']=='outside_pinned_mixed_cell_scope'
    assert screen_edge(edge,way,nodes,water,Cover(),mixed,{(0,1)})['reason']=='outside_pinned_mixed_cell_scope'


def test_source_lineage_and_no_invented_connector():
    source,graph,edge,way,nodes,water,mixed=fixture()
    ways={10:way};by_id={n['id']:n for n in nodes}
    assert edge_source_nodes(edge,ways,by_id)[0]==way
    altered=copy.deepcopy(edge);altered['coordinates'][1][0]+=1
    with pytest.raises(ValueError,match='lineage'):
        edge_source_nodes(altered,ways,by_id)


class Guard:
    def __init__(self,ambiguous=False):self.ambiguous=ambiguous
    def assess_origin(self,p):return {'status':'ambiguous' if self.ambiguous else 'unambiguous'}


def test_path_inventory_checks_modes_origins_and_opt_in():
    source,graph,edge,way,nodes,water,mixed=fixture();network=RoadNetwork(graph,graph['sha256'])
    anchor=lambda ident,pos:{'status':'source_supported_exercise_anchor','graph_node_id':ident,'source_position':pos}
    cases=[{'original_case_id':'fixture','original_case_sha256':'a'*64,
        'groups':[{'id':'g','position':anchor('1',nodes[0]['position_m'])}],
        'destinations':[{'id':'d','position':anchor('2',nodes[1]['position_m'])}],
        'topology':[{'group_id':'g','destination_id':'d'}]}]
    paths,edges=screen_paths(cases,network,source,water,Cover(),mixed,set(),Guard())
    assert paths[0]['status']=='assumed_exercise_path' and len(edges)==1
    assert edges[0]['geometry_sha256']==digest(edge['coordinates'])
    paths,_=screen_paths(cases,network,source,water,Cover(),mixed,set(),Guard(True))
    assert paths[0]['status']=='unknown' and paths[0]['reason']=='ambiguous_endpoint'
    with pytest.raises(ValueError,match='opt-in'):
        make_candidate(cases,network,source,water,Cover(),mixed,set(),Guard(),opt_in=False)


@pytest.mark.skipif(not all(p.exists() for p in (CANDIDATE,OUT.parent/'expanded-road-segment-exercise-v1-r4'/'candidate.json',
    Path('/tmp/expanded-population-anchors-v1-secure/candidate.json'),Path('/tmp/expanded-post-anchors-v4/network.json'))),
    reason='private pinned source artifacts unavailable')
def test_real_pinned_47_paths_and_road_only_adapter():
    import json
    from shapely.geometry import LineString
    from eval.expanded_road_segment_exercise_v1 import (ANCHOR_PIN,GRAPH_PIN,SOURCE_PIN,OVERLAY_PIN,
        SegmentExerciseRoutes,_verify_overlay)
    graph=json.loads(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes())
    source=load_candidate()
    anchors=json.loads(Path('/tmp/expanded-population-anchors-v1-secure/candidate.json').read_bytes())
    overlay=json.loads((OUT.parent/'expanded-road-segment-exercise-v1-r4'/'candidate.json').read_bytes())
    assert overlay['sha256']==OVERLAY_PIN and _verify_overlay(overlay)==OVERLAY_PIN
    assert graph['sha256']==GRAPH_PIN and anchors['sha256']==ANCHOR_PIN and digest(source)==SOURCE_PIN
    assert len(overlay['edges'])==4 and len(overlay['paths'])==48
    assert sum(p['status']=='assumed_exercise_path' for p in overlay['paths'])==47
    assert sum(p['status']=='no_movement' for p in overlay['paths'])==1
    assert all(e['screen']['status']=='assumed_eligible' for e in overlay['edges'])
    assert all(e['screen']['flood_legal_vehicle_and_2021']=='unknown' for e in overlay['edges'])
    before_graph=graph['sha256'];before_anchors=anchors['sha256']
    with pytest.raises(ValueError,match='opt-in'):
        SegmentExerciseRoutes(graph,source,anchors,overlay,opt_in=False)
    tampered=copy.deepcopy(overlay);tampered['paths'][0]['edge_ids']=[]
    tampered['sha256']=digest({k:v for k,v in tampered.items() if k!='sha256'})
    with pytest.raises(ValueError,match='overlay pin'):
        _verify_overlay(tampered)
    router=SegmentExerciseRoutes(graph,source,anchors,overlay,opt_in=True)
    edges={e['id']:e for e in graph['edges']}
    for record in overlay['paths']:
        origin=router._network.nodes[record['source_node']]
        result=router.evacuation_options(record['original_case_id'],record['group_id'],record['destination_id'],
            origin,burning=set(),wind=[0,0],excluded_cells=[])
        if record['status']=='no_movement':
            assert result['status']=='no_movement' and not result['options']
            assert 'no arrival or safety approval' in result['scope']
        else:
            assert result['status']=='exercise_assumption_route' and len(result['options'])==1
            legs=result['options'][0]['journey']['legs']
            assert [leg['edge_id'] for leg in legs]==record['edge_ids']
            assert all(leg['mode']=='road' and leg['coordinates']==edges[leg['edge_id']]['coordinates'] for leg in legs)
            assert result['options'][0]['journey']['origin']==origin
            assert result['options'][0]['journey']['destination']==router._network.nodes[record['target_node']]
    record=next(r for r in overlay['paths'] if r['status']=='assumed_exercise_path')
    origin=router._network.nodes[record['source_node']]
    closed=router.evacuation_options(record['original_case_id'],record['group_id'],record['destination_id'],
        origin,burning=set(),wind=[0,0],excluded_cells=[router._guard._router._cell(origin)])
    assert closed['status']=='blocked' and not closed['options']
    invalid=router.evacuation_options(record['original_case_id'],record['group_id'],record['destination_id'],
        [origin[0]+.01,origin[1]],burning=set(),wind=[0,0],excluded_cells=[])
    assert invalid['status']=='unmapped' and not invalid['options']
    cross=LineString(edges['1148795467:0:4:f']['coordinates']).intersection(
        LineString(edges['23359994:0:9:f']['coordinates']))
    result=router.evacuation_options(record['original_case_id'],record['group_id'],record['destination_id'],
        [cross.x,cross.y],burning=set(),wind=[0,0],excluded_cells=[])
    assert result['reason']=='ambiguous_source_origin' and not result['options']
    assert graph['sha256']==before_graph and anchors['sha256']==before_anchors


def test_self_crossing_source_geometry_is_not_eligible():
    _,_,edge,way,nodes,water,mixed=fixture()
    edge=copy.deepcopy(edge)
    edge['coordinates']=[[290100.,4063900.],[290300.,4064100.],
                         [290100.,4064100.],[290300.,4063900.]]
    assert screen_edge(edge,way,nodes,water,Cover(),mixed,set())['reason']=='ambiguous_road_geometry'


def test_cli_requires_opt_in_and_private_path_before_source_read(tmp_path):
    import os,subprocess,sys
    root=Path(__file__).resolve().parents[1]
    target=tmp_path/'not-private'
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    no_optin=subprocess.run([sys.executable,'eval/expanded_road_segment_exercise_v1.py','--output-dir',str(target)],
                            cwd=root,env=env,capture_output=True,text=True)
    assert no_optin.returncode and 'opt-in' in no_optin.stderr and not target.exists()
    wrong_path=subprocess.run([sys.executable,'eval/expanded_road_segment_exercise_v1.py',
        '--opt-in-no-unmapped-crossings','--output-dir',str(target)],cwd=root,env=env,capture_output=True,text=True)
    assert wrong_path.returncode and 'dedicated directory' in wrong_path.stderr and not target.exists()


@pytest.mark.skipif(not all(Path(p).exists() for p in [
    '/tmp/expanded-road-segment-exercise-v1-r4/candidate.json',
    '/tmp/expanded-post-anchors-v4/network.json',
    '/tmp/expanded-population-anchors-v1-secure/candidate.json',
    '/tmp/wildfire-map-resolution-private/small-water-exercise-v4.json',
    '/tmp/wildfire-map-resolution-private/worldcover-2021.tif',
    '/home/juancm/miniconda3/envs/detectree/bin/python']),
    reason='private pinned GIS sources or raster worker unavailable')
def test_independent_real_geometry_and_47_path_reconstruction():
    from eval.expanded_road_segment_exercise_v1 import (ANCHOR_PIN,GRAPH_PIN,SOURCE_PIN,OVERLAY_PIN,
        V4_PIN,_pinned_doc,load_water_sources,WorldCover)
    from eval.expanded_guarded_routes_v1 import _verify_reviewed_pair
    from eval.expanded_origin_guard_v1 import GuardedOriginRouter
    from shapely.geometry import LineString
    source=load_candidate();graph=json.loads(Path('/tmp/expanded-post-anchors-v4/network.json').read_bytes())
    anchors=_pinned_doc(Path('/tmp/expanded-population-anchors-v1-secure/candidate.json'),ANCHOR_PIN)
    v4=_pinned_doc(Path('/tmp/wildfire-map-resolution-private/small-water-exercise-v4.json'),V4_PIN)
    overlay=_pinned_doc(Path('/tmp/expanded-road-segment-exercise-v1-r4/candidate.json'),OVERLAY_PIN)
    _verify_reviewed_pair(graph,GRAPH_PIN,source,SOURCE_PIN)
    water=load_water_sources();cover=WorldCover(Path('/tmp/wildfire-map-resolution-private/worldcover-2021.tif'))
    ways={w['id']:w for w in source['ways']};nodes={n['id']:n for n in source['nodes']}
    edges={e['id']:e for e in graph['edges']}
    mixed={(v['column'],v['row']) for v in v4['cells']}
    censored={(v['column'],v['row']) for v in v4['additional_dera_censors']}
    try:
        for record in overlay['edges']:
            edge=edges[record['edge_id']];line=LineString(edge['coordinates'])
            # Brute-force geometry independently checks STRtree filtering.
            assert all(line.distance(g)>50 for g in water['osm'].geometries)
            assert all(line.distance(g)>50 for g in water['dera'].geometries)
            assert water['land'].covers(line.buffer(50))
            assert water['shore'].distance(line)>300
            way,refs=edge_source_nodes(edge,ways,nodes)
            assert screen_edge(edge,way,refs,water,cover,mixed,censored)==record['screen']
        grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
        zeros=[[0]*84 for _ in range(59)];heights=[[None]*84 for _ in range(59)]
        guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,
                                  walk_mask=zeros,allowed_edge_ids=[e['id'] for e in graph['edges']])
        paths,inventory=screen_paths(anchors['cases'],guard._router.network,source,water,cover,mixed,censored,guard)
        assert paths==overlay['paths'] and inventory==overlay['edges']
        assert sum(p['status']=='assumed_exercise_path' for p in paths)==47
    finally:
        cover.close()


@pytest.mark.parametrize('layer',['osm','dera'])
def test_missing_water_layer_is_unknown(layer):
    _,_,edge,way,nodes,water,mixed=fixture();water[layer]=STRtree([])
    result=screen_edge(edge,way,nodes,water,Cover(),mixed,set())
    assert result['status']=='unknown' and result['reason']==layer+'_water_layer_missing'


def test_existing_private_candidate_is_not_overwritten():
    import os,subprocess,sys,hashlib
    root=Path(__file__).resolve().parents[1]
    directory=Path('/tmp/expanded-road-segment-exercise-v1-r4')
    target=directory/'candidate.json'
    if not target.exists():pytest.skip('private candidate unavailable')
    before=hashlib.sha256(target.read_bytes()).hexdigest()
    proc=subprocess.run([sys.executable,'eval/expanded_road_segment_exercise_v1.py',
        '--opt-in-no-unmapped-crossings','--output-dir',str(directory)],cwd=root,
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert proc.returncode and 'refusing to overwrite' in proc.stderr
    assert hashlib.sha256(target.read_bytes()).hexdigest()==before
