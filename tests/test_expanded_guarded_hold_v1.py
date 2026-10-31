"""Synthetic guarded hold, source identity and recovery integration."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_guarded_hold():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy,json,random
    from unittest.mock import patch
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import convert
    from eval.expanded_guarded_routes_v1 import ACCESS_VERSION
    from eval import expanded_guarded_routes_v1 as route_module
    from eval import incident_weather_v1 as weather
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_guarded_v1 import GuardedIncidentV1,CATALOG_VERSION
    from wildfire_expanded_guarded_v1.hold_v1 import GuardedHoldV1,EVIDENCE_VERSION
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
    base,_=fixture_inputs();n=base['grid']['width'];t=base['grid']['transform']
    cell=lambda x,y:[t[2]+(x+.5)*500,t[5]-(y+.5)*500]
    points={i:cell(2*i,2) for i in range(1,6)}
    ways=[dict(id=10+i,nodes=[i,i+1],tags={'highway':'residential'}) for i in range(1,5)]
    arcs=[]
    for w in ways:
        a,b=w['nodes']
        for direction in ('f','r'):
            arcs.append(dict(way=w['id'],segment=0,direction=direction,
                source=a if direction=='f' else b,target=b if direction=='f' else a))
    source=dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=ways,arcs=arcs)
    graph=convert(source)
    for e in graph['edges']:e['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    document=dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)],surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']),elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17]='mixed';document['access'][16][17]='unknown'
    case=dict(id='guarded-hold-synthetic',seed=7,initial_fire=[[16,16]],wind=[0.,0.],centers=['center-0'],
        warmup_minutes=2,weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
        relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                source_catalog_sha256='e'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(*,kind='engine',work=2,water=600,unknown=False,sites=True):
        inp=api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
        # Test-only synthetic trust anchor, not source validation.
        mixed=MixedInputsV2(inp,((17,16,.8,.5),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        ev=dict(version=ACCESS_VERSION,role='source_unknown' if unknown else 'synthetic_fixture_only',
            grid=copy.deepcopy(document['grid']),surface=copy.deepcopy(document['surface']),
            access=[['unknown']*n for _ in range(n)] if unknown else copy.deepcopy(document['access']))
        roster=dict(version=CATALOG_VERSION,role='synthetic_source_adapted_not_historical',case_id=case['id'],
            inputs_sha256=inp.document_sha256,graph_sha256=graph['sha256'],
            resources=[dict(id='unit',kind=kind,position=points[1],work_remaining=work,
                            water_litres=0 if kind=='crew' else water)],
            groups=[dict(id='cohort',count=6,position=points[2],initial_exposed=True,handover_exposure=None)],
            destinations=[dict(id='safe',capacity=10,position=points[4])])
        host=GuardedIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            graph=graph,expected_graph_sha256=graph['sha256'],source=source,expected_source_sha256=digest(source),
            cell_evidence=ev,expected_evidence_sha256=digest(ev),allowed_edge_ids=[e['id'] for e in graph['edges']],
            catalog=roster,expected_catalog_sha256=api._hash(roster),containment_config=config,pressure_scale=1,opt_in=True)
        support=dict(version=EVIDENCE_VERSION,role='synthetic_fixture_only',inputs_sha256=inp.document_sha256,
            graph_sha256=graph['sha256'],sites=[dict(node=points[3],edge=[[6,2],[7,2]],protected_cell=[6,2],
                                                    kinds=['crew','engine'])] if sites else [])
        return GuardedHoldV1(host,support,expected_work_sites_sha256=api._hash(support),opt_in=True)
    def deploy(h):
        route=h.route_options('unit',points[3])['options'][0]
        h.dispatch_hold('unit',points[3],route['id'],[[6,2],[7,2]],[6,2])
    def arrive(h):
        for _ in range(30):
            unit=h._host._ops.resources['unit']
            if unit['state']=='on_scene':return
            h.advance()
        raise AssertionError('did not arrive')
    def first_work(h):
        arrive(h);u=h._host._ops.resources['unit']
        before=(u['work_remaining'],u['water_litres'])
        assert not h._host._fire._fire._containment._lines
        while h._host._ops.resources['unit']['state']=='on_scene':
            if h._host._minute+1 < h._host._ops.resources['unit']['due_at']:
                h.advance();u=h._host._ops.resources['unit']
                assert (u['work_remaining'],u['water_litres'])==before
                assert not h._host._fire._fire._containment._lines
            else:h.advance()
        assert h._host._ops.resources['unit']['state']=='working'
        return before

    # Finite water/duty charged only on the real hold tick; return is guarded.
    h=factory();deploy(h);first_work(h)
    unit=h._host._ops.resources['unit']
    assert (unit['work_remaining'],unit['water_litres'])==(1,100)
    assert h._host._fire._fire._containment._lines[((6,2),(7,2))]['strength']>0
    assert not any(r['kind']=='hold_status' and r['observed_at']==h._host._minute for r in h.reports(limit=200))
    cp=json.loads(json.dumps(h.checkpoint()));clone=factory();clone.restore(cp)
    assert h.advance()==clone.advance()
    unit=h._host._ops.resources['unit']
    assert (unit['work_remaining'],unit['water_litres'],unit['state'])==(0,0,'returning')
    assert h._host._valid_journey(unit['journey'],'engine')
    assert api._encoded(h.checkpoint())==api._encoded(clone.checkpoint())
    tape=h.transcript();fresh=factory()
    assert fresh.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'])==h.situation()
    assert api._encoded(fresh.checkpoint())==api._encoded(h.checkpoint())
    assert 'strength' not in json.dumps(h.situation())+json.dumps(h.reports(limit=200))
    assert h.situation()['population'][0]['exposed'] is None

    # Crews spend duty but no water; exhaustion uses a guarded return.
    crew=factory(kind='crew',work=1);deploy(crew);arrive(crew)
    while crew._host._ops.resources['unit']['work_remaining']:
        crew.advance()
    c=crew._host._ops.resources['unit']
    assert (c['work_remaining'],c['water_litres'],c['state'])==(0,0,'returning')
    assert crew._host._valid_journey(c['journey'],'crew')
    assert crew._host._fire._fire._containment._lines[((6,2),(7,2))]['strength']>0

    # Actual crossing is blocked by the previously constructed line. The fire
    # is ignited after setup; its proximity ends work before the physical step.
    class ZeroRandom(random.Random):
        def random(self):return 0.
    left=factory();right=factory();left.restore(cp);right.restore(cp)
    right._host._fire._fire._containment._lines.clear()  # controlled no-line comparison
    for obj in (left,right):
        assert obj._host._fire.change({'ignite':[[7,2]]})
        minute=obj._host._minute+1
        row=weather.conditions(obj._host._weather._profile,minute);row.update(minute=0,received_at=0)
        profile=weather.WeatherProfile.from_records([row])
        prior=weather.initial_state(profile,moisture_proxy=obj._host._weather._weather_state['moisture_proxy'])
        f=obj._host._fire._fire;f.wind=tuple(row['wind'])
        f.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
        f.cells[2][7].update(heat=1.,age=f.spread_interval(-1,0)-1);f.rng=ZeroRandom(2)
    left.advance();right.advance()
    assert left._host._fire._fire.cells[2][6]['heat']==0
    assert right._host._fire._fire.cells[2][6]['heat']>0
    assert left._host._ops.resources['unit']['work_remaining']==1  # unsafe, no new work
    assert left._host._ops.resources['unit']['state']=='needs_assistance'  # guarded unsafe return

    # A mid-edge release cannot fabricate a return connector.
    moving=factory();deploy(moving);moving.advance(2)
    p=copy.deepcopy(moving._host._ops.resources['unit']['position'])
    assert p not in list(points.values())
    moving.release('unit')
    assert moving._host._ops.resources['unit']['state']=='needs_assistance'
    assert moving._host._ops.resources['unit']['position']==p
    assert not moving._missions

    # Source-unknown access cannot support any line or dispatch.
    unknown=factory(unknown=True,sites=False)
    assert not unknown.route_options('unit',points[3])['options']
    try:factory(unknown=True,sites=True)
    except ValueError:pass
    else:raise AssertionError('source unknown promoted to work access')
    no_site=factory(sites=False);supported=factory()
    option=no_site.route_options('unit',points[3])['options'][0]
    supported_option=supported.route_options('unit',points[3])['options'][0]
    assert option==supported_option
    assert no_site.dispatch_hold('unit',points[3],option['id'],[[6,2],[7,2]],[6,2]) == \
           supported.dispatch_hold('unit',points[3],supported_option['id'],[[6,2],[7,2]],[6,2])
    assert no_site._host._ops.resources['unit']['state']=='available'
    assert supported._host._ops.resources['unit']['state']=='mobilizing'
    assert not any(r['kind']=='dispatch_result' for r in no_site.reports(limit=200))
    no_site.advance(6)
    assert any(r['kind']=='dispatch_result' and r['payload']['accepted'] is False for r in no_site.reports(limit=200))
    assert not no_site._host._fire._fire._containment._lines

    # Guarded host and hold identity bind the loaded lifecycle and routing code.
    probe=factory();snap=json.loads(json.dumps(probe.checkpoint()))
    standalone=factory()._host
    standalone_snap=json.loads(json.dumps(standalone.checkpoint()))
    pins=probe._identity['host']['implementation']
    assert all(key in pins for key in ('operations','events','mobility'))
    old_sha=host_module._sha
    for module in (host_module.operations_module,host_module.events_module,host_module.mobility_module):
        target=Path(module.__file__).resolve()
        def changed(path, target=target):
            # Simulate changed file bytes without touching source used by other runs.
            if Path(path).resolve()==target:
                import hashlib
                return hashlib.sha256(Path(path).read_bytes()+b'\n# simulated mutation').hexdigest()
            return old_sha(path)
        with patch.object(host_module,'_sha',side_effect=changed):
            try:probe.situation()
            except RuntimeError:pass
            else:raise AssertionError('changed dependency accepted')
            try:probe.restore(snap)
            except (RuntimeError,ValueError):pass
            else:raise AssertionError('checkpoint accepted changed dependency')
            try:standalone.restore(standalone_snap)
            except (RuntimeError,ValueError):pass
            else:raise AssertionError('standalone host accepted changed dependency')
        probe.restore(snap);standalone.restore(standalone_snap)
        with patch.object(module,'__file__','/tmp/untrusted-dependency.py'):
            try:probe.situation()
            except RuntimeError:pass
            else:raise AssertionError('wrong loaded dependency origin accepted')
            try:probe.restore(snap)
            except (RuntimeError,ValueError):pass
            else:raise AssertionError('checkpoint accepted wrong dependency origin')
            try:standalone.restore(standalone_snap)
            except (RuntimeError,ValueError):pass
            else:raise AssertionError('standalone host accepted wrong dependency origin')
        probe.restore(snap);standalone.restore(standalone_snap)
    assert api._encoded(probe.checkpoint())==api._encoded(snap)
    with patch.object(host_module.operations_module,'journey_position',lambda *a:None):
        try:probe.situation()
        except RuntimeError:pass
        else:raise AssertionError('changed loaded mobility binding accepted')

    # Cost mismatch poisons the partial fire step but leaves Ops uncommitted.
    fault=factory(work=3,water=900);deploy(fault);first_work(fault)
    snap=json.loads(json.dumps(fault.checkpoint()));original=fault._host._fire.step
    def corrupt(*args,**kwargs):
        out=original(*args,**kwargs);fault._host._fire.costs=[];return out
    fault._host._fire.step=corrupt
    try:fault.advance()
    except RuntimeError:pass
    else:raise AssertionError('mismatched costs accepted')
    assert fault._host._ops.checkpoint()==snap['host']['operations']
    try:fault.situation()
    except RuntimeError:pass
    else:raise AssertionError('partial state exposed')
    del fault._host._fire.step
    fault.restore(snap)
    assert api._encoded(fault.checkpoint())==api._encoded(snap)

    # Censor during a real hold tick keeps reconciled private duty/water but no reports.
    boundary=factory(work=3,water=900);deploy(boundary);first_work(boundary)
    old=copy.deepcopy(boundary._host._ops.resources['unit'])
    assert boundary._host._fire.change({'ignite':[[1,1]]})
    row=weather.conditions(boundary._host._weather._profile,boundary._host._minute+1)
    row.update(minute=0,received_at=0);profile=weather.WeatherProfile.from_records([row])
    prior=weather.initial_state(profile,moisture_proxy=boundary._host._weather._weather_state['moisture_proxy'])
    f=boundary._host._fire._fire;f.wind=tuple(row['wind'])
    f.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
    f.cells[1][1].update(heat=1.,age=f.spread_interval(0,-1)-1);f.rng=ZeroRandom(7)
    assert boundary.advance()['status']=='censored'
    after=boundary._host._ops.resources['unit']
    assert after['work_remaining']==old['work_remaining']-1 and after['water_litres']==max(0,old['water_litres']-500)
    try:boundary.reports()
    except api.CensoredError:pass
    else:raise AssertionError('censored reports leaked')
    saved=json.loads(json.dumps(boundary.checkpoint()));restored=factory(work=3,water=900);restored.restore(saved)
    assert api._encoded(saved)==api._encoded(restored.checkpoint())


if __name__=='__main__':main()
