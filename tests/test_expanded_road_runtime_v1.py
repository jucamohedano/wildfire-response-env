"""Topology, source pin and fail-closed behavior of private road conversion."""
import copy
import heapq
import math
from collections import defaultdict

import pytest

from eval.expanded_road_runtime_v1 import (CANDIDATE, convert, indexes, load_candidate,
                                            post_distances, validate)
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.incident_day.routing import HazardRouter
from wildfire_core.mobility_routing import RoadNetwork


def sample():
    points = {1:[290100.,4064000.], 2:[290200.,4064000.], 3:[290300.,4064000.],
              4:[290400.,4064000.], 5:[290300.,4063900.],
              # Geometric crossing without a shared node.
              6:[290450.,4063800.], 7:[290450.,4063900.]}
    ways = [dict(id=10,nodes=[1,2,3,4],tags={'highway':'residential'}),
            dict(id=20,nodes=[3,5],tags={'highway':'service'}),
            dict(id=30,nodes=[6,7],tags={'highway':'track'})]
    arcs = []
    for way in ways:
        for i,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
            for d in (('f','r') if way['id'] != 20 else ('f',)):
                arcs.append(dict(way=way['id'],segment=i,direction=d,
                                 source=a if d=='f' else b,target=b if d=='f' else a))
    return dict(nodes=[dict(id=k,position_m=v) for k,v in points.items()],ways=ways,arcs=arcs)


def source_distances(source, start):
    nodes,ways,arcs,_ = indexes(source)
    from eval.prepare_coastal_roads import SPEEDS
    links = defaultdict(list)
    for (way,_,_), (a,b) in arcs.items():
        links[a].append((b, math.dist(nodes[a],nodes[b]) / SPEEDS[ways[way]['tags']['highway']]))
    costs = {start:0}; queue=[(0,start)]
    while queue:
        cost,node=heapq.heappop(queue)
        if cost != costs[node]:
            continue
        for end,weight in links[node]:
            value=cost+weight
            if value < costs.get(end,math.inf):
                costs[end]=value;heapq.heappush(queue,(value,end))
    return costs


def test_compaction_preserves_all_components_directions_and_costs():
    source=sample(); graph=convert(source); report=validate(graph,source)
    assert report['source_arcs'] == len(source['arcs'])
    assert '2' not in {n['id'] for n in graph['nodes']}
    assert {'3','6','7'} <= {n['id'] for n in graph['nodes']}
    assert all(edge['allowed_modes'] == [] for edge in graph['edges'])
    network=RoadNetwork(graph,graph['sha256'])
    for origin in network.nodes:
        costs=source_distances(source,int(origin))
        for target in network.nodes:
            if int(target) not in costs:
                with pytest.raises(ValueError,match='No directed'):
                    network.path(origin,target)
            else:
                result=network.path(origin,target)
                cost=sum(network.edges[e]['length_m']/network.edges[e]['speed_kph'] for e in result)
                assert cost == pytest.approx(costs[int(target)])
    with pytest.raises(ValueError,match='No directed'):
        network.path('5','3')
    with pytest.raises(ValueError,match='No directed'):
        network.path('1','6')


