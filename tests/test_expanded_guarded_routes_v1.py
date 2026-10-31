"""Road-only guard seam; source cell access is never inferred from roads."""
import copy
import json
from pathlib import Path

import pytest
from shapely.geometry import LineString
from eval import expanded_guarded_routes_v1 as routes_module
from eval.expanded_guarded_routes_v1 import (ACCESS_VERSION, GuardedRoadRoutes,
                                              check_anchored_draft, check_evidence)
from eval.expanded_road_runtime_v1 import CANDIDATE, convert, load_candidate
from envs.wildfire_response.regional.ops.generator import digest


@pytest.fixture(autouse=True)
def synthetic_only_test_pair(monkeypatch):
    """Unit fixtures use a test-local trust seam; production has one fixed pair.

    Candidate-shaped inputs always execute the actual reviewed-pair check,
    including the source-subset attack below.
    """
    reviewed=routes_module._verify_reviewed_pair
    def fixture_pair(graph,graph_sha,source,source_sha):
        if source.get('version') is None:
            if (source_sha!=digest(source) or graph_sha!=graph.get('sha256') or
                graph_sha!=digest({k:v for k,v in graph.items() if k!='sha256'})):
                raise ValueError('test fixture pair mismatch')
            return 'synthetic-test-only'
        return reviewed(graph,graph_sha,source,source_sha)
    monkeypatch.setattr(routes_module,'_verify_reviewed_pair',fixture_pair)


