import copy
import json
from pathlib import Path

import pytest

from eval.check_incident_day_plausibility import exercise_gate, run, verify_status
from envs.wildfire_response.incident_day.plausibility import audit_case, counterfactual_case, route_checks, static_case
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


BOUNDS = json.loads((Path(__file__).resolve().parents[1] / 'data/incident_day_v2/plausibility/bounds-v1.json').read_text())


def test_completion_requires_replay_and_independent_reward():
    class CompletedEpisode:
        terminated = True
        truncated = False
        minute = 1440
        _scenario = {'horizon_minutes': 1440}

        def verify_episode(self):
            return dict(passed=True, exact_replay=True, independent_reward_agreement=False)

    verification, status = verify_status(CompletedEpisode())
    assert status == 'verification_failed'
    assert verification['passed'] is True and verification['independent_reward_agreement'] is False


def test_gate_passes_on_verified_cases_and_mechanics_without_observed_harm():
    static_cases = [dict(counterfactual=dict(status='demonstrated')) for unused in range(6)]
    assert exercise_gate(0, 0, True, True, static_cases) == 'passed'
    assert exercise_gate(1, 0, True, True, static_cases) == 'failed'
    assert exercise_gate(0, 1, True, True, static_cases) == 'failed'
    assert exercise_gate(0, 0, False, True, static_cases) == 'not established'
    static_cases[0]['counterfactual']['status'] = 'not_demonstrated'
    assert exercise_gate(0, 0, True, True, static_cases) == 'not established'


@pytest.mark.parametrize('index', range(6))
def test_shift_counterfactuals_demonstrate_intended_mechanics(index):
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
    env.reset(f'incident-day-shift-v1-{index:03}')
    assert counterfactual_case(env)['status'] == 'demonstrated'


def test_verification_exception_and_censored_episode_are_not_completed():
    class Episode:
        terminated = False
        truncated = True
        minute = 100
        _scenario = {'horizon_minutes': 1440}

        def verify_episode(self):
            return dict(passed=True, exact_replay=True, independent_reward_agreement=True)

    episode = Episode()
    assert verify_status(episode)[1] == 'censored'
    episode.verify_episode = lambda: (_ for _ in ()).throw(ValueError('broken replay'))
    verification, status = verify_status(episode)
    assert status == 'verification_failed' and 'broken replay' in verification['error']


@pytest.fixture
def fixture_environment():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
    env.reset('incident-day-dev-000')
    return env


def test_static_audit_detects_unreachable_inherited_assignment(fixture_environment):
    env = fixture_environment
    result = static_case(env._scenario, env.inputs, env.network, env._burning(), BOUNDS)
    engine = next(unit for unit in result['resources'] if unit['id'] == 'engine-0')
    assert engine['nearest_eligible_burning_cells'] == 4
    assert any(item['code'] == 'initial_assignment_out_of_reach' and item['resource'] == 'engine-0' for item in result['findings'])
    assert any(item['code'] == 'land_mask_missing' and item['category'] == 'unsupported' for item in result['findings'])


def test_land_mask_detects_initial_footprint_and_later_water(fixture_environment):
    env = fixture_environment
    height, width = env.inputs['grid']['height'], env.inputs['grid']['width']
    mask = [[1 for column in range(width)] for row in range(height)]
    mask[16][16] = 0
    record = copy.deepcopy(env._audit[0])
    second = copy.deepcopy(record)
    second.update(minute=1, new_footprint=[16 * width + 16])
    result = audit_case(env._scenario, env.inputs, env.network, [record, second], BOUNDS, land_mask=mask)
    codes = {item['code'] for item in result['findings']}
    assert 'initial_footprint_on_water' in codes
    assert 'fire_on_water' in codes


def test_unresolved_land_is_not_declared_water(fixture_environment):
    env = fixture_environment
    height, width = env.inputs['grid']['height'], env.inputs['grid']['width']
    mask = [[1 for column in range(width)] for row in range(height)]
    mask[16][16] = None
    result = audit_case(env._scenario, env.inputs, env.network, env._audit, BOUNDS, land_mask=mask)
    assert any(item['code'] == 'initial_footprint_land_unknown' and item['category'] == 'unsupported' for item in result['findings'])
    assert not any(item['code'] == 'initial_footprint_on_water' for item in result['findings'])


def test_audit_rejects_early_work_refill_and_causal_delivery(fixture_environment):
    env = fixture_environment
    first = copy.deepcopy(env._audit[0])
    first['resources']['engine-1']['state'] = 'travel'
    second = copy.deepcopy(first)
    second['minute'] = 1
    second['new_footprint'] = []
    second['resources']['engine-1']['water_litres'] += 100
    second['applied_work'] = [dict(resource_id='engine-1', position=second['resources']['engine-1']['position'],
                                  location=[16, 16], state='working')]
    reports = [dict(observed_at=2, reported_at=2, received_at=1, kind='test')]
    result = audit_case(env._scenario, env.inputs, env.network, [first, second], BOUNDS, reports)
    codes = {item['code'] for item in result['findings']}
    assert {'work_before_arrival', 'instant_water_increase', 'communication_order', 'work_out_of_reach'} <= codes