@pytest.mark.parametrize('offset', [0.0, 0.0000005])
def test_crossing_origin_on_other_road_rejects_ambiguous_graph(offset, monkeypatch):
    import eval.expanded_road_runtime_v1 as converter
    source = {
        'nodes': [dict(id=i, position_m=p) for i,p in {
            1:[290100.,4064000.], 2:[290200.,4064000.], 3:[290300.,4064000.],
            4:[290200.+offset,4063900.], 5:[290200.+offset,4064100.]}.items()],
        'ways':[dict(id=10,nodes=[1,2,3],tags={'highway':'residential'}),
                dict(id=20,nodes=[4,5],tags={'highway':'residential'})], 'arcs':[]}
    for way in source['ways']:
        for i,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
            for d in ('f','r'):
                source['arcs'].append(dict(way=way['id'],segment=i,direction=d,
                    source=a if d=='f' else b,target=b if d=='f' else a))
    # Simulate the previous protection-only converter: node 2 is retained,
    # but a resource on the unrelated 4--5 road would still attach to node 2.
    with monkeypatch.context() as patch:
        patch.setattr(converter,'reject_ambiguous_retained',lambda *args: None)
        unsafe=converter.convert(source)
    assert '2' in {n['id'] for n in unsafe['nodes']}
    allowed=copy.deepcopy(unsafe)
    for edge in allowed['edges']: edge['allowed_modes']=['crew','engine']
    allowed['sha256']=digest({k:v for k,v in allowed.items() if k!='sha256'})
    network=RoadNetwork(allowed,allowed['sha256']); cells=[[1]*2 for _ in range(2)]
    grid={'width':2,'height':2,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    router=HazardRouter(network,{'grid':grid,'fuel_classes':cells,'fuel_valid_mask':cells},cells,walk_mask=cells)
    assert router._origins([290200.+offset,4064000.],'engine')==[('2',[])]
    with pytest.raises(ValueError,match='ambiguous origin'):
        converter.validate(unsafe,source)
    with pytest.raises(ValueError,match='ambiguous origin'):
        converter.convert(source)


def test_coincident_source_nodes_reject_graph():
    source=sample()
    next(n for n in source['nodes'] if n['id']==6)['position_m']=[290200.,4064000.]
    next(n for n in source['nodes'] if n['id']==7)['position_m']=[290200.,4063900.]
    with pytest.raises(ValueError,match='ambiguous origin'):
        convert(source)


def test_post_split_validator_rejects_merging_over_original_junction():
    import copy
    from eval.expanded_post_anchors_v2 import split_graph, validate_split
    source=sample(); original=convert(source)
    for edge in original['edges']: edge['exercise_access_basis']='synthetic'
    original['sha256']=digest({k:v for k,v in original.items() if k!='sha256'})
    modified=split_graph(original,source,{3})
    by_id={e['id']:e for e in modified['edges']}
    first,second=by_id['10:0:1:f'],by_id['10:2:2:f']
    merged=copy.deepcopy(first)
    merged.update(id='10:0:2:f',target=second['target'],last_segment=2,
                  coordinates=first['coordinates']+second['coordinates'][1:],
                  length_m=first['length_m']+second['length_m'])
    modified['edges']=[e for e in modified['edges'] if e['id'] not in {first['id'],second['id']}]+[merged]
    modified['sha256']=digest({k:v for k,v in modified.items() if k!='sha256'})
    # Node 3 remains retained through its branch; all source arcs are still present.
    assert '3' in {n['id'] for n in modified['nodes']}
    with pytest.raises(ValueError,match='subdivision|hides an original'):
        validate_split(modified,original,source,{3})


def test_unknown_modes_never_become_ground_access():
    graph=convert(sample()); network=RoadNetwork(graph,graph['sha256'])
    grid={'width':2,'height':2,'crs':'EPSG:25830',
          'transform':[500,0,290000,0,-500,4064500]}
    inputs={'grid':grid, 'fuel_classes':[[1]*2 for _ in range(2)],
            'fuel_valid_mask':[[1]*2 for _ in range(2)]}
    router=HazardRouter(network,inputs,[[10]*2 for _ in range(2)],walk_mask=[[1]*2 for _ in range(2)])
    assert all(not router._edge_allowed(edge,mode) for edge in graph['edges'] for mode in ('crew','engine'))
    # A graph path is still a source topology diagnostic, not a permitted mission.
    assert network.path('1','3')
    for mode in ('crew','engine'):
        routes, _ = router._roads([('1', [])], frozenset(), {'3'}, mode)
        assert '3' not in routes
    # Unknown cell evidence must remain unusable even if geometry crosses it.
    router.walk_mask=[[0]*2 for _ in range(2)]
    assert not router.safe_position(network.nodes['1'],set(),[0,0],mode='crew')


@pytest.mark.parametrize('change', ['geometry','lineage','direction','modes','speed','missing','node'])
def test_validator_rejects_mutated_graph(change):
    source=sample(); graph=convert(source)
    if change == 'geometry': graph['edges'][0]['coordinates'][0][0] += 1
    elif change == 'lineage': graph['edges'][0]['first_segment'] += 999
    elif change == 'direction': graph['edges'][0]['direction'] = 'r' if graph['edges'][0]['direction']=='f' else 'f'
    elif change == 'modes': graph['edges'][0]['allowed_modes']=['engine']
    elif change == 'speed': graph['edges'][0]['speed_kph'] += 1
    elif change == 'missing': graph['edges'].pop()
    else: graph['nodes'].pop()
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    with pytest.raises((ValueError,KeyError)):
        validate(graph,source)


def test_source_pin_and_unsupported_catalog_fail_closed(tmp_path):
    bad=tmp_path/'candidate.gz'; bad.write_bytes(b'not the pinned source')
    with pytest.raises(ValueError,match='pin mismatch'):
        load_candidate(bad)
    with pytest.raises(ValueError,match='catalog pin'):
        post_distances(convert(sample()), {'sha256':'0'*64})


@pytest.mark.skipif(not CANDIDATE.is_file(),reason='private pinned candidate unavailable')
def test_private_full_map_conversion_and_runtime_limits():
    source=load_candidate(); graph=convert(source); stats=validate(graph,source)
    assert stats['source_arcs'] == 168948
    assert stats['source_nodes'] == 92278
    assert stats['nodes'] <= 50000 and stats['edges'] <= 100000
    assert stats['coordinates'] <= 500000
    network = RoadNetwork(graph,graph['sha256'])
    assert network.sha256 == graph['sha256']
    grid = {'width':84, 'height':59, 'crs':'EPSG:25830',
            'transform':[500,0,290000,0,-500,4064500]}
    cells = [[1]*84 for _ in range(59)]
    router = HazardRouter(network, {'grid':grid, 'fuel_classes':cells,
                          'fuel_valid_mask':cells}, cells, walk_mask=cells)
    start = next(iter(network.edges.values()))
    for mode in ('engine','crew'):
        routes, _ = router._roads([(start['source'], [])], frozenset(), {start['target']}, mode)
        if start['source'] != start['target']:
            assert start['target'] not in routes
    # Compare independent Dijkstra costs on the uncontracted source graph for
    # reachable and unreachable retained endpoints, over the entire map.
    origin = min(network.main_component, key=int)
    costs = source_distances(source,int(origin))
    reachable = sorted((n for n in network.nodes if int(n) in costs), key=int)
    for target in reachable[::max(1,len(reachable)//8)][:8]:
        path = network.path(origin,target)
        cost = sum(network.edges[e]['length_m']/network.edges[e]['speed_kph'] for e in path)
        assert cost == pytest.approx(costs[int(target)])
    unreachable = next((n for n in network.nodes if int(n) not in costs),None)
    if unreachable:
        with pytest.raises(ValueError,match='No directed'):
            network.path(origin,unreachable)


def test_post_split_validator_rejects_hiding_new_post_within_same_parent():
    from eval.expanded_post_anchors_v2 import split_graph, validate_split
    source=sample(); original=convert(source)
    for edge in original['edges']: edge['exercise_access_basis']='synthetic'
    original['sha256']=digest({k:v for k,v in original.items() if k!='sha256'})
    assert '2' not in {n['id'] for n in original['nodes']}
    modified=split_graph(original,source,{2})
    by_id={e['id']:e for e in modified['edges']}
    first,second=by_id['10:0:0:f'],by_id['10:1:1:f']
    merged=copy.deepcopy(first)
    merged.update(id='10:0:1:f',target=second['target'],last_segment=1,
                  coordinates=first['coordinates']+second['coordinates'][1:],
                  length_m=first['length_m']+second['length_m'])
    modified['edges']=[e for e in modified['edges'] if e['id'] not in {first['id'],second['id']}]+[merged]
    modified['sha256']=digest({k:v for k,v in modified.items() if k!='sha256'})
    # The added post is still listed and both pieces share the same parent.
    assert '2' in {n['id'] for n in modified['nodes']}
    with pytest.raises(ValueError,match='hides a retained node'):
        validate_split(modified,original,source,{2})


def test_private_versioned_output_never_overwrites(tmp_path):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from eval.expanded_road_runtime_v1 import write_private_bytes, ensure_new_output
    target=tmp_path/'graph.json'
    write_private_bytes(target,b'original')
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): write_private_bytes(target,b'replacement')
    with pytest.raises(FileExistsError): ensure_new_output(target)
    assert target.read_bytes()==b'original'
    symlink=tmp_path/'alias.json'; symlink.symlink_to(target)
    with pytest.raises(FileExistsError): write_private_bytes(symlink,b'replacement')
    assert target.read_bytes()==b'original'
    hardlink=tmp_path/'hardlink.json'; os.link(target,hardlink)
    with pytest.raises(FileExistsError): write_private_bytes(hardlink,b'replacement')
    assert target.read_bytes()==b'original'
    concurrent=tmp_path/'concurrent.json'
    def publish(data):
        try:
            write_private_bytes(concurrent,data)
            return 'created'
        except FileExistsError:
            return 'exists'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish,[b'first',b'second'])) == ['created','exists']
    assert concurrent.read_bytes() in (b'first',b'second')
    assert not list(tmp_path.glob('.*.tmp'))


