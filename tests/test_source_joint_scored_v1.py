"""Synthetic single-clock full-day, replay, privacy and factor-pin regression."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_source_joint_scored_synthetic_day():
    paths = ['envs','envs/wildfire_extended_inputs_v1','envs/wildfire_response/core/src',
             'envs/wildfire_containment_v1/src','envs/wildfire_containment_score_v1/src','.']
    env = dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',
               PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in paths))
    result = subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import json
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import convert
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_expanded_incident_v1 import mixed_v2 as mixed_module
    from simulator import terrain as terrain_module
    from wildfire_expanded_scored_v2 import mixed_factors_sha256
    from wildfire_source_joint_v1.fixture_host import SharedSyntheticIncidentV1, VERSION as HOST_VERSION
    from wildfire_source_joint_scored_v1 import (SourceJointScoredV1, configuration_identity,
        handover_exposure_sha256, terrain_profiles_sha256, verify_outcome)
    from wildfire_containment_score_v1.scoring import recompute

    base,_ = fixture_inputs(); n = base['grid']['width']; t = base['grid']['transform']
    cell = lambda x,y:[t[2]+(x+.5)*500,t[5]-(y+.5)*500]
    points = {i:cell(i*2,2) for i in range(1,6)}
    ways = [dict(id=10+i,nodes=[i,i+1],tags={'highway':'residential'}) for i in range(1,5)]
    arcs = []
    for way in ways:
        a,b = way['nodes']
        for direction in ('f','r'):
            arcs.append(dict(way=way['id'],segment=0,direction=direction,
                source=a if direction=='f' else b,target=b if direction=='f' else a))
    graph = convert(dict(nodes=[dict(id=i,position_m=p) for i,p in points.items()],ways=ways,arcs=arcs))
    for edge in graph['edges']: edge['allowed_modes']=['crew','engine']
    # Disconnected synthetic cohort anchors confer no road access.
    graph['nodes'].extend([dict(id='inherited',position=cell(16,16)),dict(id='new',position=cell(15,15))])
    graph['sha256'] = digest({k:v for k,v in graph.items() if k!='sha256'})
    document = dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)],surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']),elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17]='mixed'; document['access'][16][17]='unknown'
    case = dict(id='joint-scored-fixture',role='synthetic_fixture_only',seed=7,initial_fire=[[16,16]],
        wind=[0.,0.],centers=['center'],warmup_minutes=2,
        weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,
                               rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256'] = api._hash(case)
    drafts = dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                  source_catalog_sha256='e'*64,cases=[case]); drafts['sha256']=api._hash(drafts)
    inputs = api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
    def unit(identifier,kind,pos,water,work):
        return dict(id=identifier,kind=kind,position=pos,base_position=pos,state='available',available_at=0,
            facility_id=identifier+'-base',fixture_facility_water=2000,work_remaining=work,
            water_litres=water,capacity_litres=water)
    engine = unit('engine','engine',points[1],600,2)
    air = unit('air','aircraft',cell(12,16),500,2); air.update(fuel_minutes=180,fuel_capacity_minutes=180)
    fixture = dict(version=HOST_VERSION,role='synthetic_fixture_only',case_id=case['id'],inputs_sha256=inputs.document_sha256,
        road=graph,road_sha256=graph['sha256'],allowed_edges=[e['id'] for e in graph['edges']],
        work_sites=[dict(id='site',node=points[3],edge=[[6,2],[7,2]],protected_cell=[6,2],kinds=['engine'])],
        resources=[engine,air],groups=[
            dict(id='inherited',count=4,position=cell(16,16),home=cell(16,16),status='at_home',initial_exposed=True,handover_exposure=None),
            dict(id='new',count=3,position=cell(15,15),home=cell(15,15),status='at_home',initial_exposed=False,handover_exposure=None),
            dict(id='far',count=6,position=points[2],home=points[2],status='at_home',initial_exposed=False,handover_exposure=None)],
        destinations=[dict(id='safe',capacity=10,position=points[4],status='open')],centers=['center'],events=[],incoming_reports=[],
        start_utc='2024-06-21T12:00:00Z',longitude=-4.,latitude=36.7,flight_speed_kph=160)
    config = dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(fixture_arg=fixture, *, crossing=.5):
        mixed = MixedInputsV2(inputs,((17,16,.8,crossing),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        return SharedSyntheticIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            fixture=fixture_arg,expected_fixture_sha256=api._hash(fixture_arg),expected_road_sha256=graph['sha256'],
            expected_work_sites_sha256=api._hash(fixture_arg['work_sites']),containment_config=config,pressure_scale=1,opt_in=True)

    mask = [y*n+x for y in range(5,24) for x in range(5,24) if (x,y)!=(17,16)]
    identity = configuration_identity(factory(),mask)  # test-only candidate; real pins are independent
    assert identity['mixed_factors_sha256'] == mixed_factors_sha256([(17,16,.8,.5)])
    assert identity['handover_exposure_sha256'] == handover_exposure_sha256(fixture['groups'])
    assert identity['handover_state_sha256'] == api._hash(factory().checkpoint())
    approved_profiles = {
        'field':dict(fuel=1.,spread=1.15,duration=55),
        'scrub':dict(fuel=1.,spread=.85,duration=95),
        'woodland':dict(fuel=1.,spread=.6,duration=150),
        'built':dict(fuel=1.,spread=.3,duration=120),
        'road':dict(fuel=0.,spread=0.,duration=1),
        'bare':dict(fuel=0.,spread=0.,duration=1),
    }
    assert identity['terrain_profiles_sha256'] == terrain_profiles_sha256(approved_profiles)
    assert mixed_module.PROFILES is terrain_module.PROFILES
    invalid_profiles = copy.deepcopy(approved_profiles); invalid_profiles['scrub']['spread'] = True
    try: terrain_profiles_sha256(invalid_profiles)
    except ValueError: pass
    else: raise AssertionError('invalid terrain profile accepted')
    def make(): return SourceJointScoredV1(factory(),scoring_mask=mask,expected_identity=identity,opt_in=True)

    # Same supplied mixed identity and underlying checkpoint, different physics.
    a,b = factory(),factory(crossing=.95)
    assert api._encoded(a._identity) == api._encoded(b._identity)
    assert api._encoded(a.checkpoint()) == api._encoded(b.checkpoint())
    assert configuration_identity(b,mask)['mixed_factors_sha256'] != identity['mixed_factors_sha256']
    try: SourceJointScoredV1(b,scoring_mask=mask,expected_identity=identity,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('different crossing factor accepted')
    # Reviewer reproduction: same source identity and checkpoint, but a
    # changed mutable profile gives a different scoring configuration.
    initial_checkpoint = copy.deepcopy(a.checkpoint())
    original_spread = mixed_module.PROFILES['scrub']['spread']
    try:
        mixed_module.PROFILES['scrub']['spread'] = original_spread / 2
        changed_profiles = factory()
        assert api._encoded(a._identity) == api._encoded(changed_profiles._identity)
        assert api._encoded(initial_checkpoint) == api._encoded(changed_profiles.checkpoint())
        changed_identity = configuration_identity(changed_profiles,mask)
        assert changed_identity['terrain_profiles_sha256'] != identity['terrain_profiles_sha256']
        try: SourceJointScoredV1(changed_profiles,scoring_mask=mask,expected_identity=identity,opt_in=True)
        except ValueError: pass
        else: raise AssertionError('changed terrain profiles accepted against trusted pin')
    finally:
        mixed_module.PROFILES['scrub']['spread'] = original_spread

    # A changed static fire rule is not present in the host checkpoint; its
    # independently pinned effective configuration must still differ.
    altered_fire = factory()
    altered_fire._fire._fire.rules['ignition_probability_per_eligible_cell'] = .1
    assert api._encoded(a._identity) == api._encoded(altered_fire._identity)
    assert api._encoded(a.checkpoint()) == api._encoded(altered_fire.checkpoint())
    assert configuration_identity(altered_fire,mask)['fire_configuration_sha256'] != identity['fire_configuration_sha256']
    try: SourceJointScoredV1(altered_fire,scoring_mask=mask,expected_identity=identity,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('different effective fire configuration accepted')
    altered_ops = factory(); altered_ops._ops.profile['duty_minutes'] += 1
    assert configuration_identity(altered_ops,mask)['operations_configuration_sha256'] != identity['operations_configuration_sha256']
    try: SourceJointScoredV1(altered_ops,scoring_mask=mask,expected_identity=identity,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('different effective operations configuration accepted')
    altered_route = factory()
    edge_id = next(iter(altered_route._network.edges))
    altered_route._network.edges[edge_id]['speed_kph'] *= .5
    assert api._encoded(a._identity) == api._encoded(altered_route._identity)
    assert api._encoded(a.checkpoint()) == api._encoded(altered_route.checkpoint())
    assert configuration_identity(altered_route,mask)['routing_configuration_sha256'] != identity['routing_configuration_sha256']
    try: SourceJointScoredV1(altered_route,scoring_mask=mask,expected_identity=identity,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('different effective road configuration accepted')

    env = make(); initial = env._audit[0]['population']
    assert initial['inherited']['exposed']==4 and initial['new']['exposed']==0
    assert env._counter.initial_exposure == 4
    public = {g['id']:g for g in env.situation()['population']}
    assert public['inherited']['exposed'] is None and public['new']['exposed'] is None
    text = json.dumps([env.situation(),env.reports(limit=200)])
    assert all(word not in text for word in ('initial_exposed','dose','mixed_factors','terrain_profiles','scoring_mask','graded_score','audit'))
    before = env.checkpoint(); env.situation(); env.reports(limit=200); assert before == env.checkpoint()
    env._host._fire._inner._fire._mixed_openness[(17,16)] = .95
    try: env.situation()
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered effective crossing accepted')
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    assert env.checkpoint() == before
    env._host._fire._fire.rules['ignition_probability_per_eligible_cell'] = .1
    try: env.situation()
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered effective fire configuration accepted')
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    assert env.checkpoint() == before
    assert terrain_profiles_sha256(before['terrain_profiles']) == identity['terrain_profiles_sha256']
    altered_profiles = copy.deepcopy(before); altered_profiles['terrain_profiles']['scrub']['spread'] /= 2
    altered_profiles['sha256'] = api._hash({k:v for k,v in altered_profiles.items() if k != 'sha256'})
    try: env.restore(altered_profiles,expected_checkpoint_sha256=api._hash(altered_profiles))
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered terrain-profile checkpoint accepted')
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    # Restore must fail closed while the live shared table is changed.
    original_duration = mixed_module.PROFILES['scrub']['duration']
    try:
        mixed_module.PROFILES['scrub']['duration'] = original_duration + 1
        try: env.situation()
        except (ValueError,RuntimeError): pass
        else: raise AssertionError('runtime duration change accepted')
        try: env.restore(before,expected_checkpoint_sha256=api._hash(before))
        except (ValueError,RuntimeError): pass
        else: raise AssertionError('restore accepted changed terrain profiles')
    finally:
        mixed_module.PROFILES['scrub']['duration'] = original_duration
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    # Rebinding an equal-looking table is rejected because it is not the
    # terrain module's actual kernel binding.
    original_binding = mixed_module.PROFILES
    try:
        mixed_module.PROFILES = copy.deepcopy(original_binding)
        try: env.situation()
        except (ValueError,RuntimeError): pass
        else: raise AssertionError('unreviewed kernel profile binding accepted')
    finally:
        mixed_module.PROFILES = original_binding
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    # An instance-level replacement is not the pinned mixed-fire step.
    fire = env._host._fire._fire
    fire.step = lambda *_args, **_kwargs: None
    try: env.situation()
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('unreviewed live kernel step accepted')
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    # Inject a profile mutation inside an actual physical step. The wrapper
    # must fail before returning an actor result and require trusted restore.
    fire = env._host._fire._fire
    original_tick = fire._containment.tick
    original_spread = mixed_module.PROFILES['scrub']['spread']
    def change_profiles_during_tick(*args, **kwargs):
        result = original_tick(*args, **kwargs)
        mixed_module.PROFILES['scrub']['spread'] = original_spread / 2
        return result
    fire._containment.tick = change_profiles_during_tick
    try:
        try: env.advance()
        except (ValueError,RuntimeError): pass
        else: raise AssertionError('during-step terrain mutation accepted')
        assert env._poisoned
    finally:
        mixed_module.PROFILES['scrub']['spread'] = original_spread
    env.restore(before,expected_checkpoint_sha256=api._hash(before))
    assert env.checkpoint() == before
    assert verify_outcome(env,fresh_host=factory,expected_identity=identity,scoring_mask=mask)['status']=='unfinished'
    bad = copy.deepcopy(identity); bad['handover_exposure_sha256']='0'*64
    try: SourceJointScoredV1(factory(),scoring_mask=mask,expected_identity=bad,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong handover identity accepted')
    bad_source = copy.deepcopy(identity); bad_source['host']['implementation']='0'*64
    try: SourceJointScoredV1(factory(),scoring_mask=mask,expected_identity=bad_source,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong host source pin accepted')
    bad_scorer = copy.deepcopy(identity); bad_scorer['implementation_sha256']='0'*64
    try: SourceJointScoredV1(factory(),scoring_mask=mask,expected_identity=bad_scorer,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong scorer source pin accepted')
    wrong_factors = copy.deepcopy(identity); wrong_factors['mixed_factors_sha256']='0'*64
    try: SourceJointScoredV1(factory(),scoring_mask=mask,expected_identity=wrong_factors,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong mixed factor pin accepted')
    env.dispatch_hold('engine','site'); env.protect('far','shelter'); env.advance(15)
    assert env._audit[-1]['population']['new']['exposed']==0
    env.advance(1)
    assert env._audit[-1]['population']['new']['exposed']==3
    assert {g['id']:g for g in env.situation()['population']}['new']['exposed'] is None
    env.clear_aircraft('air'); env.dispatch_aircraft('air',[16,16]); env.request_status('center')
    assert env._host._ops.resources['engine']['work_remaining'] < 2
    assert env._host._fire._fire._containment._lines
    saved = json.loads(json.dumps(env.checkpoint())); pin = api._hash(saved)
    resumed = make(); resumed.restore(saved,expected_checkpoint_sha256=pin)
    altered = copy.deepcopy(saved); altered['audit'][0]['population']['inherited']['exposed']=0
    altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
    try: resumed.restore(altered,expected_checkpoint_sha256=pin)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered trusted checkpoint accepted')
    resumed.restore(saved,expected_checkpoint_sha256=pin)
    assert env.advance(60)==resumed.advance(60)
    assert api._encoded(env.checkpoint())==api._encoded(resumed.checkpoint())
    env = resumed
    while env._host._minute < 1440 and env._host._status=='active': env.advance(60)
    assert env._host._status=='end_unscored' and env._host._minute==1440
    wrong_replay = verify_outcome(env,fresh_host=lambda:factory(crossing=.95),expected_identity=identity,scoring_mask=mask)
    assert wrong_replay['status']=='verification_failed' and wrong_replay['score'] is None
    grade = verify_outcome(env,fresh_host=factory,expected_identity=identity,scoring_mask=mask)
    assert grade['status']=='full_day' and grade['verified'] and grade['score'] is not None, grade['status']
    outcome = recompute(env._audit,scoring_mask=mask,terminal=True,abandoned=False,censored=False)
    assert outcome['inherited_exposed_population']==4 and outcome['new_exposed_population']==3
    assert abs(outcome['protected_population_fraction']-2/3)<1e-12
    inherited = set(env._audit[0]['footprint'])
    new = {c for row in env._audit for c in row['new_footprint']}
    burned = (new-inherited) & set(mask); eligible=set(mask)-inherited
    assert inherited & set(mask)
    assert abs(grade['score']-(.8*2/3+.2*(1-len(burned)/len(eligible))))<1e-12
    assert env._host._fire._fire._containment._lines
    assert env._host._weather._minute==env._host._minute==env._host._ops.last_minute
    assert verify_outcome(env,fresh_host=factory,expected_identity=bad,scoring_mask=mask)['status']=='verification_failed'
    assert verify_outcome(env,fresh_host=factory,expected_identity=identity,scoring_mask=mask[1:])['status']=='verification_failed'
    assert verify_outcome(env,fresh_host=factory,expected_identity=identity,scoring_mask=mask,infrastructure_timeout=True)['status']=='timeout'
    env._calls[0]['result']['status']='tampered'
    assert verify_outcome(env,fresh_host=factory,expected_identity=identity,scoring_mask=mask)['status']=='verification_failed'

    # A private inherited flag has no actor-visible effect before delivered reports.
    other = copy.deepcopy(fixture); other['groups'][2]['initial_exposed'] = True
    other_identity = configuration_identity(factory(other),mask)
    left = make(); right = SourceJointScoredV1(factory(other),scoring_mask=mask,expected_identity=other_identity,opt_in=True)
    assert left.situation()==right.situation() and left.reports()==right.reports()
    assert left.protect('far','shelter')==right.protect('far','shelter')
    assert left.situation()==right.situation() and left.reports()==right.reports()
    for _ in range(20):
        left.advance(); right.advance()
        l = [r for r in left.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id')=='far']
        r = [r for r in right.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id')=='far']
        if l or r: break
        assert left.situation()==right.situation() and left.reports()==right.reports()
    assert l and r
    assert {g['id']:g for g in left.situation()['population']}['far']['exposed'] is False
    assert {g['id']:g for g in right.situation()['population']}['far']['exposed'] is True

    # Controlled post-step boundary contact is unscored and checkpointable.
    # This injects fire state and RNG solely to exercise the censor branch.
    import random
    class ZeroRandom(random.Random):
        def random(self): return 0.0
    post = make(); host = post._host
    assert host._fire.change({'ignite':[[1,1]]})
    fire = host._fire._fire
    fire.cells[1][1].update(heat=1.,age=fire.spread_interval(0,-1)-1)
    fire.rng = ZeroRandom(7)
    post._seal = api._hash(host.checkpoint())
    assert post.advance()['status']=='censored' and host._minute==1
    assert 'population' not in post.situation() and post._outcome()['graded_score']==0
    assert verify_outcome(post,fresh_host=factory,expected_identity=identity,scoring_mask=mask)['score'] is None
    post_checkpoint = json.loads(json.dumps(post.checkpoint()))
    post_restored = make()
    post_restored.restore(post_checkpoint,expected_checkpoint_sha256=api._hash(post_checkpoint))
    assert api._encoded(post_restored.checkpoint())==api._encoded(post_checkpoint)

    # Inject a pre-step boundary censor; it is not natural boundary-spread evidence.
    censored = make(); assert not censored._host._fire.change({'ignite':[[0,0]]})
    censored._seal = api._hash(censored._host.checkpoint())
    assert censored.advance()['status']=='censored'
    assert 'population' not in censored.situation()
    result = verify_outcome(censored,fresh_host=factory,expected_identity=identity,scoring_mask=mask)
    assert result['status']=='censored' and result['score'] is None and censored._outcome()['graded_score']==0
    try: censored.reports()
    except api.CensoredError: pass
    else: raise AssertionError('censored reports exposed')


if __name__ == '__main__':
    main()