def test_refill_rest_and_setup_have_profile_minima(fixture_environment):
    env = fixture_environment
    first = copy.deepcopy(env._audit[0])
    records = [first]
    for minute in range(1, 5):
        record = copy.deepcopy(records[-1])
        record.update(minute=minute, new_footprint=[], applied_work=[])
        records.append(record)
    records[1]['resources']['engine-1']['state'] = 'refilling'
    records[2]['resources']['engine-1']['state'] = 'available'
    records[3]['resources']['engine-1']['state'] = 'on_scene'
    records[4]['resources']['engine-1']['state'] = 'working'
    result = audit_case(env._scenario, env.inputs, env.network, records, BOUNDS)
    assert sum(item['code'] == 'duration_too_short' for item in result['findings']) == 2


def test_route_checks_detect_impossible_road_time(fixture_environment):
    env = fixture_environment
    edge = next(iter(env.network.edges.values()))
    approach = dict(journey=dict(legs=[dict(mode='road', edge_id=edge['id'], distance_m=edge['length_m'],
        duration_minutes=0.1, coordinates=edge['coordinates'])], duration_minutes=1,
        origin=edge['coordinates'][0], destination=edge['coordinates'][-1]))
    result = route_checks({'route': approach}, env.network)
    assert any(item['code'] == 'route_faster_than_road' for item in result['findings'])


def test_work_before_due_is_detected_even_when_unit_returns_immediately(fixture_environment):
    env = fixture_environment
    first = copy.deepcopy(env._audit[0])
    first['resources']['engine-1'].update(state='on_scene', due_at=5)
    second = copy.deepcopy(first)
    second.update(minute=1, new_footprint=[], applied_work=[dict(resource_id='engine-1',
        position=first['resources']['engine-1']['position'], location=[16, 16], state='working')])
    second['resources']['engine-1']['state'] = 'returning'
    result = audit_case(env._scenario, env.inputs, env.network, [first, second], BOUNDS)
    assert any(item['code'] == 'work_before_setup_due' for item in result['findings'])


def test_return_after_duty_is_flagged_without_suggesting_stopping_recovery(fixture_environment):
    env = fixture_environment
    scenario = copy.deepcopy(env._scenario)
    next(unit for unit in scenario['resources'] if unit['id'] == 'engine-1')['duty_end_at'] = 1
    records = [copy.deepcopy(env._audit[0])]
    for minute in (1, 2):
        record = copy.deepcopy(records[-1])
        record.update(minute=minute, new_footprint=[], applied_work=[])
        record['resources']['engine-1']['state'] = 'returning'
        records.append(record)
    result = audit_case(scenario, env.inputs, env.network, records, BOUNDS)
    assert any(item['code'] == 'recovery_after_shift_end' and item['minute'] == 2 for item in result['findings'])


def test_egress_exception_requires_report_and_uninterrupted_assistance(fixture_environment):
    env = fixture_environment
    scenario = copy.deepcopy(env._scenario)
    scenario['profile']['require_return_within_shift'] = True
    next(unit for unit in scenario['resources'] if unit['id'] == 'engine-1')['duty_end_at'] = 2
    records = [copy.deepcopy(env._audit[0])]
    for minute in (1, 2, 3):
        record = copy.deepcopy(records[-1])
        record.update(minute=minute, new_footprint=[], applied_work=[])
        record['resources']['engine-1']['state'] = 'needs_assistance' if minute < 3 else 'returning'
        records.append(record)
    report = dict(kind='egress_unavailable', observed_at=1, reported_at=1, received_at=2,
                  payload={'resource_id': 'engine-1'})
    result = audit_case(scenario, env.inputs, env.network, records, BOUNDS, reports=[report])
    assert any(item['code'] == 'recovery_after_shift_end_egress_unavailable' and item['category'] == 'unsupported' for item in result['findings'])
    records[2]['resources']['engine-1']['state'] = 'available'
    result = audit_case(scenario, env.inputs, env.network, records, BOUNDS, reports=[report])
    assert any(item['code'] == 'recovery_after_shift_end' and item['category'] == 'contradiction' for item in result['findings'])


def test_report_keeps_static_evidence_when_budget_censors(tmp_path):
    output = tmp_path / 'fresh'
    report = run(output, ['incident-day-dev-001'], 1e-12)
    assert len(report['static_cases']) == 6
    assert report['dynamic_cases'][0]['status'] == 'censored'
    assert report['gates']['exercise_consistent'] == 'failed'
    assert report['gates']['physically_validated'] == 'no'
    assert json.loads((output / 'report.json').read_text())['version'] == report['version']
    assert 'not physical validation' in (output / 'report.html').read_text()
    assert '<pre>' not in (output / 'report.html').read_text()
    with pytest.raises(FileExistsError):
        run(output, [], 1)
    with pytest.raises(ValueError):
        run(tmp_path / 'bad', [], float('nan'))
