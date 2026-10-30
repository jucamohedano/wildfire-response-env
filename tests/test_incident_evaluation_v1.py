"""Scenario diversity and source-map integration for the new evaluation pack."""
import copy
import os
from pathlib import Path

import pytest

from eval.generate_incident_evaluation_v1 import TEMPLATES, VERSION, build_catalog
from eval.incident_eval_bridge_v1 import IncidentEvalBridgeV1
from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1
from eval.incident_weather_v1 import WeatherProfile
from eval.hub_runtime import make_environment
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_control_v3.population_engine_v3 import PopulationEngineV3


DATA = Path(os.environ.get('WILDFIRE_EVAL_DATA', '.runtime/wildfire-response-spain'))


def test_templates_have_substantive_distinct_dimensions():
    assert len(TEMPLATES) == 12
    assert len({item['family'] for item in TEMPLATES}) == len(TEMPLATES)
    assert {item['radius'] for item in TEMPLATES} == {2, 3, 4}
    assert {item['warmup'] for item in TEMPLATES} >= {60, 120, 180, 240}
    assert {item.get('ground_waves', 3) for item in TEMPLATES} == {2, 3}
    assert any(item.get('reserve_crew') for item in TEMPLATES)
    assert any(item.get('reports') == 'conflicting' for item in TEMPLATES)
    assert any(any(row['rain_mm_per_hour'] > 0 for row in item['weather']) for item in TEMPLATES)
    assert any(any(row['rain_mm_per_hour'] == 0 for row in item['weather']) for item in TEMPLATES)
    for item in TEMPLATES:
        WeatherProfile.from_records(item['weather'])


@pytest.fixture(scope='module')
def compiled():
    if not (DATA/'manifest.json').is_file():
        pytest.skip('set WILDFIRE_EVAL_DATA to the pinned source-map data pack')
    env, _ = make_environment(DATA, 'es-ma-2021', allow_unapproved_data=True)
    catalog, evidence = build_catalog(env.inputs, env.network)
    return env, catalog, evidence


def test_compiled_identity_preflight_and_groups(compiled):
    env, catalog, evidence = compiled
    assert catalog['version'] == VERSION
    assert catalog['sha256'] == digest({key:value for key,value in catalog.items() if key != 'sha256'})
    assert len(catalog['cases']) == len(evidence) == 12
    assert len({case['id'] for case in catalog['cases']}) == 12
    assert len({case['seed'] for case in catalog['cases']}) == 12
    assert len({case['sha256'] for case in catalog['cases']}) == 12
    assert len({case['family'] for case in catalog['cases']}) == 12
    assert all(row['handover_footprint_cells'] * 25 > 500 for row in evidence)
    assert max(row['handover_footprint_cells'] for row in evidence) >= 3 * min(row['handover_footprint_cells'] for row in evidence)
    assert {row['crew_slots'] for row in evidence} >= {4, 6, 7}
    assert all(all(count > 0 for count in row['geometric_work_approaches'].values()) for row in evidence)
    assert any(row['first_unit_available_at']['crew'] > 0 for row in evidence)
    assert next(row for row in evidence if row['family'] == 'wind_shift_access')['initial_wind_counterfactual'] == {
        'lost_approach':True, 'alternative_remains':True}
    for case in catalog['cases']:
        assert case['sha256'] == digest({key:value for key,value in case.items() if key != 'sha256'})
        assert all(env.inputs['fuel_known_mask'][y][x] for x,y in case['initial_fire'])
        assert all(row['minute'] <= 1440 for row in case['weather_schedule'])


def test_generator_catalog_works_with_bridge_and_weather_without_future_leak(compiled):
    base, catalog, evidence = compiled
    bridge = IncidentEvalBridgeV1(copy.deepcopy(catalog))
    assert bridge.pins['catalog_sha256'] == catalog['sha256']
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        for case, preflight in zip(catalog['cases'],evidence):
            env = PopulationEngineV3(base.inputs,base.network.document,
                expected_inputs_sha256=base.inputs['sha256'],
                expected_network_sha256=base.network.sha256,allow_unapproved_data=True)
            observation, _ = env.reset(case['id'],case['seed'])
            assert env._prehistory['footprint_hectares'] == preflight['handover_footprint_cells'] * 25
            assert all(report['observed_at'] <= report['received_at'] <= observation['minute']
                       for report in observation['reports'])
            weather = [report for report in observation['reports'] if report['kind'] == 'weather_update']
            assert len(weather) == 1 and weather[0]['observed_at'] == weather[0]['received_at'] == 0
            assert 'weather_schedule' not in str(observation) and 'incoming_reports' not in str(observation)


def test_compiled_exercises_have_causal_event_shapes(compiled):
    _, catalog, _ = compiled
    cases = {case['family']:case for case in catalog['cases']}
    access = cases['ground_access_restriction']
    assert any(event['kind'] == 'access_restriction' and event['minute'] == 90 for event in access['events'])
    closure = cases['occupied_destination_closure']
    destination = next(event['destination_id'] for event in closure['events'] if event['kind'] == 'destination_closed')
    assert any(group['position'] == next(d['position'] for d in closure['destinations'] if d['id'] == destination)
               and group['destination_id'] == destination for group in closure['groups'])
    reports = cases['conflicting_incoming_calls']['incoming_reports']
    assert len(reports) == 3 and reports[0]['location'] == reports[2]['location']
    assert reports[0]['minute'] == reports[1]['minute'] and reports[0]['text'] == reports[1]['text']
    assert 'flames' in reports[0]['text'] and 'no flames' in reports[2]['text']


