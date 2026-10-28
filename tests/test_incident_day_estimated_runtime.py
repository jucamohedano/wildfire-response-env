import copy
import gzip
import json
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.data import digest, load_inputs, prepare_inputs
from envs.wildfire_response.incident_day.fuel_estimation import build_estimates
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.scenarios import DayFire
from wildfire_core.incident_day import CALIBRATED_CASE_IDS, IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from wildfire_core.mobility_routing import make_journey, make_leg
from test_incident_day_fuel_estimation import fixture, pin, polygon


def estimated_pack():
    prepared, domain, probe = fixture([polygon(2002, .5, 1.5)])
    domain.update(elevations_metres=[[100.] * 5], height_support_mask=[[1] * 5])
    pin(domain)
    document = build_estimates(prepared, domain, probe)
    original = json.loads((Path(__file__).parent.parent / 'data/incident_day_v2/synthetic_fixture.json').read_text())
    pack = prepare_inputs(prepared, domain, original['network'],
                          fuel_resolution={'kind': 'estimated', 'document': document})
    return pack, document


def test_estimates_load_only_by_explicit_opt_in_and_do_not_become_measured():
    pack, document = estimated_pack()
    assert pack['fuel_known_mask'] == [[1, 0, 1, 0, 1]]
    assert pack['fuel_classes'] == document['fuel_classes']
    assert pack['approval_status'] == 'unapproved_source_pack'
    with pytest.raises(ValueError, match='unapproved'):
        load_inputs(pack, pack['sha256'])
    assert load_inputs(pack, pack['sha256'], allow_unapproved=True) == pack
    tampered = copy.deepcopy(pack)
    tampered['fuel_classes'][0][1] = 8
    tampered['sha256'] = digest({key: value for key, value in tampered.items() if key != 'sha256'})
    with pytest.raises(ValueError, match='Fuel differs'):
        load_inputs(tampered, tampered['sha256'], allow_unapproved=True)


def test_urban_mixed_cells_do_not_turn_into_forest_or_an_artificial_firebreak():
    pack, document = estimated_pack()
    fire = DayFire(pack, 17)
    urban = fire.cells[0][1]
    assert urban['terrain'] == 'built'
    assert urban['initial_fuel'] == pytest.approx(.75 * .35)
    assert urban['initial_fuel'] > 0
    assert document['cells'][1]['land_kind'] == 'unresolved'


def test_unknown_land_guard_censors_instead_of_blocking_fire_or_grading_success():
    inputs, network = fixture_inputs()
    environment = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                 expected_network_sha256=network['sha256'])
    environment.reset()
    environment._uncertain_land = {(19, 16)}
    observation, reward, terminated, truncated, info = environment.advance_time(60)
    assert truncated and not terminated and reward == 0
    assert info['wake_reason'] == 'input_uncertainty_censored'
    assert info['model_limit'] == 'unresolved_land_or_water_not_a_firebreak'
    assert not environment.outcomes()['score_eligible']
    assert environment.fire.cells[16][19]['fuel'] > 0


def test_unsuccessful_ground_work_preserves_water_and_requests_repositioning():
    resource = dict(id='engine', kind='engine', home_base='base', position=[0, 0],
                    base_position=[0, 0], state='working', work_remaining=30,
                    capacity_litres=3500, water_litres=2500)
    operations = Operations([resource], [], [], {})
    events = []
    operations.tick(1, wind=0, daylight=True, hazard_check=lambda position, kind: True,
                    suppress=lambda unit: False, emit=lambda *arguments, **keywords: events.append(arguments))
    unit = operations.resources['engine']
    assert unit['water_litres'] == 2500 and unit['work_remaining'] == 30
    assert unit['state'] == 'needs_assistance'
    assert events[0][1] == 'work_target_unavailable'


def flight(origin, destination):
    return make_journey([make_leg('flight', [origin, destination], 60)], origin, destination, 'exercise test')


def test_aircraft_sortie_must_fit_the_declared_shift_including_recovery():
    unit = dict(id='plane', kind='aircraft', home_base='base', position=[0, 0], base_position=[0, 0],
                state='available', work_remaining=30, capacity_litres=100, water_litres=100,
                fuel_minutes=100, fuel_capacity_minutes=100, duty_end_at=3)
    operations = Operations([unit], [], [], {'require_sortie_within_shift': True},
        return_planner=lambda resource, minute: flight(resource['position'], resource['base_position']))
    operations.set_flight_conditions(0, True)
    operations.clear_aircraft('plane', 0)
    assert not operations.dispatch('plane', 'sector', [0, 0], flight([0, 0], [1000, 0]), 0)
    operations.resources['plane']['duty_end_at'] = 30
    assert operations.dispatch('plane', 'sector', [0, 0], flight([0, 0], [1000, 0]), 0)


def test_no_target_aircraft_recovers_without_spending_a_drop():
    unit = dict(id='plane', kind='aircraft', home_base='base', position=[1000, 0], base_position=[0, 0],
                state='working', clearance=True, work_remaining=30, capacity_litres=100, water_litres=100)
    operations = Operations([unit], [], [], {},
        return_planner=lambda resource, minute: flight(resource['position'], resource['base_position']))
    operations.tick(1, wind=0, daylight=True, hazard_check=lambda position, kind: True,
                    suppress=lambda resource: False, emit=lambda *arguments, **keywords: None)
    current = operations.resources['plane']
    assert current['state'] == 'returning'
    assert current['water_litres'] == 100 and current['work_remaining'] == 30


def test_sorted_disk_checkpoint_keeps_multi_resource_physics_and_public_order(tmp_path):
    inputs, network = fixture_inputs()
    arguments = dict(expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
    environment = IncidentDayEnv(inputs, network, **arguments)
    observation, unused = environment.reset(CALIBRATED_CASE_IDS[1])
    policy = DayDispatcher()
    while environment.minute < 120:
        observation, unused_reward, terminated, truncated, info = environment.step(policy.action(environment, observation))
        assert not terminated and not truncated
    checkpoint = tmp_path / 'state.json.gz'
    environment.save_checkpoint(checkpoint)
    other = IncidentDayEnv(inputs, network, **arguments)
    other.restore(json.loads(gzip.decompress(checkpoint.read_bytes())))
    assert other.get_situation() == environment.get_situation()
    for unused in range(10):
        assert other.advance_time(20) == environment.advance_time(20)
    assert other.checkpoint() == environment.checkpoint()
