"""Identity-preserving synthetic post drafts and width-only diagnostics."""
import copy
import gzip
import json
import math
from pathlib import Path

import pytest
from eval.expanded_post_anchors_v2 import (BASE_PATH,CATALOG_PATH,EXERCISE_PATH,OLD_SOURCE_PATH,
    anchors,draft_catalog,graph_routes,split_graph,validate_split,width_screen)
from eval.expanded_road_runtime_v1 import CANDIDATE, load_candidate
from eval.expanded_road_exercise_v1 import validate_exercise
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork

pytestmark=pytest.mark.skipif(not all(p.exists() for p in (BASE_PATH,CATALOG_PATH,EXERCISE_PATH,OLD_SOURCE_PATH,CANDIDATE)),
                             reason='pinned private inputs unavailable')


@pytest.fixture(scope='module')
def material():
    source=load_candidate(); base=json.loads(BASE_PATH.read_bytes()); graph=json.loads(EXERCISE_PATH.read_bytes())
    validate_exercise(graph,base,source)
    catalog=json.loads(CATALOG_PATH.read_bytes()); old=json.loads(gzip.decompress(OLD_SOURCE_PATH.read_bytes()))
    mapping=anchors(catalog,source,old); ids={m['node_id'] for m in mapping.values()}
    split=split_graph(graph,source,ids); validate_split(split,graph,source,ids)
    return source,graph,catalog,old,mapping,ids,split


def test_posts_are_same_osm_nodes_with_only_rounding_corrected(material):
    source,graph,catalog,old,mapping,ids,split=material
    assert ids == {321315386,6119295366}
    assert '321315386' not in {n['id'] for n in graph['nodes']}
    assert ids <= {int(n['id']) for n in split['nodes']}
    assert 0 < mapping['center-0']['rounding_gap_m'] < math.sqrt(2)*.05 + 1e-8
    assert 0 < mapping['center-1']['rounding_gap_m'] < math.sqrt(2)*.05 + 1e-8
    altered=copy.deepcopy(old)
    next(x for x in altered['elements'] if x.get('type')=='node' and x['id']==321315386)['lon'] += .0001
    with pytest.raises(ValueError,match='coordinates changed'):
        anchors(catalog,source,altered)
    missing=dict(source); missing['nodes']=[n for n in source['nodes'] if n['id']!=321315386]
    with pytest.raises(ValueError,match='unavailable'):
        anchors(catalog,missing,old)


def test_split_preserves_source_arcs_directions_and_permissions(material):
    source,original,_,_,_,ids,split=material
    stats=validate_split(split,original,source,ids)
    assert stats['post_nodes_added']==1 and stats['edges']==len(original['edges'])+2
    broken=copy.deepcopy(split); e=next(e for e in broken['edges'] if e['source']=='321315386')
    e['coordinates'][0][0]+=1
    broken['sha256']=digest({k:v for k,v in broken.items() if k!='sha256'})
    with pytest.raises(ValueError,match='geometry'):
        validate_split(broken,original,source,ids)
    broken=copy.deepcopy(split); next(e for e in broken['edges'] if e['source']=='321315386')['allowed_modes']=[]
    broken['sha256']=digest({k:v for k,v in broken.items() if k!='sha256'})
    with pytest.raises(ValueError,match='permissions'):
        validate_split(broken,original,source,ids)


def test_drafts_preserve_parent_and_every_post_reference(material):
    _,_,catalog,_,mapping,_,split=material
    snapshot=copy.deepcopy(catalog); drafts=draft_catalog(catalog,mapping,split['sha256'])
    assert catalog == snapshot
    assert drafts['status']=='private-unqualified-not-runnable' and len(drafts['cases'])==12
    for parent,draft in zip(catalog['cases'],drafts['cases']):
        assert draft['id']!=parent['id'] and draft['parent_scenario_sha256']==parent['sha256']
        assert draft['profile']['allowed_road_edge_ids']==[]
        for unit in draft['resources']:
            if unit['kind'] in ('crew','engine'):
                assert unit['position']==unit['base_position']==mapping[unit['home_base']]['source_position']
        for post in draft['temporary_posts']:
            assert post['node_id']=='osm:n'+str(mapping[post['center_id']]['node_id'])
        for dest in draft['destinations']:
            for m in mapping.values():
                assert dest['position']!=m['old_position']
        for group in draft['groups']:
            for m in mapping.values():
                assert group['position']!=m['old_position']


def test_mode_aware_post_routes_use_source_only_and_no_width_edge(material):
    _,_,_,_,mapping,_,split=material
    routes=graph_routes(split,mapping)
    for mode in ('crew','engine'):
        for route in routes[mode]:
            assert route['available'] and route['edges']==1
            assert route['distance_m']==pytest.approx(149.464,abs=.001)
            assert route['width_way_edges']==0 and route['basis_counts']=={'assumed_public_road':1}
    network=RoadNetwork(split,split['sha256']); cells=[[1]*84 for _ in range(59)]
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    router=HazardRouter(network,{'grid':grid,'fuel_classes':cells,'fuel_valid_mask':cells},cells,walk_mask=cells)
    for mode in ('crew','engine'):
        for start,end in (('center-0','center-1'),('center-1','center-0')):
            point=mapping[start]['source_position']; target=str(mapping[end]['node_id'])
            origins=router._origins(point,mode)
            assert origins==[(str(mapping[start]['node_id']),[])]
            paths,_=router._roads(origins,frozenset(),{target},mode)
            assert target in paths and len(paths[target])==1
            assert paths[target][0]['coordinates'][0]==point
            assert paths[target][0]['coordinates'][-1]==mapping[end]['source_position']


def test_width_is_only_a_screen_and_does_not_authorize_edge(material):
    source,_,_,_,_,_,split=material
    result=width_screen(source)
    assert result['width_only_pass'] and result['tagged_width_m']==5
    assert result['profile']['required_width_m']==3.5
    assert result['profile']['height_compliance']==result['profile']['weight_compliance']=='unknown'
    assert not result['retrieved_restriction_way_member']
    assert result['source_height_weight_tags_present']==[]
    assert all(e['allowed_modes']==[] for e in split['edges'] if e['source_way']==47052550)
    changed=dict(source);changed['ways']=[copy.deepcopy(w) for w in source['ways']]
    next(w for w in changed['ways'] if w['id']==47052550)['tags']['width']='3'
    with pytest.raises(ValueError,match='width evidence changed'):
        width_screen(changed)
