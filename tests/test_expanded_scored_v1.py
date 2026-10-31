"""End-to-end private scoring regression on a synthetic expanded exercise."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_scored_integration():
    paths = ['envs', 'envs/wildfire_extended_inputs_v1', 'envs/wildfire_response/core/src',
             'envs/wildfire_containment_v1/src', 'envs/wildfire_containment_score_v1/src', '.']
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in paths))
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr


def test_independent_inherited_and_new_exposure():
    # Standalone audit arithmetic, not a substitute for population v2 replay.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(
        ROOT/'envs/wildfire_containment_score_v1/src'))
    program = """
from wildfire_containment_score_v1.scoring import recompute
handover = {'inherited': {'count': 4, 'exposed': 4, 'status': 'at_home'},
            'new': {'count': 6, 'exposed': 0, 'status': 'at_home'}}
last = {'inherited': {'count': 4, 'exposed': 4, 'status': 'at_home'},
        'new': {'count': 6, 'exposed': 6, 'status': 'at_home'}}
records = [dict(minute=0, footprint=[0], burnable=[0,1,2,3],
                cell_area_hectares=1., new_footprint=[], population=handover)]
records.extend(dict(minute=m, new_footprint=[1] if m == 1 else [],
                    population=last) for m in range(1,1441))
out = recompute(records, scoring_mask=[0,1,2,3], terminal=True,
                abandoned=False, censored=False)