def _engine(base):
    return PopulationEngineV3(base.inputs,base.network.document,
        expected_inputs_sha256=base.inputs['sha256'],expected_network_sha256=base.network.sha256,
        allow_unapproved_data=True)


def _advance_to(env, minute):
    observation = env.get_situation()
    while observation['minute'] < minute:
        observation, _, terminated, truncated, _ = env.step({'advance_minutes':1})
        assert not (terminated or truncated), 'case ended before its intended exercise'
    return observation


def _reports(env):
    items, cursor = [], 0
    while True:
        page = env.get_reports(after_cursor=cursor, limit=20)
        items.extend(page)
        cursor += len(page)
        if len(page) < 20:
            return items


def test_wind_shift_changes_public_routes_after_delivery(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        observation, _ = env.reset('incident-eval-v1-002')
        before = {tuple(option['approach_grid']) for option in env.route_options('engine-0')['options']}
        observation = _advance_to(env,15)
        assert observation['wind'] == [1,0]  # New physical wind has not arrived yet.
        while observation['minute'] < 30 and not any(
                report['kind']=='weather_update' and report['observed_at']==15
                for report in _reports(env)):
            observation = _advance_to(env,observation['minute']+1)
        assert observation['wind'] == [0,2]
        after = {tuple(option['approach_grid']) for option in env.route_options('engine-0')['options']}
        assert before - after and after


def test_rain_changes_spread_proxy_and_is_delivered(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        env.reset('incident-eval-v1-003')
        _advance_to(env,240)
        before = env.fire.rules['spread_factor']
        observation = _advance_to(env,300)
        after = env.fire.rules['spread_factor']
        assert after < before
        reports = _reports(env)
        assert any(r['kind']=='weather_update' and r['observed_at']==240
                   and r['payload']['rain_mm_per_hour'] == 4 for r in reports)
        assert observation['minute'] == 300
    # Same initial state, seed, wind, temperature and humidity; only rain differs.
    dry = copy.deepcopy(catalog)
    case = next(item for item in dry['cases'] if item['id'] == 'incident-eval-v1-003')
    for row in case['weather_schedule']:
        row['rain_mm_per_hour'] = 0
    case['sha256'] = digest({key:value for key,value in case.items() if key != 'sha256'})
    dry['sha256'] = digest({key:value for key,value in dry.items() if key != 'sha256'})
    proposed = IncidentWeatherRuntimeV1(dry, expected_pins={})
    with IncidentWeatherRuntimeV1(dry, expected_pins=proposed.pins):
        env = _engine(base)
        env.reset('incident-eval-v1-003')
        _advance_to(env,300)
        assert after < env.fire.rules['spread_factor']


def test_wind_closes_and_reopens_aircraft_window(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        observation, _ = env.reset('incident-eval-v1-005')
        assert observation['aircraft_operable'] is True
        observation = _advance_to(env,190)
        assert observation['wind'] == [2.9,0]
        assert observation['aircraft_operable'] is False
        observation = _advance_to(env,260)
        assert observation['wind'] == [1,0]
        assert observation['aircraft_operable'] is True


def test_reported_engine_access_restriction_preserves_crew_approach(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        env.reset('incident-eval-v1-004')
        assert env.route_options('engine-0')['options']
        observation = _advance_to(env,100)
        assert 'engine' in observation['access_restrictions']
        assert not env.route_options('engine-0')['options']
        assert env.route_options('crew-0')['options']


def test_night_case_opens_after_dawn_without_synthetic_outage(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        observation, _ = env.reset('incident-eval-v1-009')
        assert observation['aircraft_operable'] is False
        observation = _advance_to(env,600)
        assert observation['aircraft_operable'] is True
        assert not any(event['kind']=='aircraft_unavailable' for event in
                       next(case for case in catalog['cases'] if case['id']=='incident-eval-v1-009')['events'])


def test_conflicting_reports_are_delivered_and_duplicate_does_not_wake(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        env.reset('incident-eval-v1-008')
        _advance_to(env,35)
        reports = [r for r in _reports(env) if r['kind']=='incoming_report']
        assert len(reports) == 3
        assert reports[0]['observed_at'] == reports[2]['observed_at'] == 20
        assert reports[0]['text'] == reports[2]['text'] and reports[2]['actionable'] is False
        assert 'no flames' in reports[1]['text'] and reports[1]['location'] == reports[0]['location']


def test_occupied_destination_closure_reaches_public_group(compiled):
    base, catalog, _ = compiled
    proposed = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=proposed.pins):
        env = _engine(base)
        obs, _ = env.reset('incident-eval-v1-006')
        group = next(g for g in obs['districts'] if g['id']=='population-1')
        assert group['status'] == 'arrived'
        obs = _advance_to(env,490)
        group = next(g for g in obs['districts'] if g['id']=='population-1')
        reports = _reports(env)
        assert group['status'] == 'blocked'
        assert any(r['kind']=='relocation_required' and r['payload']['group']['id']=='population-1'
                   for r in reports)
