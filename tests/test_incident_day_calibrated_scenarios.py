import copy
import math

import pytest

from wildfire_core.incident_day import fixture_inputs
from wildfire_core.mobility_routing import RoadNetwork, make_journey, make_leg
from envs.wildfire_response.incident_day.calibrated_scenarios import (
    SHIFT_CASE_IDS, SHIFT_PROFILE_VERSION, NoSafeStaging, _handover_burning, build_calibrated_scenario)
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.scenarios import CASE_IDS, PROFILE, build_scenario, grid_point


@pytest.fixture(scope='module')
def exercise_inputs():
    inputs, network_document = fixture_inputs()
    return inputs, RoadNetwork(network_document, network_document['sha256'])


@pytest.fixture(scope='module')
def scenarios(exercise_inputs):
    inputs, network = exercise_inputs
    return [build_calibrated_scenario(inputs, network, identifier) for identifier in SHIFT_CASE_IDS]


def test_all_six_keep_old_events_and_frozen_old_scenario(exercise_inputs, scenarios):
    inputs, network = exercise_inputs
    original_profile = copy.deepcopy(PROFILE)
    for index, scenario in enumerate(scenarios):
        old = build_scenario(inputs, network, CASE_IDS[index])
        assert scenario['id'] == SHIFT_CASE_IDS[index]
        assert scenario['seed'] == 3866400 + index
        assert scenario['family'] == old['family']
        assert scenario['events'] == old['events']
        assert scenario['initial_fire'] == old['initial_fire']
        assert scenario['horizon_minutes'] == 1440
        assert scenario['profile']['version'] == SHIFT_PROFILE_VERSION
        assert scenario['profile']['require_sortie_within_shift'] is True
        assert scenario['profile']['require_return_within_shift'] is True
        for key in ('spread_interval_multiplier', 'burn_duration_multiplier', 'aircraft_max_wind',
                    'flight_speed_kph', 'ground_setup_minutes', 'ground_refill_minutes'):
            assert scenario['profile'][key] == old['profile'][key]
        assert old == build_scenario(inputs, network, CASE_IDS[index])
    assert PROFILE == original_profile


def test_finite_named_ground_and_air_shifts(scenarios):
    for scenario in scenarios:
        ground = [unit for unit in scenario['resources'] if unit['kind'] != 'aircraft']
        air = [unit for unit in scenario['resources'] if unit['kind'] == 'aircraft']
        assert len(ground) == 12 and len(air) == 2
        assert len({unit['id'] for unit in scenario['resources']}) == 14
        for start in (0, 480, 960):
            wave = [unit for unit in ground if unit['duty_end_at'] == start + 480]
            assert {(unit['home_base'], unit['kind']) for unit in wave} == {
                ('center-0', 'engine'), ('center-0', 'crew'), ('center-1', 'engine'), ('center-1', 'crew')}
            if start:
                assert all(unit['available_at'] == start and unit['state'] == 'unavailable' for unit in wave)
        assert [(unit['available_at'], unit['duty_end_at']) for unit in air] == [(0, 480), (480, 960)]
        assert air[1]['state'] == 'unavailable'
        assert all(unit['fuel_minutes'] == 180 for unit in air)
    shortage = next(unit for unit in scenarios[5]['resources'] if unit['id'] == 'engine-1')
    assert shortage['available_at'] == 360 and shortage['state'] == 'unavailable'


def test_inherited_staging_has_safe_reachable_useful_work(exercise_inputs, scenarios):
    inputs, network = exercise_inputs
    center = [inputs['grid']['width'] // 2, inputs['grid']['height'] // 2]
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['walk_mask'])
    for scenario in scenarios:
        burning = _handover_burning(inputs, scenario)
        staged = [unit for unit in scenario['resources'] if unit['state'] == 'on_scene']
        assert len(staged) == (2 if scenario['family'] == 'wind_access_loss' else 1)
        for unit in staged:
            point = grid_point(inputs, unit['position'])
            assert router.safe_position(unit['position'], burning, scenario['wind'], mode=unit['kind'])
            assert any(math.dist(point, cell) <= 3 and math.dist(center, cell) <= 3 for cell in burning)
            options = router.options(unit['base_position'], center, mode=unit['kind'], burning=burning,
                                     wind=scenario['wind'], limit=32)['options']
            assert any(option['approach_point'] == unit['position'] for option in options)
            returning = router.options(unit['position'], grid_point(inputs, unit['base_position']), mode=unit['kind'],
                                       burning=burning, wind=scenario['wind'], target_metric=unit['base_position'], limit=1)
            assert returning['status'] == 'reachable' and returning['options']
            staging = next(item for item in scenario['handover_staging'] if item['resource_id'] == unit['id'])
            assert staging['return_travel_minutes'] == returning['options'][0]['journey']['duration_minutes']
        if scenario['family'] == 'wind_access_loss':
            crew = next(unit for unit in staged if unit['kind'] == 'crew')
            assert grid_point(inputs, crew['position'])[1] < center[1]
            assert not router.safe_position(crew['position'], burning, [0., -2.], mode='crew')


