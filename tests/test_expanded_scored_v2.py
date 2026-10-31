"""Synthetic end-to-end scoring, privacy and replay over population v2."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_scored_v2_full_day():
    paths = ['envs', 'envs/wildfire_extended_inputs_v1', 'envs/wildfire_response/core/src',
             'envs/wildfire_containment_v1/src', 'envs/wildfire_containment_score_v1/src', '.']
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in paths))
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import json
    import random
    import adapter_v1 as api
    from eval import incident_weather_v1 as weather
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from wildfire_expanded_incident_v1.host import CATALOG_VERSION as HOST_CATALOG
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_expanded_full_v1.hold import WeatherMixedHoldV1
    from wildfire_expanded_population_v2 import ExpandedPopulationV2, CATALOG_VERSION
    from wildfire_expanded_scored_v2 import (ExpandedScoredV2, configuration_identity,
        mixed_factors_sha256, verify_outcome)
    from wildfire_containment_score_v1.scoring import recompute

    base, network = fixture_inputs()
    for edge in network['edges']:
        edge['allowed_modes'] = ['crew', 'engine']
    network['sha256'] = api._hash({k:v for k,v in network.items() if k != 'sha256'})
    n = base['grid']['width']
    document = dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)], surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']), elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17] = 'mixed'
    document['access'][16][17] = 'unknown'
    point = lambda key: next(node['position'] for node in network['nodes'] if node['id'] == key)
    center = lambda x,y: [base['grid']['transform'][2]+(x+.5)*500,
                          base['grid']['transform'][5]-(y+.5)*500]
    case = dict(id='synthetic-scored-v2', seed=7, initial_fire=[[16,16]], wind=[0.,0.], centers=['center-0'],
        warmup_minutes=2, weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
            relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)], events=[])
    case['sha256'] = api._hash(case)
    drafts = dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                  source_catalog_sha256='e'*64,cases=[case])
    drafts['sha256'] = api._hash(drafts)
    config = dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    groups = [dict(id='inherited',count=4,position=center(16,16),initial_exposed=True,handover_exposure=None),
              dict(id='new',count=3,position=center(15,15),initial_exposed=False,handover_exposure=None),
              dict(id='far',count=6,position=point('4:4'),initial_exposed=False,handover_exposure=None)]

    def factory(roster=groups, *, fuel_factor=.8, crossing=.5):
        inputs = api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
        mixed = MixedInputsV2(inputs,((17,16,fuel_factor,crossing),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        sc = dict(id=case['id'],seed=case['seed'],initial_fire=case['initial_fire'],wind=case['wind'],
                  center='center-0',resources=[dict(id='crew-0',kind='crew',position=point('2:2'),
                                                   work_remaining=240,water_litres=0)])
        catalog = dict(version=HOST_CATALOG,role='synthetic_fixture_only',inputs_sha256=inputs.document_sha256,
                       network_sha256=network['sha256'],cases=[sc])
        host = WeatherMixedHoldV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            network=network,expected_network_sha256=network['sha256'],catalog=catalog,
            expected_catalog_sha256=api._hash(catalog),containment_config=config,pressure_scale=1,opt_in=True)
        pop = dict(version=CATALOG_VERSION,role='synthetic_fixture_only',case_id=case['id'],
            inputs_sha256=inputs.document_sha256,network_sha256=network['sha256'],
            groups=copy.deepcopy(roster),destinations=[dict(id='safe',capacity=10,position=point('8:4'))])
        return ExpandedPopulationV2(host,pop,expected_catalog_sha256=api._hash(pop),opt_in=True)

    mask = [y*n+x for y in range(5,24) for x in range(5,24) if (x,y) != (17,16)]
    identity = configuration_identity(factory(), mask)  # fixture-only candidate pin
    # Independently approved fixture factors, never sourced from the episode under test.
    assert identity['mixed_factors_sha256'] == mixed_factors_sha256([(17,16,.8,.5)])
    assert mixed_factors_sha256([(17,16,.8,.5)]) != mixed_factors_sha256([(17,16,.8,.95)])
    assert mixed_factors_sha256([(17,16,.8,.5)]) != mixed_factors_sha256([(17,16,.7,.5)])
    assert configuration_identity(factory(fuel_factor=.7),mask)['mixed_factors_sha256'] == mixed_factors_sha256([(17,16,.7,.5)])
    assert mixed_factors_sha256([(17,16,.8,.5),(2,3,.7,.4)]) == mixed_factors_sha256([(2,3,.7,.4),(17,16,.8,.5)])
    for invalid in ([(17,16,.8,.5),(17,16,.7,.4)], [(17,16,.8,True)], [(17,16,float('nan'),.5)]):
        try: mixed_factors_sha256(invalid)
        except ValueError: pass
        else: raise AssertionError('invalid mixed factor inventory accepted')
    # Exact reviewer reproduction: equal supplied identity and underlying checkpoint,
    # but a different crossing factor changes the effective fire behavior.
    first, second = factory(), factory(crossing=.95)
    assert api._encoded(first._identity) == api._encoded(second._identity)
    assert api._encoded(first.checkpoint()) == api._encoded(second.checkpoint())
    assert first._inner._host._fire._inner._fire._mixed_openness[(17,16)] == .5
    assert second._inner._host._fire._inner._fire._mixed_openness[(17,16)] == .95
    second_identity = configuration_identity(second, mask)
    assert second_identity['mixed_factors_sha256'] != identity['mixed_factors_sha256']
    assert {k:v for k,v in second_identity.items() if k != 'mixed_factors_sha256'} == {
        k:v for k,v in identity.items() if k != 'mixed_factors_sha256'}
    try:
        ExpandedScoredV2(second,scoring_mask=mask,expected_identity=identity,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('different effective crossing factor accepted against trusted pin')
    alternate = ExpandedScoredV2(factory(crossing=.95),scoring_mask=mask,
                                 expected_identity=second_identity,opt_in=True)
    def make():
        return ExpandedScoredV2(factory(),scoring_mask=mask,expected_identity=identity,opt_in=True)
    env = make()
    from wildfire_expanded_full_v1.scored import verify_outcome as verify_v1
    assert verify_v1(env, fresh_population=factory, expected_identity=identity, scoring_mask=mask)['status'] == 'verification_failed'
    host = env._env._inner._host
    assert [16,16] in host._fire.burning()
    initial = env._audit[0]['population']
    assert initial['inherited']['exposed'] == 4 and initial['new']['exposed'] == 0
    assert env._counter.initial_exposure == 4
    public = {g['id']:g for g in env.situation()['population']}
    assert public['inherited']['exposed'] is None and public['new']['exposed'] is None
    assert all(word not in json.dumps([env.situation(),env.reports(limit=200)])
               for word in ('initial_exposed','dose','scoring_mask','mixed_factors','weather_schedule','audit','graded_score'))
    assert verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask)['status'] == 'unfinished'
    before = env.checkpoint()
    assert before['mixed_factors'] == [[17,16,.8,.5]]
    assert alternate.checkpoint()['mixed_factors'] == [[17,16,.8,.95]]
    assert api._encoded(alternate.checkpoint()) != api._encoded(before)
    try: alternate.restore(before)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('checkpoint accepted with different crossing factors')
    # Re-hashing a changed private factor inventory cannot override the external pin.
    altered = copy.deepcopy(before); altered['mixed_factors'][0][3] = .95
    altered['sha256'] = api._hash({k:v for k,v in altered.items() if k != 'sha256'})
    try: env.restore(altered)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered factor inventory accepted')
    env.restore(before)
    # Every live copy that affects physics or the censor is checked; trusted
    # checkpoint restoration reconstructs a clean backend after corruption.
    for target in ('backend_crossing','live_crossing','fuel_factor','fuel_cell','censor'):
        backend = env._env._inner._host._fire._inner
        if target == 'backend_crossing': backend._mixed_openness[(17,16)] = .95
        elif target == 'live_crossing': backend._fire._mixed_openness[(17,16)] = .95
        elif target == 'fuel_factor': backend._mixed_factors[(17,16)] = .7
        elif target == 'fuel_cell': backend._fire.cells[16][17]['initial_fuel'] += .1
        else: backend._censor._allowed = frozenset()
        try: env.situation()
        except (ValueError,RuntimeError): pass
        else: raise AssertionError('altered live ' + target + ' was accepted')
        env.restore(before)
    assert env.checkpoint() == before
    from dataclasses import replace
    weather_host = env._env._inner._host._weather
    original_inputs = weather_host._inputs
    weather_host._inputs = replace(original_inputs, factors=((17,16,.8,.95),))
    try: env.situation()
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('changed mixed input was accepted')
    try: env.restore(before)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('restore accepted different declared mixed factors')
    weather_host._inputs = original_inputs
    env.restore(before)
    env.situation(); env.reports(limit=200)
    assert env.checkpoint() == before
    bad = copy.deepcopy(identity); bad['population']['catalog'] = '0'*64
    try:
        ExpandedScoredV2(factory(),scoring_mask=mask,expected_identity=bad,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong population catalog pin accepted')
    bad_source = copy.deepcopy(identity); bad_source['population']['source'] = '0'*64
    try:
        ExpandedScoredV2(factory(),scoring_mask=mask,expected_identity=bad_source,opt_in=True)
    except ValueError: pass
    else: raise AssertionError('wrong population source pin accepted')
    assert env.protect('far','shelter')['status'] == 'submitted'
    route = env.route_options('crew-0',[6,4])['options'][0]
    env.dispatch_hold('crew-0',[6,4],route['id'],[[6,4],[7,4]],[7,4])
    env.advance(15)
    assert env._audit[-1]['population']['new']['exposed'] == 0
    env.advance(1)
    assert env._audit[-1]['population']['new']['exposed'] == 3
    assert {g['id']:g for g in env.situation()['population']}['new']['exposed'] is None
    saved = json.loads(json.dumps(env.checkpoint()))
    resumed = make(); resumed.restore(saved)
    assert resumed._audit[0]['population']['inherited']['exposed'] == 4
    broken = copy.deepcopy(saved)
    broken['audit'][0]['population']['inherited']['exposed'] = 0
    broken['sha256'] = api._hash({k:v for k,v in broken.items() if k != 'sha256'})
    try: resumed.restore(broken)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('altered handover exposure accepted')
    resumed.restore(saved)
    assert env.advance(60) == resumed.advance(60)
    assert env.checkpoint() == resumed.checkpoint()
    env = resumed
    while env._env._inner._host._minute < 1440 and not env._env._inner._host._censored:
        env.advance(60)
    assert env._env._inner._host._complete
    # A fresh factory with identical declared source identity but different
    # effective crossing cannot be used to validate a completed episode.
    mismatch = verify_outcome(env,fresh_population=lambda: factory(crossing=.95),
                              expected_identity=identity,scoring_mask=mask)
    assert mismatch['status'] == 'verification_failed' and mismatch['score'] is None
    grade = verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask)
    assert grade['status'] == 'full_day' and grade['verified'] and grade['score'] is not None, grade['status']
    outcome = recompute(env._audit,scoring_mask=mask,terminal=True,abandoned=False,censored=False)
    assert outcome['inherited_exposed_population'] == 4 and outcome['new_exposed_population'] == 3
    assert abs(outcome['protected_population_fraction'] - 2/3) < 1e-12
    first = set(env._audit[0]['footprint'])
    new = {cell for row in env._audit for cell in row['new_footprint']}
    expected_burned = (new - first) & set(mask)
    assert first & set(mask) and expected_burned
    fire = env._env._inner._host._fire._fire
    physical = {y*fire.width+x for y,row in enumerate(fire.cells) for x,cell in enumerate(row)
                if cell['heat'] > 0 or cell['burned'] > 0}
    assert expected_burned == (physical-first) & set(mask)
    retained = set(mask) - first
    expected_score = .8 * (2/3) + .2 * (1 - len(expected_burned)/len(retained))
    assert abs(grade['score'] - expected_score) < 1e-12
    assert env._env._inner._ops.groups['far']['status'] == 'sheltering'
    assert env._env._inner._host._fire._fire._containment._lines
    assert verify_outcome(env,fresh_population=factory,expected_identity=bad,scoring_mask=mask)['status'] == 'verification_failed'
    assert verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask[1:])['status'] == 'verification_failed'
    assert verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask,
                          infrastructure_timeout=True)['status'] == 'timeout'
    env._calls[0]['result']['status'] = 'tampered'
    assert verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask)['status'] == 'verification_failed'

    # Different hidden exposure, identical undisclosed public handover and calls.
    hidden = [dict(id='private',count=2,position=point('4:4'),initial_exposed=True,handover_exposure=None)]
    other = copy.deepcopy(hidden); other[0]['initial_exposed'] = False
    def private_case(roster):
        new_factory = lambda: factory(roster)
        pin = configuration_identity(new_factory(),mask)
        return ExpandedScoredV2(new_factory(),scoring_mask=mask,expected_identity=pin,opt_in=True)
    left, right = private_case(hidden), private_case(other)
    assert left._audit[0]['population']['private']['exposed'] == 2
    assert right._audit[0]['population']['private']['exposed'] == 0
    assert left.situation() == right.situation() and left.reports() == right.reports()
    assert left.situation()['population'][0]['exposed'] is None
    assert left.protect('private','shelter') == right.protect('private','shelter')
    assert left.situation() == right.situation() and left.reports() == right.reports()
    for _ in range(8):
        left.advance(); right.advance()
        l = [r for r in left.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id') == 'private']
        r = [r for r in right.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id') == 'private']
        if l or r: break
        assert left.situation() == right.situation() and left.reports() == right.reports()
    assert l and r
    assert left.situation()['population'][0]['exposed'] is True
    assert right.situation()['population'][0]['exposed'] is False

    # Controlled fault injection exercises the physical censor branch only.
    class ZeroRandom(random.Random):
        def random(self): return 0.0
    censored = make()
    host = censored._env._inner._host
    assert host._fire.change({'ignite': [[1,1]]})
    row = weather.conditions(host._weather._profile, 1)
    f = host._fire._fire
    f.wind = tuple(row['wind'])
    f.cells[1][1].update(heat=1., age=f.spread_interval(0,-1)-1)
    f.rng = ZeroRandom(7)
    censored._seal = api._hash(censored._env.checkpoint())
    assert censored.advance()['status'] == 'censored'
    assert 'population' not in censored.situation()
    result = verify_outcome(censored,fresh_population=factory,expected_identity=identity,scoring_mask=mask)
    assert result['status'] == 'censored' and result['score'] is None


if __name__ == '__main__':
    main()
