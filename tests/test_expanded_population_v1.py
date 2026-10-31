"""Synthetic integration tests for the private expanded population slice."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_population_integration():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'), str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'), str(ROOT/'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import json
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from wildfire_expanded_incident_v1.host import CATALOG_VERSION as HOST_CATALOG
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_expanded_full_v1.hold import WeatherMixedHoldV1
    from wildfire_expanded_full_v1.population import ExpandedPopulationV1, CATALOG_VERSION

    base, network = fixture_inputs()
    for edge in network['edges']: edge['allowed_modes'] = ['crew','engine']
    network['sha256'] = api._hash({k:v for k,v in network.items() if k!='sha256'})
    n = base['grid']['width']
    document = dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)], surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']), elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17] = 'mixed'; document['access'][16][17] = 'unknown'
    point = lambda key: next(node['position'] for node in network['nodes'] if node['id']==key)
    case = dict(id='synthetic-population',seed=7,initial_fire=[[16,16]],wind=[0.,0.],centers=['center-0'],
        warmup_minutes=2,weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
            relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                source_catalog_sha256='e'*64,cases=[case])
    drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(groups, destinations, *, doc=document, road=network):
        inputs=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
        # Test-only synthetic trust anchor; it is not v4 source reconstruction.
        mixed=MixedInputsV2(inputs,((17,16,.8,.5),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        sc=dict(id=case['id'],seed=case['seed'],initial_fire=case['initial_fire'],wind=case['wind'],
            center='center-0',resources=[dict(id='crew-0',kind='crew',position=point('2:2'),
                                              work_remaining=240,water_litres=0)])
        catalog=dict(version=HOST_CATALOG,role='synthetic_fixture_only',inputs_sha256=inputs.document_sha256,
                     network_sha256=road['sha256'],cases=[sc])
        host=WeatherMixedHoldV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            network=road,expected_network_sha256=road['sha256'],catalog=catalog,
            expected_catalog_sha256=api._hash(catalog),containment_config=config,pressure_scale=1,opt_in=True)
        pop=dict(version=CATALOG_VERSION,role='synthetic_fixture_only',case_id=case['id'],
                 inputs_sha256=inputs.document_sha256,network_sha256=road['sha256'],
                 groups=copy.deepcopy(groups),destinations=copy.deepcopy(destinations))
        return ExpandedPopulationV1(host,pop,expected_catalog_sha256=api._hash(pop),opt_in=True)

    shelter_group=[dict(id='district',count=6,position=[base['grid']['transform'][2]+15.5*500, base['grid']['transform'][5]-15.5*500])]
    destinations=[dict(id='safe',capacity=10,position=point('8:4'))]
    protected=factory(shelter_group,destinations); passive=factory(shelter_group,destinations)
    submitted=protected.protect('district','shelter')
    assert submitted==dict(request_id='protection-request-1',status='submitted')
    assert protected._ops.groups['district']['status']=='at_home'  # order delay
    for _ in range(17): protected.advance(); passive.advance()
    assert not passive._ops.groups['district']['exposed']
    passive.advance(); protected.advance()
    assert passive._ops.groups['district']['exposed']
    assert not passive.situation()['population'][0]['exposed']  # observation has not arrived
    for _ in range(7): protected.advance(); passive.advance()
    assert passive.situation()['population'][0]['exposed']
    assert protected._ops.groups['district']['status']=='sheltering'
    assert not protected._ops.groups['district']['exposed'] and passive._ops.groups['district']['exposed']
    assert passive._dose['district'] >= 180 > protected._dose['district']
    assert all(r['received_at']<=protected.situation()['minute'] for r in protected.reports(limit=200))

    groups=[dict(id='a',count=6,position=point('4:4')),dict(id='b',count=6,position=point('4:4'))]
    env=factory(groups,destinations)
    read_before=env.checkpoint()
    option=env.evacuation_options('a','safe')['options'][0]
    env.situation(); env.reports(limit=200)
    assert api._encoded(read_before)==api._encoded(env.checkpoint())
    assert option['journey']['legs'] and all(l['mode']=='road' for l in option['journey']['legs'])
    assert env.protect('a','evacuate',destination_id='safe',option_id=option['id'])['status']=='submitted'
    initial=copy.deepcopy(env.situation()['population'][0]['position'])
    assert env._ops.groups['a']['position']==initial and env._ops.groups['a']['status']=='at_home'
    second=env.evacuation_options('b','safe')['options'][0]
    assert env.protect('b','evacuate',destination_id='safe',option_id=second['id'])['status']=='submitted'
    assert env._ops.reservations=={'a':'safe'}  # finite capacity; denial is delayed to actor
    assert not any(r['kind']=='protection_request_result' and r['payload']['group_id']=='b'
                   for r in env.reports(limit=200))
    saved=json.loads(json.dumps(env.checkpoint()))
    resumed=factory(groups,destinations); resumed.restore(saved)
    damaged=copy.deepcopy(saved); damaged['dose']['a'] += 1
    try: resumed.restore(damaged)
    except ValueError: pass
    else: raise AssertionError('tampered population checkpoint accepted')
    resumed.restore(saved)
    moved=False
    for _ in range(35):
        assert env.advance()==resumed.advance()
        true=env._ops.groups['a']
        if true['position'] != initial: moved=True
        if true['status'] != 'arrived': assert true['position'] != point('8:4')
        if true['status']=='arrived': break
    assert moved and env._ops.groups['a']['status']=='arrived'
    assert env._ops.groups['a']['position']==point('8:4')
    assert env.situation()['population'][0]['status'] != 'arrived'
    assert not any(r['kind']=='evacuation_arrived' and r['payload']['group_id']=='a'
                   for r in env.reports(limit=200))
    assert api._encoded(env.checkpoint())==api._encoded(resumed.checkpoint())
    env.advance(10)
    reports=env.reports(limit=200)
    assert any(r['kind']=='protection_request_result' and r['payload']['group_id']=='b'
               and r['payload']['accepted'] is False for r in reports)
    assert any(r['kind']=='evacuation_arrived' and r['payload']['group_id']=='a' for r in reports)
    assert env.situation()['population'][0]['status']=='arrived'
    assert 'dose' not in json.dumps(env.situation())+json.dumps(reports)
    assert 'weather_schedule' not in json.dumps(env.situation())+json.dumps(reports)

    # Unknown cell access is not inferred from a road: all intermediate cells
    # remain excluded and no vehicle route may be invented.
    sparse=copy.deepcopy(document); sparse['access']=[['unknown']*n for _ in range(n)]
    for x,y in [(2,2),(4,4),(8,4)]: sparse['access'][y][x]='accessible'
    no_route=factory(groups,destinations,doc=sparse)
    unavailable=no_route.evacuation_options('a','safe')
    assert not unavailable['options'] and unavailable['status']=='unmapped'
    before=no_route.checkpoint()
    try: no_route.protect('a','evacuate',destination_id='safe',option_id='invented')
    except ValueError: pass
    else: raise AssertionError('invented route accepted')
    assert api._encoded(before)==api._encoded(no_route.checkpoint())

    # A real fire blocking the first road step cannot teleport the cohort.
    blocked=factory(groups,destinations)
    option=blocked.evacuation_options('a','safe')['options'][0]
    blocked.protect('a','evacuate',destination_id='safe',option_id=option['id'])
    while blocked._ops.groups['a']['status']!='evacuating': blocked.advance()
    origin=copy.deepcopy(blocked._ops.groups['a']['position'])
    assert blocked._host._fire.change({'ignite':[[6,4]]})
    blocked.advance()
    assert blocked._ops.groups['a']['status']=='blocked'
    assert blocked._ops.groups['a']['position']==origin
    assert blocked.situation()['population'][0]['status'] != 'blocked'
    assert not any(r['kind']=='evacuation_arrived' for r in blocked.reports(limit=200))

    # Actual physical boundary censor suppresses all population observations,
    # and its private population/host clocks restore together.
    import random
    from eval import incident_weather_v1 as weather
    class ZeroRandom(random.Random):
        def random(self): return 0.0
    censored=factory(groups,destinations)
    assert censored._host._fire.change({'ignite':[[1,1]]})
    row=weather.conditions(censored._host._weather._profile,1)
    row.update(minute=0,received_at=0)
    profile=weather.WeatherProfile.from_records([row])
    prior=weather.initial_state(profile,moisture_proxy=censored._host._weather._weather_state['moisture_proxy'])
    f=censored._host._fire._fire
    f.wind=tuple(row['wind']);f.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
    f.cells[1][1].update(heat=1.,age=f.spread_interval(0,-1)-1);f.rng=ZeroRandom(7)
    assert censored.advance()['status']=='censored'
    assert 'population' not in censored.situation()
    assert censored._ops.last_minute==censored._host._minute==1
    try: censored.reports()
    except api.CensoredError: pass
    else: raise AssertionError('censored population reports leaked')
    checkpoint=json.loads(json.dumps(censored.checkpoint()))
    restored=factory(groups,destinations);restored.restore(checkpoint)
    assert api._encoded(checkpoint)==api._encoded(restored.checkpoint())

    # Same observed minute, different causal phase and delivery order: the
    # post-fire exposure update arrives at 20, then a pre-tick false snapshot
    # of the shelter request arrives at 22. Exposure must never regress.
    conflict_groups=[dict(id='d0',count=3,position=point('4:4')),
        dict(id='d1',count=3,position=shelter_group[0]['position'])]
    conflict=factory(conflict_groups,destinations)
    conflict.protect('d0','shelter')
    conflict.advance(16)
    assert conflict._host._minute == 17 and not conflict._ops.groups['d1']['exposed']
    conflict.protect('d1','shelter')
    assert conflict._host._minute == 18 and conflict._ops.groups['d1']['exposed']
    assert not conflict._known['d1']['exposed']
    conflict.advance(2)
    true_report=next(r for r in conflict.reports(limit=200) if r['kind']=='resident_update'
                     and r['source']=='d1' and r['payload']['group']['exposed'])
    assert (true_report['observed_at'],true_report['received_at'])==(18,20)
    assert conflict._known['d1']['exposed'] and conflict._exposed_at['d1']==18
    before_late=json.loads(json.dumps(conflict.checkpoint()))
    replay=factory(conflict_groups,destinations); replay.restore(before_late)
    for _ in range(2):
        assert conflict.advance()==replay.advance()
        assert conflict._known['d1']['exposed'] and replay._known['d1']['exposed']
    stale=next(r for r in conflict.reports(limit=200) if r['kind']=='protection_request_result'
               and r['payload'].get('group_id')=='d1')
    assert (stale['observed_at'],stale['received_at'],stale['payload']['group']['exposed'])==(18,22,False)
    assert conflict._known_at['d1']==18 and conflict._known_rank['d1']==1
    assert conflict._known['d1']['exposed'] and conflict._exposed_at['d1']==18
    assert api._encoded(conflict.checkpoint())==api._encoded(replay.checkpoint())
    conflict.advance(2); replay.advance(2)
    assert conflict._known['d1']['exposed'] and replay._known['d1']['exposed']
    assert api._encoded(conflict.checkpoint())==api._encoded(replay.checkpoint())

    # A rejected hold option fails before any state-changing tick; later calls
    # and checkpoint continuation remain usable.
    invalid=factory(groups,destinations)
    original=invalid.checkpoint()
    bad_calls=[('crew-0',[6,4],'bogus',[[6,4],[7,4]],[7,4]),
               ('crew-0',[6,4],'bogus',[[6,4],[8,4]],[8,4]),
               ('unknown',[6,4],'bogus',[[6,4],[7,4]],[7,4])]
    for args in bad_calls:
        try: invalid.dispatch_hold(*args)
        except ValueError: pass
        else: raise AssertionError('invalid hold dispatch accepted')
        assert api._encoded(original)==api._encoded(invalid.checkpoint())
    invalid.advance()
    assert invalid._host._minute==1
    valid=invalid.route_options('crew-0',[6,4])['options'][0]
    invalid.dispatch_hold('crew-0',[6,4],valid['id'],[[6,4],[7,4]],[7,4])
    assert invalid._host._minute==2 and invalid._host._ops.resources['crew-0']['state']=='mobilizing'

    # A failure after the host tick begins remains indeterminate and poisons
    # the composition until a trusted joint checkpoint is restored.
    fault=factory(groups,destinations)
    route=fault.route_options('crew-0',[6,4])['options'][0]
    saved=json.loads(json.dumps(fault.checkpoint()))
    def fail_step(): raise RuntimeError('synthetic in-flight failure')
    fault._host._weather.step=fail_step
    try: fault.dispatch_hold('crew-0',[6,4],route['id'],[[6,4],[7,4]],[7,4])
    except RuntimeError: pass
    else: raise AssertionError('in-flight fault accepted')
    assert fault._host._minute==1
    try: fault.situation()
    except RuntimeError: pass
    else: raise AssertionError('in-flight fault did not poison host')
    del fault._host._weather.step
    fault.restore(saved)
    assert api._encoded(saved)==api._encoded(fault.checkpoint())
    fault.advance()

    # Existing ground hold still runs while population orders advance.
    combined=factory(groups,destinations)
    route=combined.route_options('crew-0',[6,4])['options'][0]
    combined.dispatch_hold('crew-0',[6,4],route['id'],[[6,4],[7,4]],[7,4])
    combined.protect('a','shelter')
    combined.advance(25)
    assert combined._ops.groups['a']['status']=='sheltering'
    assert combined._host._fire._fire._containment._lines
    assert combined._ops.last_minute==combined._host._minute


if __name__=='__main__': main()