def test_rejects_unsupported_staging_and_wrong_seed(exercise_inputs, monkeypatch):
    inputs, network = exercise_inputs
    with pytest.raises(ValueError, match='seeds are pinned'):
        build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[0], 5)
    monkeypatch.setattr(HazardRouter, 'options', lambda *args, **kwargs: {'status': 'unreachable', 'options': []})
    with pytest.raises(ValueError, match='No reachable safe staging'):
        build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[0])


def test_deterministic_digest(exercise_inputs, scenarios):
    inputs, network = exercise_inputs
    assert build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[0]) == scenarios[0]


def test_staging_fails_when_outbound_options_have_no_return(exercise_inputs, monkeypatch):
    inputs, network = exercise_inputs
    original_options = HazardRouter.options

    def no_return_options(router, origin, target, **kwargs):
        if kwargs.get('target_metric') is not None:
            return {'status': 'unreachable', 'options': []}
        return original_options(router, origin, target, **kwargs)

    monkeypatch.setattr(HazardRouter, 'options', no_return_options)
    with pytest.raises(ValueError, match='legal mapped recovery'):
        build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[0])


def test_failed_access_fallback_uses_mapped_safe_return_and_preserves_stock(exercise_inputs, monkeypatch):
    inputs, network = exercise_inputs
    def unavailable_staging(*args, **kwargs):
        raise NoSafeStaging('No useful staging')

    monkeypatch.setattr('envs.wildfire_response.incident_day.calibrated_scenarios._staging', unavailable_staging)
    scenario = build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[0])
    engine = next(unit for unit in scenario['resources'] if unit['id'] == 'engine-0')
    metadata = scenario['handover_staging'][0]
    assert engine['state'] == 'returning' and engine['water_litres'] == 2500 and engine['work_remaining'] == 180
    assert engine['position'] == network.nodes[metadata['node_id']]
    assert 50 <= math.dist(engine['position'], engine['base_position']) <= 2000
    assert engine['journey']['origin'] == engine['position']
    assert engine['journey']['destination'] == engine['base_position']
    assert engine['journey']['departed_at'] == 0
    assert engine['journey']['arrives_at'] == engine['journey']['duration_minutes']
    assert metadata['kind'] == 'synthetic_failed_access_recovery'
    assert 'Partial mapped ground access' in scenario['handover_notice']


def test_shift_profile_rejects_aircraft_sortie_without_return_time(scenarios):
    scenario = scenarios[0]
    aircraft = copy.deepcopy(next(unit for unit in scenario['resources'] if unit['id'] == 'plane-0'))
    origin = aircraft['base_position']
    destination = [origin[0] + 20000, origin[1]]

    def flight_route(start, end):
        return make_journey([make_leg('flight', [start, end], 240)], start, end, 'exercise')

    def return_planner(unit, minute):
        return flight_route(unit['position'], unit['base_position'])

    operations = Operations([aircraft], [], [], copy.deepcopy(scenario['profile']), return_planner=return_planner)
    operations.set_flight_conditions([0, 0], True)
    operations.clear_aircraft('plane-0', 467)
    assert not operations.dispatch('plane-0', 'incident', [16, 16], flight_route(origin, destination), 467)
    operations = Operations([aircraft], [], [], copy.deepcopy(scenario['profile']), return_planner=return_planner)
    operations.set_flight_conditions([0, 0], True)
    operations.clear_aircraft('plane-0', 466)
    assert operations.dispatch('plane-0', 'incident', [16, 16], flight_route(origin, destination), 466)