def synthetic(cross=False):
    points={1:[290100.,4064000.],2:[290300.,4064000.],3:[290450.,4064000.]}
    ways=[dict(id=10,nodes=[1,2],tags={'highway':'residential'}),
          dict(id=20,nodes=[2,3],tags={'highway':'residential'})]
    if cross:
        points.update({4:[290200.,4063900.],5:[290200.,4064100.]})
        ways.append(dict(id=30,nodes=[4,5],tags={'highway':'residential'}))
    arcs=[]
    for way in ways:
        for i,(a,b) in enumerate(zip(way['nodes'],way['nodes'][1:])):
            for direction in ('f','r'):
                arcs.append(dict(way=way['id'],segment=i,direction=direction,
                    source=a if direction=='f' else b,target=b if direction=='f' else a))
    source=dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=ways,arcs=arcs)
    graph=convert(source)
    for edge in graph['edges']: edge['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    return source,graph


def evidence(role='synthetic_fixture_only', access='accessible', surface='land',width=2,height=2):
    return {'version':ACCESS_VERSION,'role':role,
        'grid':{'width':width,'height':height,'crs':'EPSG:25830',
                'transform':[500,0,290000,0,-500,4064500]},
        'access':[[access]*width for _ in range(height)],'surface':[[surface]*width for _ in range(height)]}


def build(source,graph,ev,allowed=None):
    fill=None if ev['role']=='source_unknown' else 100
    cells=[[fill]*ev['grid']['width'] for _ in range(ev['grid']['height'])]
    whitelist=[e['id'] for e in graph['edges']] if allowed is None else allowed
    return GuardedRoadRoutes(graph,graph['sha256'],source,digest(source),ev,digest(ev),cells,
                             allowed_edge_ids=whitelist,opt_in=True)


@pytest.mark.parametrize('mode',['crew','engine'])
@pytest.mark.parametrize('kind',['dispatch','return','evacuation'])
def test_entry_points_only_return_full_allowed_road_legs(mode,kind):
    source,graph=synthetic(); router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    kw={'burning':set(),'wind':[0,0]}
    if kind=='dispatch': result=router.dispatch_options(a,b,mode=mode,**kw)
    elif kind=='return': result=router.return_options(a,b,mode=mode,**kw)
    else: result=router.evacuation_options(a,b,**kw)
    assert result['status']=='reachable' and result['options']
    edges={e['id']:e for e in graph['edges']}
    for option in result['options']:
        journey=option['journey']
        assert journey['origin']==a and journey['destination']==b
        assert journey['legs'] and all(leg['mode']=='road' for leg in journey['legs'])
        assert all(leg['coordinates']==edges[leg['edge_id']]['coordinates'] for leg in journey['legs'])
        assert all('engine' in edges[leg['edge_id']]['allowed_modes'] for leg in journey['legs'])
        if mode=='crew' and kind!='evacuation':
            assert all('crew' in edges[leg['edge_id']]['allowed_modes'] for leg in journey['legs'])
        assert 'unverified' in result['scope']


def test_crew_and_engine_routes_have_distinct_ids():
    source,graph=synthetic(); router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    crew=router.dispatch_options(a,b,mode='crew',burning=set(),wind=[0,0])['options'][0]['id']
    engine=router.dispatch_options(a,b,mode='engine',burning=set(),wind=[0,0])['options'][0]['id']
    assert crew!=engine


@pytest.mark.parametrize('kind',['dispatch','return','evacuation'])
def test_crossing_origin_fails_closed_for_all_entries(kind):
    source,graph=synthetic(cross=True); router=build(source,graph,evidence()); crossing=[290200.,4064000.]; dest=[290450.,4064000.]
    kw={'burning':set(),'wind':[0,0]}
    if kind=='dispatch': result=router.dispatch_options(crossing,dest,mode='engine',**kw)
    elif kind=='return': result=router.return_options(crossing,dest,mode='engine',emergency_egress=True,**kw)
    else: result=router.evacuation_options(crossing,dest,**kw)
    assert result['status']=='unmapped' and result['options']==[] and result['reason']=='ambiguous_source_segments'


def test_crossing_destination_and_mid_edge_return_are_unknown():
    source,graph=synthetic(cross=True); router=build(source,graph,evidence()); a=[290100.,4064000.]
    cross=router.return_options(a,[290200.,4064000.],mode='engine',burning=set(),wind=[0,0])
    assert cross['reason']=='ambiguous_source_segments'
    mid=router.return_options([290150.,4064000.],a,mode='engine',burning=set(),wind=[0,0])
    assert mid['reason']=='endpoint_not_exact_source_node'


def test_unknown_access_and_empty_whitelist_never_gain_road_access():
    source,graph=synthetic(); unknown=evidence('source_unknown','unknown','unknown')
    router=build(source,graph,unknown)
    result=router.dispatch_options([290100.,4064000.],[290450.,4064000.],mode='engine',burning=set(),wind=[0,0])
    assert result['status']=='unmapped' and result['options']==[]
    invalid=evidence('source_unknown','accessible','land')
    with pytest.raises(ValueError,match='source access must remain unknown'):
        check_evidence(invalid,digest(invalid))
    empty=build(source,graph,evidence(),allowed=[])
    assert empty.return_options([290100.,4064000.],[290450.,4064000.],mode='engine',burning=set(),wind=[0,0])['reason']=='empty_road_whitelist'


def test_unknown_crossed_cell_not_overridden_by_road():
    source,graph=synthetic(); ev=evidence(); ev['access'][0][0]='unknown'
    router=build(source,graph,ev)
    # Road lies along a cell boundary; both adjacent cells must be supported.
    out=router.dispatch_options([290100.,4064000.],[290450.,4064000.],mode='engine',burning=set(),wind=[0,0])
    assert out['status']=='unmapped' and not out['options']


def test_no_walk_or_connector_even_if_router_returns_one(monkeypatch):
    source,graph=synthetic(); router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    fake={'status':'reachable','options':[{'id':'fake','journey':{'origin':a,'destination':b,
        'legs':[{'mode':'walk','coordinates':[a,b],'edge_id':None}]}}]}
    monkeypatch.setattr(router._guard,'options',lambda *a,**k: copy.deepcopy(fake))
    assert router.evacuation_options(a,b,burning=set(),wind=[0,0])['options']==[]
    edge=graph['edges'][0]
    fake['options'][0]['journey']['legs']=[{'mode':'road','edge_id':edge['id'],
        'coordinates':[a,b],'destination_node':edge['target']}]
    assert router.dispatch_options(a,b,mode='engine',burning=set(),wind=[0,0])['options']==[]


def test_missing_crew_permission_or_corridor_is_never_allowed():
    source,graph=synthetic()
    for edge in graph['edges']:
        if edge['source_way']==20: edge['allowed_modes']=['engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    assert not router.dispatch_options(a,b,mode='crew',burning=set(),wind=[0,0])['options']
    assert router.dispatch_options(a,b,mode='engine',burning=set(),wind=[0,0])['options']
    allowed=[e['id'] for e in graph['edges'] if e['source_way']==10]
    router=build(source,graph,evidence(),allowed=allowed)
    assert not router.evacuation_options(a,b,burning=set(),wind=[0,0])['options']


def test_rejects_unpinned_evidence_and_unapproved_opt_in():
    source,graph=synthetic(); ev=evidence()
    with pytest.raises(ValueError,match='identity'):
        check_evidence(ev,'0'*64)
    with pytest.raises(ValueError,match='opt-in'):
        GuardedRoadRoutes(graph,graph['sha256'],source,digest(source),ev,digest(ev),[[100]*2]*2,
                          allowed_edge_ids=[],opt_in=False)


PRIVATE=Path('/tmp/expanded-post-anchors-v4')
@pytest.mark.skipif(not CANDIDATE.exists() or not (PRIVATE/'network.json').exists() or
                    not (PRIVATE/'scenario-drafts.json').exists(),reason='private pinned source unavailable')
def test_real_source_posts_and_crossing_remain_unknown_without_cell_evidence():
    graph=json.loads((PRIVATE/'network.json').read_bytes()); source=load_candidate()
    drafts=json.loads((PRIVATE/'scenario-drafts.json').read_bytes())
    assert graph['sha256']=='97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525'
    assert drafts['sha256']=='46299ebe3e2a1529121759771afd6b68c135ed62e7e94da01eca792f7ba19a87'
    anchors=check_anchored_draft(drafts,drafts['sha256'],graph['sha256'],graph)
    assert anchors['center-0'][0]=='321315386' and anchors['center-1'][0]=='6119295366'
    ev=evidence('source_unknown','unknown','unknown',84,59)
    router=build(source,graph,ev,allowed=[e['id'] for e in graph['edges']])
    a,b=anchors['center-0'][1],anchors['center-1'][1]
    assert not router.dispatch_options(a,b,mode='engine',burning=set(),wind=[0,0])['options']
    assert not router.return_options(b,a,mode='crew',burning=set(),wind=[0,0])['options']
    assert not router.evacuation_options(a,b,burning=set(),wind=[0,0])['options']
    edges={e['id']:e for e in graph['edges']}
    x=LineString(edges['1148795467:0:4:f']['coordinates']).intersection(
        LineString(edges['23359994:0:9:f']['coordinates']))
    crossing=[x.x,x.y]
    for method in (lambda:router.dispatch_options(crossing,b,mode='engine',burning=set(),wind=[0,0]),
                   lambda:router.return_options(crossing,b,mode='crew',burning=set(),wind=[0,0]),
                   lambda:router.evacuation_options(crossing,b,burning=set(),wind=[0,0])):
        result=method(); assert result['reason']=='ambiguous_source_segments' and not result['options']
    # The same source graph can be exercised with a separately declared,
    # explicitly synthetic access fixture. This is not source access evidence.
    import gc
    del router
    gc.collect()
    fixture=evidence('synthetic_fixture_only','accessible','land',84,59)
    router=build(source,graph,fixture,allowed=[e['id'] for e in graph['edges']])
    for mode in ('engine','crew'):
        result=router.dispatch_options(a,b,mode=mode,burning=set(),wind=[0,0])
        assert result['status']=='reachable' and result['options']
        assert result['cell_evidence_role']=='synthetic_fixture_only'
        assert all(leg['mode']=='road' for leg in result['options'][0]['journey']['legs'])
        returning=router.return_options(b,a,mode=mode,burning=set(),wind=[0,0])
        assert returning['status']=='reachable' and returning['options']
    evacuation=router.evacuation_options(a,b,burning=set(),wind=[0,0])
    assert evacuation['status']=='reachable' and evacuation['options']


def test_anchored_draft_requires_pinned_graph():
    source,graph=synthetic()
    case={'version':'expanded-synthetic-post-anchors-v4','profile':{'allowed_road_edge_ids':[]},'resources':[]}
    case['sha256']=digest(case)
    draft={'version':'expanded-synthetic-post-anchors-v4','status':'private-unqualified-not-runnable',
           'exercise_network_sha256':graph['sha256'],'cases':[case]}
    draft['sha256']=digest(draft)
    altered=copy.deepcopy(graph);altered['nodes'][0]['position'][0]+=1
    with pytest.raises(ValueError,match='untrusted anchored graph'):
        check_anchored_draft(draft,draft['sha256'],graph['sha256'],altered)


def test_one_way_vehicle_routes_do_not_reverse():
    source,graph=synthetic()
    source['ways'][1]['tags']['oneway']='yes'
    source['arcs']=[a for a in source['arcs'] if not (a['way']==20 and a['direction']=='r')]
    graph=convert(source)
    for edge in graph['edges']: edge['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    assert router.dispatch_options(a,b,mode='engine',burning=set(),wind=[0,0])['options']
    assert not router.return_options(b,a,mode='engine',burning=set(),wind=[0,0])['options']


def test_swept_cell_access_is_rechecked_before_returning_option(monkeypatch):
    source,graph=synthetic(); ev=evidence(); ev['access'][0][0]='unknown'
    router=build(source,graph,ev); a=[290100.,4064000.]; b=[290450.,4064000.]
    edges={e['id']:e for e in graph['edges']}
    legs=[]
    for ident in ('10:0:0:f','20:0:0:f'):
        edge=edges[ident]
        legs.append({'mode':'road','edge_id':ident,'coordinates':copy.deepcopy(edge['coordinates']),
                     'destination_node':edge['target']})
    fake={'status':'reachable','options':[{'id':'fake','journey':{'origin':a,'destination':b,'legs':legs}}]}
    monkeypatch.setattr(router._guard,'options',lambda *args,**kwargs:fake)
    result=router.evacuation_options(a,b,burning=set(),wind=[0,0])
    assert result['status']=='unmapped' and not result['options']


@pytest.mark.parametrize('kind',['dispatch','return','evacuation'])
def test_dynamic_exclusions_and_nonreusable_hazard_input_fail_closed(kind):
    source,graph=synthetic(); router=build(source,graph,evidence()); a=[290100.,4064000.]; b=[290450.,4064000.]
    params={'burning':set(),'wind':[0,0],'excluded_cells':[(0,0)]}
    if kind=='dispatch': result=router.dispatch_options(a,b,mode='engine',**params)
    elif kind=='return': result=router.return_options(a,b,mode='crew',**params)
    else: result=router.evacuation_options(a,b,**params)
    assert result['status']=='unmapped' and not result['options']
    with pytest.raises(ValueError,match='reusable'):
        router.evacuation_options(a,b,burning=iter([(1,1)]),wind=[0,0])


def test_emergency_return_keeps_guard_and_no_reentry_semantics():
    a=[290750.,4063250.]; b=[292750.,4063250.]
    source={'nodes':[{'id':1,'position_m':a},{'id':2,'position_m':b}],
        'ways':[{'id':10,'nodes':[1,2],'tags':{'highway':'residential'}}],
        'arcs':[{'way':10,'segment':0,'direction':'f','source':1,'target':2},
                {'way':10,'segment':0,'direction':'r','source':2,'target':1}]}
    graph=convert(source)
    for edge in graph['edges']: edge['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    router=build(source,graph,evidence(width=8,height=8))
    ordinary=router.return_options(a,b,mode='engine',burning={(0,2)},wind=[0,0])
    assert ordinary['status']=='unmapped' and not ordinary['options']
    emergency=router.return_options(a,b,mode='engine',burning={(0,2)},wind=[0,0],emergency_egress=True)
    assert emergency['status']=='reachable' and emergency['options']
    journey=emergency['options'][0]['journey']
    assert journey['emergency_egress']['version']=='return-only-buffer-exit-v1'
    assert all(leg['mode']=='road' for leg in journey['legs'])


def test_reviewed_pair_rejects_rehashed_source_omitting_crossing(monkeypatch):
    """A complete-source trust anchor cannot be replaced by a caller hash."""
    source,_=synthetic(cross=True)
    # The omitted road crosses a retained graph node, so an origin there would
    # otherwise pass the exact-node endpoint gate as well as the origin guard.
    for node in source['nodes']:
        if node['id'] in (4,5): node['position_m'][0]=290300.
    _,graph=synthetic(cross=False)  # graph intentionally omits the other road
    source.update(version='extended-access-directed-road-candidate-v2',
        status='source_candidate_not_operational_access',crs='EPSG:25830',
        bounds=[290000,4035000,332000,4064500],
        source_manifest_sha256=routes_module.REVIEWED_PAIR_MANIFEST['source_manifest_sha256'],
        restriction_relations=[],boundary_evidence=[],restriction_way_evidence=[],restriction_node_evidence=[])
    manifest=copy.deepcopy(routes_module.REVIEWED_PAIR_MANIFEST)
    manifest['source_sha256']=digest(source)
    manifest['graph_sha256']=graph['sha256']
    manifest['source_counts']={k:len(source[k]) for k in manifest['source_counts']}
    # Test harness stands in for a separately reviewed immutable manifest. The
    # second constructor call cannot alter that trusted pin.
    trusted_anchor=digest(manifest)
    def reviewed_fixture(graph,graph_sha,source,source_sha):
        return routes_module._verify_pair_against_anchor(graph,graph_sha,source,source_sha,
                                                        manifest,trusted_anchor)
    monkeypatch.setattr(routes_module,'_verify_reviewed_pair',reviewed_fixture)
    ev=evidence();router=build(source,graph,ev)
    crossing=[290300.,4064000.];end=[290450.,4064000.]
    assert router.dispatch_options(crossing,end,mode='engine',burning=set(),wind=[0,0])['reason']=='ambiguous_source_segments'
    subset=copy.deepcopy(source)
    subset['ways']=[w for w in subset['ways'] if w['id']!=30]
    subset['arcs']=[a for a in subset['arcs'] if a['way']!=30]
    subset['nodes']=[n for n in subset['nodes'] if n['id'] not in (4,5)]
    assert digest(subset)!=digest(source)
    # The lower-level guard alone accepts the rehashed subset and misses the
    # omitted crossing; that is why the reviewed pair is required here.
    from eval.expanded_origin_guard_v1 import GuardedOriginRouter
    cells=[[100]*2 for _ in range(2)]
    lower=GuardedOriginRouter(graph,graph['sha256'],subset,digest(subset),
        {'grid':ev['grid']},cells,walk_mask=[[0]*2 for _ in range(2)],
        allowed_edge_ids=[e['id'] for e in graph['edges']])
    assert lower.assess_origin(crossing)['status']=='unambiguous'
    legacy=lower.options(crossing,[0,1],mode='engine',burning=set(),wind=[0,0],target_metric=end)
    assert legacy['status']=='reachable' and legacy['options']
    # The caller supplies a valid new hash for the reduced source; the graph
    # remains identical and every graph edge is represented in that subset.
    with pytest.raises(ValueError,match='complete source differ from reviewed pair'):
        build(subset,graph,ev)
    with pytest.raises(ValueError,match='complete source differ from reviewed pair'):
        GuardedRoadRoutes(graph,graph['sha256'],subset,digest(source),ev,digest(ev),[[100]*2]*2,
                          allowed_edge_ids=[e['id'] for e in graph['edges']],opt_in=True)
    changed_graph=copy.deepcopy(graph)
    changed_graph['edges'][0]['allowed_modes']=[]
    changed_graph['sha256']=digest({k:v for k,v in changed_graph.items() if k!='sha256'})
    with pytest.raises(ValueError,match='complete source differ from reviewed pair'):
        build(source,changed_graph,ev)
    # Rehashing a tampered manifest does not change the independent anchor.
    manifest['source_sha256']=digest(subset)
    manifest['source_counts']={k:len(subset[k]) for k in manifest['source_counts']}
    with pytest.raises(ValueError,match='manifest pin mismatch'):
        build(subset,graph,ev)


def test_unreviewed_graph_and_source_pins_are_rejected():
    source,graph=synthetic()
    # A fixture with a self-consistent pair is not a production candidate.
    source['version']='not-the-reviewed-source'
    ev=evidence()
    with pytest.raises(ValueError,match='reviewed pair'):
        GuardedRoadRoutes(graph,graph['sha256'],source,digest(source),ev,digest(ev),[[100]*2]*2,
                          allowed_edge_ids=[e['id'] for e in graph['edges']],opt_in=True)


def test_rebinding_public_pair_digest_does_not_change_fixed_anchor(monkeypatch):
    source,graph=synthetic()
    source['version']='extended-access-directed-road-candidate-v2'
    altered=copy.deepcopy(routes_module.REVIEWED_PAIR_MANIFEST)
    altered['source_sha256']=digest(source); altered['graph_sha256']=graph['sha256']
    monkeypatch.setattr(routes_module,'REVIEWED_PAIR_MANIFEST',altered)
    # A caller changing the public variable to a rehash cannot change the
    # verifier's independently captured trusted digest.
    monkeypatch.setattr(routes_module,'TRUSTED_PAIR_SHA256',digest(altered))
    ev=evidence()
    with pytest.raises(ValueError,match='manifest pin mismatch'):
        GuardedRoadRoutes(graph,graph['sha256'],source,digest(source),ev,digest(ev),[[100]*2]*2,
                          allowed_edge_ids=[],opt_in=True)
