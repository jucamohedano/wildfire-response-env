"""Guarded synthetic-source-adapted host integration; no source access claim."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_guarded_incident_integration():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy,json
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import convert
    from eval.expanded_guarded_routes_v1 import ACCESS_VERSION
    from eval import expanded_guarded_routes_v1 as route_module
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_guarded_v1 import GuardedIncidentV1,CATALOG_VERSION
    from wildfire_expanded_guarded_v1 import host as host_module
    # The production guard accepts only the reviewed real source/graph pair.
    # This isolated subprocess exercises lifecycle behavior with a test-local
    # synthetic pair; the source-origin and complete-edge checks still run.
    def synthetic_pair(graph,graph_sha,source,source_sha):
        if (source_sha!=digest(source) or graph_sha!=graph.get('sha256')
                or graph_sha!=digest({k:v for k,v in graph.items() if k!='sha256'})):
            raise ValueError('synthetic pair mismatch')
        return 'synthetic-test-only'
    route_module._verify_reviewed_pair=synthetic_pair
    # This isolated test process substitutes both the verifier binding and
    # anchor; production exposes no constructor override or test mode.
    host_module._check_pair_binding=lambda:None
    host_module._REVIEWED_PAIR_SHA256='synthetic-test-only'
    # Test-process-only stand-in for the independent reviewed pair. The
    # production constructor has no caller-controlled trust-anchor option.
    host_module._check_pair_binding=lambda:None
    host_module._REVIEWED_PAIR_SHA256='synthetic-test-only'
    base,_=fixture_inputs(); n=base['grid']['width']; t=base['grid']['transform']
    def cell(x,y): return [t[2]+(x+.5)*500,t[5]-(y+.5)*500]
    points={i:cell(2*i,2) for i in range(1,6)}
    ways=[dict(id=10+i,nodes=[i,i+1],tags={'highway':'residential'}) for i in range(1,5)]
    def make_source(cross=False):
        ps=copy.deepcopy(points);ws=copy.deepcopy(ways)
        if cross:
            a=points[1];ps[6]=[a[0],a[1]-100];ps[7]=[a[0],a[1]+100]
            ws.append(dict(id=30,nodes=[6,7],tags={'highway':'residential'}))
        arcs=[]
        for way in ws:
            a,b=way['nodes']
            for direction in ('f','r'):
                arcs.append(dict(way=way['id'],segment=0,direction=direction,
                    source=a if direction=='f' else b,target=b if direction=='f' else a))
        source=dict(nodes=[dict(id=i,position_m=p) for i,p in ps.items()],ways=ws,arcs=arcs)
        # Keep the crossing source segment in the ambiguity index while the
        # runtime graph retains only the original mapped roads.
        graph_source=source if not cross else dict(nodes=source['nodes'][:5],ways=source['ways'][:4],
                                                   arcs=[a for a in arcs if a['way']!=30])
        graph=convert(graph_source)
        for edge in graph['edges']:edge['allowed_modes']=['crew','engine']
        graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
        return source,graph
    source,graph=make_source()
    doc=dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)],surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']),elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    doc['surface'][16][17]='mixed';doc['access'][16][17]='unknown'
    case=dict(id='guarded-synthetic',seed=7,initial_fire=[[16,16]],wind=[0.,0.],centers=['center-0'],
        warmup_minutes=2,weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
        relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                source_catalog_sha256='e'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(*,d=doc,s=source,g=graph,role='synthetic_fixture_only',evidence_override=None,
                initial_exposed=False,handover_exposure=None):
        inp=api.load_inputs(d,api._hash(d),expected_source_pins=d['source_pins'],opt_in=True)
        # Test-only mixed-source trust stand-in; no claim about the actual map.
        mixed=MixedInputsV2(inp,((17,16,.8,.5),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        evidence=dict(version=ACCESS_VERSION,role=role,grid=copy.deepcopy(d['grid']),
            surface=[['unknown' if v=='unknown' else 'water' if v=='sea' else v for v in row] for row in d['surface']],
            access=copy.deepcopy(d['access']))
        if role=='source_unknown':evidence['access']=[['unknown']*n for _ in range(n)]
        if evidence_override is not None:evidence=evidence_override
        roster=dict(version=CATALOG_VERSION,role='synthetic_source_adapted_not_historical',case_id=case['id'],
            inputs_sha256=inp.document_sha256,graph_sha256=g['sha256'],
            resources=[dict(id='crew',kind='crew',position=points[1],work_remaining=20,water_litres=0)],
            groups=[dict(id='cohort',count=6,position=points[2],initial_exposed=initial_exposed,handover_exposure=handover_exposure)],
            destinations=[dict(id='safe',capacity=10,position=points[4])])
        return GuardedIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            graph=g,expected_graph_sha256=g['sha256'],source=s,expected_source_sha256=digest(s),
            cell_evidence=evidence,expected_evidence_sha256=digest(evidence),allowed_edge_ids=[e['id'] for e in g['edges']],
            catalog=roster,expected_catalog_sha256=api._hash(roster),containment_config=config,pressure_scale=1,opt_in=True)
    a=factory()
    option=a.route_options('crew',points[3])['options'][0]
    assert set(option)=={'id','travel_minutes'}  # source geometry is private
    guard=a._routes.dispatch_options(points[1],points[3],mode='crew',burning=case['initial_fire'],wind=case['wind'])
    edges={e['id']:e for e in graph['edges']}
    assert all(l['mode']=='road' and l['coordinates']==edges[l['edge_id']]['coordinates']
               for l in guard['options'][0]['journey']['legs'])
    a.dispatch('crew',points[3],option['id'])
    assert a._ops.resources['crew']['state']=='mobilizing'
    a.advance(2)
    assert a._ops.resources['crew']['state']=='travel'
    assert a._ops.resources['crew']['position'] not in list(points.values())
    mid=copy.deepcopy(a._ops.resources['crew']['position'])
    a.release('crew')  # mid-edge: no invented partial return or teleport
    assert a._ops.resources['crew']['state']=='needs_assistance' and a._ops.resources['crew']['position']==mid
    cp=json.loads(json.dumps(a.checkpoint()))
    b=factory();b.restore(cp)
    corrupted=copy.deepcopy(cp); corrupted['operations']['resources']['crew']['base_position']=[0,0]
    corrupted['sha256']=api._hash({k:v for k,v in corrupted.items() if k!='sha256'})
    try:b.restore(corrupted)
    except ValueError:pass
    else:raise AssertionError('rehash cannot change resource base')
    b.restore(cp)
    assert a.advance(3)==b.advance(3)
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    transcript=a.transcript();fresh=factory()
    assert fresh.replay(transcript,expected_sha256=api._hash(transcript),
        expected_identity_sha256=transcript['identity_sha256'])==a.situation()
    assert api._encoded(fresh.checkpoint())==api._encoded(a.checkpoint())
    # Return from an exact node uses the guarded full-edge route.
    returning=factory(); option=returning.route_options('crew',points[3])['options'][0]
    returning.dispatch('crew',points[3],option['id'])
    for _ in range(20):
        returning.advance()
        if returning._ops.resources['crew']['state']=='on_scene':break
    assert returning._ops.resources['crew']['position']==points[3]
    returning.release('crew')
    assert returning._ops.resources['crew']['state']=='returning'
    assert returning._valid_journey(returning._ops.resources['crew']['journey'],'crew')
    for _ in range(20):
        returning.advance()
        if returning._ops.resources['crew']['state']=='available':break
    assert returning._ops.resources['crew']['position']==points[1]
    # Evacuation follows a complete road-only route after order/ack delays.
    evac=factory(); opt=evac.evacuation_options('cohort','safe')['options'][0]
    evac.protect('cohort','evacuate',destination_id='safe',option_id=opt['id'])
    assert evac._ops.groups['cohort']['position']==points[2]
    before=json.loads(json.dumps(evac.checkpoint())); clone=factory();clone.restore(before)
    for _ in range(40):
        assert evac.advance()==clone.advance()
        if evac._ops.groups['cohort']['status']=='arrived':break
    assert evac._ops.groups['cohort']['position']==points[4] and evac._ops.groups['cohort']['status']=='arrived'
    assert api._encoded(evac.checkpoint())==api._encoded(clone.checkpoint())
    assert not any(r['kind']=='evacuation_arrived' for r in evac.reports(limit=200))  # not yet delivered
    evac.advance(8)
    assert any(r['kind']=='evacuation_arrived' for r in evac.reports(limit=200))
    # Current fire invalidates a route selected using stale public information.
    hazard=factory();opt=hazard.route_options('crew',points[3])['options'][0]
    assert hazard._fire.change({'ignite':[[6,2]]})
    hazard.dispatch('crew',points[3],opt['id'])
    assert hazard._ops.resources['crew']['state']=='available'
    hazard.advance(6)
    assert any(r['kind']=='dispatch_result' and r['payload']['accepted'] is False for r in hazard.reports(limit=200))
    # Fire reaches an already-authorized travel segment. The swept movement
    # check stops the unit without advancing its physical position.
    enroute=factory(); opt=enroute.route_options('crew',points[3])['options'][0]
    enroute.dispatch('crew',points[3],opt['id']);enroute.advance(2)
    before=copy.deepcopy(enroute._ops.resources['crew']['position'])
    transit=json.loads(json.dumps(enroute.checkpoint()))
    broken=copy.deepcopy(transit)
    broken['operations']['resources']['crew']['journey']['legs'][0]['coordinates'][0][0]+=1
    broken['sha256']=api._hash({k:v for k,v in broken.items() if k!='sha256'})
    restore_probe=factory()
    try:restore_probe.restore(broken)
    except ValueError:pass
    else:raise AssertionError('partial or invented edge accepted from checkpoint')
    restore_probe.restore(transit)
    assert api._encoded(transit)==api._encoded(restore_probe.checkpoint())
    assert enroute._fire.change({'ignite':[[4,2]]})
    enroute.advance()
    assert enroute._ops.resources['crew']['state']=='blocked'
    assert enroute._ops.resources['crew']['position']==before
    # A newly hazardous evacuation corridor also blocks at the true location.
    blocked=factory();opt=blocked.evacuation_options('cohort','safe')['options'][0]
    blocked.protect('cohort','evacuate',destination_id='safe',option_id=opt['id'])
    for _ in range(20):
        blocked.advance()
        if blocked._ops.groups['cohort']['status']=='evacuating': break
    assert blocked._ops.groups['cohort']['status']=='evacuating'
    position=copy.deepcopy(blocked._ops.groups['cohort']['position'])
    assert blocked._fire.change({'ignite':[[6,2]]})
    blocked.advance()
    assert blocked._ops.groups['cohort']['status']=='blocked'
    assert blocked._ops.groups['cohort']['position']==position
    # Identical declared handover knowledge does not disclose private inherited exposure.
    hidden=factory(initial_exposed=True);clear=factory(initial_exposed=False)
    assert hidden.situation()==clear.situation() and hidden.reports()==clear.reports()
    assert hidden.situation()['population'][0]['exposed'] is None
    assert hidden._ops.groups['cohort']['exposed'] and not clear._ops.groups['cohort']['exposed']
    assert hidden.private_handover_exposure()=={'cohort':6} and clear.private_handover_exposure()=={'cohort':0}
    assert hidden.protect('cohort','shelter')==clear.protect('cohort','shelter')
    for _ in range(3):
        assert hidden.advance()==clear.advance()
        assert hidden.reports()==clear.reports()
    assert hidden.situation()['population'][0]['exposed'] is None
    # Unknown source access cannot authorize road travel or become full-cell access.
    unknown=factory(role='source_unknown')
    assert unknown.route_options('crew',points[3])['status']=='unmapped'
    assert not unknown.evacuation_options('cohort','safe')['options']
    assert unknown._ops.resources['crew']['state']=='needs_assistance'
    sparse=copy.deepcopy(doc);sparse['access'][2][4]='unknown'
    ev=dict(version=ACCESS_VERSION,role='synthetic_fixture_only',grid=copy.deepcopy(sparse['grid']),
            surface=copy.deepcopy(sparse['surface']),access=[['accessible']*n for _ in range(n)])
    try:factory(d=sparse,evidence_override=ev)
    except ValueError:pass
    else:raise AssertionError('road evidence promoted unknown input access')
    # Shelter follows actual delivery/ack/execution, not an immediate state change.
    shelter=factory();shelter.protect('cohort','shelter')
    assert shelter._ops.groups['cohort']['status']=='at_home'
    shelter.advance(8)
    assert shelter._ops.groups['cohort']['status']=='sheltering'
    # Complete physical fire censor suppresses communications and population,
    # while the joint checkpoint retains matching operation and fire clocks.
    import random
    from eval import incident_weather_v1 as weather
    class ZeroRandom(random.Random):
        def random(self):return 0.
    boundary=factory();assert boundary._fire.change({'ignite':[[1,1]]})
    row=weather.conditions(boundary._weather._profile,1);row.update(minute=0,received_at=0)
    profile=weather.WeatherProfile.from_records([row])
    prior=weather.initial_state(profile,moisture_proxy=boundary._weather._weather_state['moisture_proxy'])
    f=boundary._fire._fire;f.wind=tuple(row['wind'])
    f.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
    f.cells[1][1].update(heat=1.,age=f.spread_interval(0,-1)-1);f.rng=ZeroRandom(7)
    assert boundary.advance()['status']=='censored'
    assert 'population' not in boundary.situation() and boundary._ops.last_minute==1
    try:boundary.reports()
    except api.CensoredError:pass
    else:raise AssertionError('censored reports leaked')
    saved=json.loads(json.dumps(boundary.checkpoint()));restored=factory();restored.restore(saved)
    assert api._encoded(saved)==api._encoded(restored.checkpoint())
    cold=copy.deepcopy(doc);cold['surface'][16][15]='unknown';cold['access'][16][15]='unknown'
    cold_host=factory(d=cold)
    assert cold_host.situation()['status']=='censored' and cold_host._ops.last_minute is None
    # An exception after the host begins a minute poisons all actor access;
    # a previously trusted checkpoint is required to recover.
    faulty=factory();snapshot=json.loads(json.dumps(faulty.checkpoint()))
    def fault():raise RuntimeError('synthetic in-flight fault')
    faulty._weather.step=fault
    try:faulty.advance()
    except RuntimeError:pass
    else:raise AssertionError('in-flight failure accepted')
    try:faulty.situation()
    except RuntimeError:pass
    else:raise AssertionError('indeterminate state exposed')
    del faulty._weather.step
    faulty.restore(snapshot)
    assert api._encoded(snapshot)==api._encoded(faulty.checkpoint())
    # A source segment crossing an exact graph node is still ambiguous.
    crossed_source,crossed_graph=make_source(True)
    ambiguous=factory(s=crossed_source,g=crossed_graph)
    assert ambiguous.route_options('crew',points[3])['status']=='unmapped'
    assert not ambiguous.route_options('crew',points[3])['options']
    assert not any('weather_schedule' in json.dumps(x) or 'rng' in json.dumps(x)
                   for x in (a.situation(),a.reports(limit=200),a.transcript()))


if __name__=='__main__': main()
