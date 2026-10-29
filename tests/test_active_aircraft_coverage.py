import copy

import pytest

from eval.check_active_aircraft_coverage import INPUT_PIN, NETWORK_PIN, inspect_case, pinned


def trace():
    unit = dict(id='air', kind='aircraft', capacity_litres=10, fuel_capacity_minutes=10,
                base_position=[0, 0], duty_end_at=8)
    def row(minute, state, position, water, fuel, due=None, work=False):
        return dict(minute=minute, wind=[1, 0], resources={'air': dict(state=state, position=position,
                    water_litres=water, fuel_minutes=fuel, work_remaining=10, total_work_remaining=10, due_at=due)}, applied_work=(
                        [dict(resource_id='air', state='working')] if work else []))
    audit = [row(0, 'available', [0, 0], 10, 10),
             row(1, 'mobilizing', [0, 0], 10, 10),
             row(2, 'travel', [1, 0], 10, 9),
             row(3, 'on_scene', [2, 0], 10, 8, 4),
             row(4, 'returning', [2, 0], 0, 8, work=True),
             row(5, 'returning', [1, 0], 0, 7),
             row(6, 'refilling', [0, 0], 0, 6),
             row(7, 'available', [0, 0], 10, 10)]
    scenario = dict(id='case', sha256='scenario', horizon_minutes=7, start_utc='2021-09-09T12:00:00Z',
                    profile={'aircraft_max_wind': 2.8}, events=[])
    inputs = {'grid': {'width': 1, 'height': 1}}
    trajectory = dict(task_id='case', scenario_sha256='scenario', implementation_sha256='impl',
                      inputs_sha256=INPUT_PIN, network_sha256=NETWORK_PIN,
                      initial_observation={'resources': [unit]},
                      transitions=[dict(valid=True, start_minute=0, action={'authorizations': ['offer']})])
    reports = [dict(kind='resource_offer', payload={'offer': {'id': 'offer', 'resource_id': 'air'}}),
               dict(kind='dispatch_accepted', observed_at=1, payload={'resource_id': 'air'})]
    verification = dict(passed=True, exact_replay=True, independent_reward_agreement=True)
    return dict(audit=audit, trajectory=trajectory, reports=reports, verification=verification), scenario, inputs


def findings(data, scenario, inputs):
    return inspect_case(data, scenario, inputs, 'impl')[1]


def test_observed_full_cycle():
    data, scenario, inputs = trace()
    counts, errors = inspect_case(data, scenario, inputs, 'impl')
    assert not errors
    assert counts['completed_cycles'] == 1


def test_work_before_arrival_and_missing_clearance_evidence():
    data, scenario, inputs = trace()
    data['audit'][3]['applied_work'] = data['audit'][4]['applied_work']
    data['trajectory']['transitions'][0]['valid'] = False
    errors = findings(data, scenario, inputs)
    assert errors['work_before_arrival_or_setup']
    assert errors['work_without_authorization']


@pytest.mark.parametrize('change', ['night', 'wind', 'closure'])
def test_work_during_closed_window(change):
    data, scenario, inputs = trace()
    if change == 'night':
        scenario['start_utc'] = '2021-09-09T00:00:00Z'
    elif change == 'wind':
        data['audit'][4]['wind'] = [4, 0]
    else:
        scenario['events'] = [dict(kind='aircraft_unavailable', minute=4, until=6)]
    assert findings(data, scenario, inputs)['work_outside_window']


def test_stock_and_teleport_counterexamples():
    data, scenario, inputs = trace()
    data['audit'][2]['resources']['air']['position'] = [0, 0]
    data['audit'][4]['resources']['air']['water_litres'] = 11
    errors = findings(data, scenario, inputs)
    assert errors['invalid_stock']
    assert errors['arrival_without_flight']
    assert errors['work_without_water_consumption']


def test_wrong_scenario_and_unverified_trace_cannot_pass():
    data, scenario, inputs = trace()
    data['trajectory']['scenario_sha256'] = 'old'
    data['verification']['exact_replay'] = False
    errors = findings(data, scenario, inputs)
    assert errors['stale_or_wrong_execution'] and errors['unverified']


def test_no_drop_is_not_a_contradiction():
    data, scenario, inputs = trace()
    data = copy.deepcopy(data)
    for row in data['audit'][1:]:
        row['resources']['air'] = copy.deepcopy(data['audit'][0]['resources']['air'])
        row['applied_work'] = []
    counts, errors = inspect_case(data, scenario, inputs, 'impl')
    assert not errors
    assert counts['completed_cycles'] == 0


def test_pins_are_content_verified(tmp_path):
    path = tmp_path / 'input.json'
    path.write_text('{"sha256": "' + INPUT_PIN + '", "tampered": true}')
    with pytest.raises(ValueError):
        pinned(path, INPUT_PIN)


def test_night_takeoff_is_detected_even_if_drop_is_in_daylight():
    data, scenario, inputs = trace()
    # Modeled sunrise is 05:58 UTC: takeoff is 05:57, drop is 05:59.
    scenario['start_utc'] = '2021-09-09T05:55:00Z'
    errors = findings(data, scenario, inputs)
    assert errors['takeoff_outside_window'] == 1
    assert not errors['work_outside_window']