def test_runtime_command_refuses_existing_output_before_conversion(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    target=tmp_path/'versioned.json'; target.write_bytes(b'old-version')
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'eval/expanded_road_runtime_v1.py','--output',str(target)],
                          cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True)
    assert result.returncode != 0 and 'refusing to overwrite' in result.stderr
    assert target.read_bytes()==b'old-version'


def test_post_bundle_refuses_an_existing_version(tmp_path):
    from eval.expanded_post_anchors_v2 import write_private_bundle
    directory=tmp_path/'version'
    write_private_bundle(directory,{'network.json':b'original','diagnostic.json':b'original-report'})
    with pytest.raises(FileExistsError):
        write_private_bundle(directory,{'network.json':b'replacement','diagnostic.json':b'new-report'})
    assert (directory/'network.json').read_bytes()==b'original'
    assert (directory/'diagnostic.json').read_bytes()==b'original-report'


def test_exercise_command_refuses_existing_version(tmp_path, monkeypatch):
    import sys
    import eval.expanded_road_exercise_v1 as exercise
    target=tmp_path/'network.json'; target.write_bytes(b'old-version')
    monkeypatch.setattr(exercise,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(sys,'argv',['exercise','--opt-in','--output',str(target)])
    with pytest.raises(FileExistsError,match='refusing to overwrite'):
        exercise.main()
    assert target.read_bytes()==b'old-version'


def test_post_command_refuses_existing_version_before_source_load(tmp_path, monkeypatch):
    import sys
    import eval.expanded_post_anchors_v2 as post
    directory=tmp_path/'version'; directory.mkdir()
    output=directory/'network.json'; output.write_bytes(b'old-version')
    monkeypatch.setattr(sys,'argv',['post','--opt-in','--output-dir',str(directory)])
    with pytest.raises(FileExistsError,match='refusing to overwrite'):
        post.main()
    assert output.read_bytes()==b'old-version'


def test_historical_docs_do_not_target_original_graph():
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'docs/expanded-road-runtime-v1.md'
    assert '--output /tmp/expanded-road-runtime-v1/network.json' not in path.read_text()
