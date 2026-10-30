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