assert out['inherited_exposed_population'] == 4
assert out['new_exposed_population'] == 6
assert out['protected_population_fraction'] == 0
assert out['inherited_burned_hectares'] == 1
assert out['additional_burned_hectares'] == 1
assert abs(out['graded_score'] - .2 * 2 / 3) < 1e-12
"""
    result = subprocess.run([sys.executable, '-c', program], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
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
    from wildfire_expanded_full_v1.scored import ExpandedScoredV1, configuration_identity, verify_outcome

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
    case = dict(id='synthetic-scored', seed=7, initial_fire=[[16,16]], wind=[0.,0.], centers=['center-0'],
        warmup_minutes=2, weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
            relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)], events=[])
    case['sha256'] = api._hash(case)
    drafts = dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                  source_catalog_sha256='e'*64,cases=[case])
    drafts['sha256'] = api._hash(drafts)
    config = dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory():
        inputs = api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
        mixed = MixedInputsV2(inputs,((17,16,.8,.5),),tuple(sorted(dict(
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
            groups=[dict(id='near',count=4,position=[base['grid']['transform'][2]+16.5*500,
                                                    base['grid']['transform'][5]-16.5*500]),
                    dict(id='far',count=6,position=point('4:4'))],
            destinations=[dict(id='safe',capacity=10,position=point('8:4'))])
        return ExpandedPopulationV1(host,pop,expected_catalog_sha256=api._hash(pop),opt_in=True)

    mask = [y*n+x for y in range(5, 24) for x in range(5,24) if (x,y) != (17,16)]
    identity = configuration_identity(factory(), mask)  # test-only independent fixture pin
    def make():
        return ExpandedScoredV1(factory(),scoring_mask=mask,expected_identity=identity,opt_in=True)
    env = make()
    assert verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask)['status'] == 'unfinished'
    before = env.checkpoint()
    public = [env.situation(), env.reports(limit=200)]
    assert before == env.checkpoint()
    assert all(word not in json.dumps(public) for word in ('dose', 'scoring_mask', 'weather_schedule', 'audit', 'graded_score'))
    wrong = copy.deepcopy(identity); wrong['scoring_mask_sha256'] = '0' * 64
    try:
        ExpandedScoredV1(factory(), scoring_mask=mask, expected_identity=wrong, opt_in=True)
    except ValueError:
        pass
    else:
        raise AssertionError('incorrect external identity accepted')
    assert verify_outcome(env, fresh_population=factory, expected_identity=identity, scoring_mask=mask,
                          infrastructure_timeout=True)['status'] == 'timeout'
    # Population v1 has no handover exposure input, even in a burning cell.
    assert [16,16] in env._env._host._fire.burning()
    assert env._audit[0]['population']['near']['exposed'] == 0
    assert env.protect('far','shelter')['status'] == 'submitted'
    assert env._audit[1]['population']['near']['exposed'] == 4
    assert env.situation()['population'][0]['exposed'] is False
    route = env.route_options('crew-0',[6,4])['options'][0]
    env.dispatch_hold('crew-0',[6,4],route['id'],[[6,4],[7,4]],[7,4])
    env.advance(30)
    saved = json.loads(json.dumps(env.checkpoint()))
    resumed = make(); resumed.restore(saved)
    broken = copy.deepcopy(saved); broken['audit'][1]['new_footprint'].append(n*n-1)
    broken['sha256'] = api._hash({k:v for k,v in broken.items() if k != 'sha256'})
    try:
        resumed.restore(broken)
    except ValueError:
        pass
    else:
        raise AssertionError('corrupt checkpoint accepted')
    resumed.restore(saved)
    assert env.advance(60) == resumed.advance(60)
    assert env.checkpoint() == resumed.checkpoint()
    env = resumed
    while env._env._host._minute < 1440 and not env._env._host._censored:
        env.advance(60)
    assert env._env._host._complete
    grade = verify_outcome(env,fresh_population=factory,expected_identity=identity,scoring_mask=mask)
    assert grade['status'] == 'handover_exposure_unavailable'
    assert not grade['verified'] and not grade['score_eligible'] and grade['score'] is None
    audit = env._audit
    inherited = set(audit[0]['footprint'])
    new = {cell for row in audit for cell in row['new_footprint']}
    expected_burned = (new - inherited) & (set(mask) - inherited)
    first, last = audit[0]['population'], audit[-1]['population']
    total = sum(g['count'] - g['exposed'] for g in first.values())
    exposure = sum(last[k]['exposed'] - g['exposed'] for k,g in first.items())
    retained = set(mask) - inherited
    expected = .8 * (1 - exposure / total if total else 1) + .2 * (1 - len(expected_burned) / len(retained) if retained else 1)
    # The private v1 counter cannot distinguish inherited from new exposure.
    assert abs(env._outcome()['graded_score'] - expected) < 1e-12
    assert inherited & set(mask) and env._counter.inherited == inherited
    assert expected_burned and env._counter.additional == expected_burned
    fire = env._env._host._fire._fire
    physical = {y * fire.width + x for y, row in enumerate(fire.cells)
                for x, cell in enumerate(row) if cell['heat'] > 0 or cell['burned'] > 0}
    assert expected_burned == (physical - inherited) & set(mask)
    assert exposure > 0
    assert env._env._ops.groups['far']['status'] == 'sheltering'
    assert env._env._host._fire._fire._containment._lines
    assert verify_outcome(env, fresh_population=factory, expected_identity=wrong, scoring_mask=mask)['status'] == 'verification_failed'
    assert verify_outcome(env, fresh_population=factory, expected_identity=identity, scoring_mask=mask[1:])['status'] == 'verification_failed'
    assert verify_outcome(env, fresh_population=factory, expected_identity=identity, scoring_mask=mask,
                          infrastructure_timeout=True)['status'] == 'timeout'
    env._calls[0]['result']['status'] = 'tampered'
    assert verify_outcome(env, fresh_population=factory, expected_identity=identity, scoring_mask=mask)['status'] == 'handover_exposure_unavailable'

    # Fault injection, not evidence of a naturally occurring boundary event.
    import random
    from eval import incident_weather_v1 as weather
    class ZeroRandom(random.Random):
        def random(self): return 0.0
    censored = make()
    host = censored._env._host
    assert host._fire.change({'ignite': [[1,1]]})
    row = weather.conditions(host._weather._profile, 1)
    f = host._fire._fire
    f.wind = tuple(row['wind'])
    f.cells[1][1].update(heat=1., age=f.spread_interval(0,-1)-1)
    f.rng = ZeroRandom(7)
    censored._seal = api._hash(censored._env.checkpoint())
    assert censored.advance()['status'] == 'censored'
    assert 'population' not in censored.situation()
    assert verify_outcome(censored, fresh_population=factory, expected_identity=identity, scoring_mask=mask)['status'] == 'censored'


if __name__ == '__main__':
    main()
