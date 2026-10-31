"""Joint synthetic lifecycle: one fire, operations engine and communications bus."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_joint_fixture():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert p.returncode==0,p.stdout+p.stderr


def main():
    import copy,json,random
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import convert
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_joint_v1.fixture_host import SharedSyntheticIncidentV1,VERSION
    base,_=fixture_inputs();n=base['grid']['width'];t=base['grid']['transform']
    cell=lambda x,y:[t[2]+(x+.5)*500,t[5]-(y+.5)*500]
    points={i:cell(i*2,2) for i in range(1,6)}
    ways=[dict(id=10+i,nodes=[i,i+1],tags={'highway':'residential'}) for i in range(1,5)]
    arcs=[]
    for way in ways:
        a,b=way['nodes']
        for direction in ('f','r'):
            arcs.append(dict(way=way['id'],segment=0,direction=direction,source=a if direction=='f' else b,target=b if direction=='f' else a))
    source=dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=ways,arcs=arcs)
    graph=convert(source)
    for e in graph['edges']:e['allowed_modes']=['crew','engine']
    graph['sha256']=digest({k:v for k,v in graph.items() if k!='sha256'})
    document=dict(version='wildfire-extended-inputs-v1',grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)],surface=[['land']*n for _ in range(n)],fuel=copy.deepcopy(base['fuel_classes']),
        elevation=copy.deepcopy(base['heights']),access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17]='mixed';document['access'][16][17]='unknown'
    case=dict(id='joint-fixture',role='synthetic_fixture_only',seed=7,initial_fire=[[16,16]],wind=[0.,0.],centers=['center'],warmup_minutes=0,
        weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0),
                          dict(minute=10,wind=[1.,0.],temperature_c=26,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=12)],
        events=[dict(kind='wind',minute=10,wind=[1.,0.])]);case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='e'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    inp=api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
    mixed=MixedInputsV2(inp,((17,16,.8,.5),),tuple(sorted(dict(version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
    def unit(identifier,kind,pos,water,work):
        return dict(id=identifier,kind=kind,position=pos,base_position=pos,state='available',available_at=0,
            facility_id=identifier+'-base',fixture_facility_water=2000,work_remaining=work,
            water_litres=water,capacity_litres=water)
    engine=unit('engine','engine',points[1],600,2)
    air=unit('air','aircraft',cell(12,16),500,2);air.update(fuel_minutes=180,fuel_capacity_minutes=180)
    fixture=dict(version=VERSION,role='synthetic_fixture_only',case_id=case['id'],inputs_sha256=inp.document_sha256,
        road=graph,road_sha256=graph['sha256'],allowed_edges=[e['id'] for e in graph['edges']],
        work_sites=[dict(id='site',node=points[3],edge=[[6,2],[7,2]],protected_cell=[6,2],kinds=['engine'])],
        resources=[engine,air],groups=[dict(id='cohort',count=6,position=points[2],home=points[2],status='at_home',
            initial_exposed=True,handover_exposure=None)],destinations=[dict(id='safe',capacity=10,position=points[4],status='open')],
        centers=['center'],events=[dict(kind='center_unavailable',minute=8,until=12,center_id='center'),
            dict(kind='destination_closed',minute=35,destination_id='safe')],
        incoming_reports=[dict(minute=3,source='patrol',role='responder',channel='radio',text='Fixture patrol report',delay=4)],
        start_utc='2024-06-21T12:00:00Z',longitude=-4.0,latitude=36.7,flight_speed_kph=160)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def make(f=fixture, draft_arg=drafts):
        return SharedSyntheticIncidentV1(mixed,draft_arg,expected_drafts_sha256=draft_arg['sha256'],case_id=case['id'],
            fixture=f,expected_fixture_sha256=api._hash(f),expected_road_sha256=graph['sha256'],
            expected_work_sites_sha256=api._hash(f['work_sites']),containment_config=config,pressure_scale=1,opt_in=True)
    h=make();assert h.situation()['population'][0]['exposed'] is None
    h.dispatch_hold('engine','site');h.clear_aircraft('air');h.dispatch_aircraft('air',[16,16]);h.protect('cohort','evacuate','safe')
    h.request_status('center')
    assert h._ops.groups['cohort']['position']==points[2]
    h.advance(20)
    assert h._ops.groups['cohort']['position']!=points[2]
    assert h._ops.resources['engine']['work_remaining']<2
    assert h._ops.resources['engine']['water_litres']<600
    assert h._fire._fire._containment._lines
    assert h._ops.resources['air']['work_remaining']<2
    assert any(x['kind']=='incoming_report' and x['payload']['text']=='Fixture patrol report' for x in h.reports(limit=200))
    assert any(x['kind']=='status_response' for x in h.reports(limit=200))
    assert h._weather._minute==h._minute==h._ops.last_minute==h._bus._last_tick
    assert h._weather._fire is h._fire
    assert h._weather._minute==25 and h._fire._fire.tick==25
    # The staged line changes actual fire spread, not just a status report.
    from eval import incident_weather_v1 as weather
    class ZeroRandom(random.Random):
        def random(self):return 0.
    lined=make();lined.dispatch_hold('engine','site')
    for _ in range(30):
        if lined._fire._fire._containment._lines:break
        lined.advance()
    assert lined._fire._fire._containment._lines
    saved=lined.checkpoint();unlined=make();unlined.restore(saved,expected_checkpoint_sha256=api._hash(saved))
    unlined._fire._fire._containment._lines.clear()
    for obj in (lined,unlined):
        assert obj._fire.change({'ignite':[[7,2]]})
        row=weather.conditions(obj._weather._profile,obj._minute+1);row.update(minute=0,received_at=0)
        profile=weather.WeatherProfile.from_records([row])
        prior=weather.initial_state(profile,moisture_proxy=obj._weather._weather_state['moisture_proxy'])
        fire=obj._fire._fire;fire.wind=tuple(row['wind'])
        fire.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
        fire.cells[2][7].update(heat=1.,age=fire.spread_interval(-1,0)-1);fire.rng=ZeroRandom(2)
    lined.advance();unlined.advance()
    assert lined._fire._fire.cells[2][6]['heat']==0 and unlined._fire._fire.cells[2][6]['heat']>0
    state=json.loads(json.dumps(h.checkpoint()));clone=make();clone.restore(state,expected_checkpoint_sha256=api._hash(state))
    assert h.advance(5)==clone.advance(5)
    assert api._encoded(h.checkpoint())==api._encoded(clone.checkpoint())
    tape=h.transcript();fresh=make()
    assert fresh.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'],
                        expected_final_checkpoint_sha256=api._hash(h.checkpoint()))==h.situation()
    changed=copy.deepcopy(state);changed['air_until']=42;changed['sha256']=api._hash({k:v for k,v in changed.items() if k!='sha256'})
    rejected=make()
    try:rejected.restore(changed,expected_checkpoint_sha256=api._hash(state))
    except ValueError:pass
    else:raise AssertionError('altered checkpoint accepted')
    illegal=copy.deepcopy(fixture);illegal['role']='source_scenario'
    try:make(illegal)
    except ValueError:pass
    else:raise AssertionError('source role accepted')
    invalid=copy.deepcopy(fixture);invalid['events'].append(dict(kind='unknown',minute=1))
    try:make(invalid)
    except ValueError:pass
    else:raise AssertionError('unsupported event accepted')
    invalid=copy.deepcopy(fixture);invalid['groups'][0]['position']=points[3]
    try:make(invalid)
    except ValueError:pass
    else:raise AssertionError('inconsistent handover location accepted')
    # An undisclosed inherited exposure does not change the handover view.
    hidden=copy.deepcopy(fixture);hidden['groups'][0]['initial_exposed']=False
    first=make();second=make(hidden)
    assert first.situation()==second.situation() and first.reports()==second.reports()
    # Actual exposure is private until a resident update is delivered.
    exposure_fixture=copy.deepcopy(fixture);exposure_fixture['groups'][0].update(initial_exposed=False,handover_exposure=False)
    exposure=make(exposure_fixture);assert exposure._fire.change({'ignite':[[4,2]]})
    exposure.advance();assert exposure._ops.groups['cohort']['exposed'] is True
    assert exposure.situation()['population'][0]['exposed'] is False
    exposure.advance(10);assert exposure.situation()['population'][0]['exposed'] is True
    capacity_fixture=copy.deepcopy(fixture);capacity_fixture['destinations'][0]['capacity']=5
    capacity=make(capacity_fixture);capacity.protect('cohort','evacuate','safe');capacity.advance(6)
    assert not capacity._ops.orders and capacity._ops.groups['cohort']['position']==points[2]
    assert any(r['kind']=='request_result' and r['payload']['accepted'] is False for r in capacity.reports(limit=200))
    # True weather changes before its delayed public report arrives.
    wx=make();wx.advance(10)
    assert wx._fire._fire.wind==(1.,0.) and wx.situation()['wind']==[0.,0.]
    wx.advance(2);assert wx.situation()['wind']==[1.,0.]
    closure=make();closure.advance(35)
    assert closure._ops.destinations['safe']['status']=='closed'
    assert closure.situation()['destinations'][0]['status']=='open'
    closure.advance(4);assert closure.situation()['destinations'][0]['status']=='closed'
    no_flight=copy.deepcopy(fixture);no_flight['events'].append(dict(kind='aircraft_unavailable',minute=1,until=10))
    flight_window=make(no_flight);flight_window.clear_aircraft('air')
    flight_window.dispatch_aircraft('air',[16,16])
    assert flight_window._ops.resources['air']['state']=='available'
    flight_window.advance(5)
    assert any(r['kind']=='request_result' and r['payload']['kind']=='air' and r['payload']['accepted'] is False
               for r in flight_window.reports(limit=200))
    # Overlapping outages cannot be shortened by a later, earlier expiry.
    overlap=copy.deepcopy(fixture);overlap['events']=[
        dict(kind='center_unavailable',minute=1,until=20,center_id='center'),
        dict(kind='aircraft_unavailable',minute=1,until=20),
        dict(kind='center_unavailable',minute=2,until=3,center_id='center'),
        dict(kind='aircraft_unavailable',minute=2,until=3)]
    window=make(overlap);window.clear_aircraft('air');window.dispatch_aircraft('air',[16,16]);window.request_status('center')
    assert window._air_until==20 and window._closed['center']==20
    window.advance(15)
    assert window._minute==18 and window._ops.air_window_open is False
    assert window._ops.resources['air']['state']=='available'
    responses=[r for r in window.reports(limit=200) if r['kind']=='status_response']
    assert responses and responses[0]['payload']['available'] is False
    air_reports=[r for r in window.reports(limit=200) if r['kind']=='aircraft_window']
    assert len(air_reports)==2 and all(r['payload']['until']==20 for r in air_reports)
    snap=json.loads(json.dumps(window.checkpoint()));resumed=make(overlap)
    resumed.restore(snap,expected_checkpoint_sha256=api._hash(snap))
    assert window.advance(2)==resumed.advance(2)
    assert window._ops.air_window_open is True and window._closed['center']==20
    assert api._encoded(window.checkpoint())==api._encoded(resumed.checkpoint())

    def changed_weather(change):
        updated=copy.deepcopy(drafts);record=updated['cases'][0]
        change(record)
        record['sha256']=api._hash({k:v for k,v in record.items() if k!='sha256'})
        updated['sha256']=api._hash({k:v for k,v in updated.items() if k!='sha256'})
        return updated
    # Future true weather is private until its independently timed delivery.
    different=changed_weather(lambda c:(c['weather_schedule'][1].update(wind=[2.,0.]),
                                       c['events'][0].update(wind=[2.,0.])))
    w1,w2=make(),make(draft_arg=different)
    assert w1.situation()==w2.situation() and w1.reports()==w2.reports()
    assert w1.advance(10)==w2.advance(10) and w1.reports()==w2.reports()
    assert w1._fire._fire.wind!=(w2._fire._fire.wind)
    w1.advance(2);w2.advance(2)
    assert w1.situation()['wind']==[1.,0.] and w2.situation()['wind']==[2.,0.]
    # Bad report timing is rejected before the fire owner is constructed.
    from unittest.mock import patch
    from wildfire_source_joint_v1 import fixture_host as host_module
    delayed_initial=changed_weather(lambda c:c['weather_schedule'][0].update(received_at=10))
    too_late=changed_weather(lambda c:c['weather_schedule'][1].update(received_at=71))
    for bad in (delayed_initial,too_late):
        with patch.object(host_module,'WeatherMixedFireDiagnosticV1',side_effect=AssertionError('fire constructed')):
            try:make(draft_arg=bad)
            except ValueError as exc:assert 'handover weather' in str(exc)
            else:raise AssertionError('unsupported weather delay accepted')
    boundary=changed_weather(lambda c:(c['weather_schedule'][1].update(minute=1,received_at=61),
                                      c['events'][0].update(minute=1)))
    accepted=make(draft_arg=boundary);accepted.advance()
    assert accepted._minute==1 and accepted.situation()['status']=='active'
    assert any(q['stage']=='message' and q['record']['kind']=='weather_update' and q['due']==61
               for q in accepted._bus._queue)
    # No actual road access is conferred by the work-site declaration.
    no_road=copy.deepcopy(fixture);no_road['allowed_edges']=[]
    denied=make(no_road);denied.dispatch_hold('engine','site');denied.advance(6)
    assert denied._ops.resources['engine']['state']=='available' and not denied._fire._fire._containment._lines
    assert any(r['kind']=='request_result' and r['payload']['accepted'] is False for r in denied.reports(limit=200))
    try:
        SharedSyntheticIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            fixture=fixture,expected_fixture_sha256=api._hash(fixture),expected_road_sha256='0'*64,
            expected_work_sites_sha256=api._hash(fixture['work_sites']),containment_config=config,pressure_scale=1,opt_in=True)
    except ValueError:pass
    else:raise AssertionError('untrusted road pin accepted')
    try:
        SharedSyntheticIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            fixture=fixture,expected_fixture_sha256=api._hash(fixture),expected_road_sha256=graph['sha256'],
            expected_work_sites_sha256='0'*64,containment_config=config,pressure_scale=1,opt_in=True)
    except ValueError:pass
    else:raise AssertionError('untrusted work-site pin accepted')
    # A new restriction blocks an already issued evacuation, without teleporting.
    restricted=copy.deepcopy(fixture);restricted['events'].append(dict(kind='access_restriction',minute=8,cells=[[4,2]]))
    blocked=make(restricted);blocked.protect('cohort','evacuate','safe');blocked.advance(10)
    assert blocked._ops.groups['cohort']['status']=='blocked' and blocked._ops.groups['cohort']['position']==points[2]
    # Actual fire during setup forces the engine away from work; an unsafe base
    # is never physically advertised as available in the true Operations state.
    unsafe=make();unsafe.dispatch_hold('engine','site')
    for _ in range(30):
        if unsafe._ops.resources['engine']['state']=='on_scene':break
        unsafe.advance()
    assert unsafe._ops.resources['engine']['state']=='on_scene'
    u=copy.deepcopy(unsafe._ops.resources['engine']);assert unsafe._fire.change({'ignite':[[6,2]]})
    unsafe.advance();actual=unsafe._ops.resources['engine']
    assert actual['state']=='needs_assistance' and actual['water_litres']==u['water_litres'] and actual['work_remaining']==u['work_remaining']
    assert not unsafe._fire._fire._containment._lines
    at_base=make();assert at_base._fire.change({'ignite':[[2,2]]});at_base.advance(2)
    assert at_base._ops.resources['engine']['state']=='needs_assistance'
    # Releasing a resource mid-edge cannot create a return connector.
    moving=make();moving.dispatch_hold('engine','site');moving.advance(2)
    prior=copy.deepcopy(moving._ops.resources['engine']['position'])
    assert prior not in list(points.values())
    moving.release('engine')
    assert moving._ops.resources['engine']['position']==prior and moving._ops.resources['engine']['state']=='needs_assistance'
    # One minute of return fuel is consumed by one minute of actual flight.
    fuel_fixture=copy.deepcopy(fixture);fuel_fixture['resources'][1]['fuel_minutes']=2
    fuel=make(fuel_fixture);fuel.clear_aircraft('air');fuel.dispatch_aircraft('air',[16,16]);fuel.advance(2)
    assert fuel._ops.resources['air']['state']=='on_scene' and fuel._ops.resources['air']['fuel_minutes']==1
    departure=copy.deepcopy(fuel._ops.resources['air']['position']);fuel.release('air')
    assert fuel._ops.resources['air']['position']!=departure and fuel._ops.resources['air']['position']==cell(12,16)
    assert fuel._ops.resources['air']['fuel_minutes']==0
    # Pre-step censor suppresses all detailed observations and preserves clock.
    censored=make();assert not censored._fire.change({'ignite':[[0,0]]})
    assert censored.advance()==dict(version=VERSION,status='censored',minute=0)
    try:censored.reports()
    except api.CensoredError:pass
    else:raise AssertionError('censored report leak')
    # A partial physical failure poisons the host until trusted restore.
    fault=make();snap=json.loads(json.dumps(fault.checkpoint()));original=fault._fire.step
    def corrupt(*args,**kwargs):
        result=original(*args,**kwargs);fault._fire.costs=[{'invalid':True}];return result
    fault._fire.step=corrupt
    try:fault.advance()
    except RuntimeError:pass
    else:raise AssertionError('cost mismatch accepted')
    try:fault.situation()
    except RuntimeError:pass
    else:raise AssertionError('partial fire state exposed')
    fault.restore(snap,expected_checkpoint_sha256=api._hash(snap))
    assert api._encoded(fault.checkpoint())==api._encoded(snap)
    text=json.dumps(h.situation())+json.dumps(h.reports(limit=200))
    assert 'strength' not in text and 'initial_exposed' not in text


if __name__=='__main__':main()
