import copy

from eval.review_scenario_realism import screen


def sample():
    return dict(horizon_minutes=60, centers=['base'], events=[dict(kind='destination_closed', minute=20, destination_id='d')],
        resources=[dict(id='r', kind='crew', home_base='base', available_at=0, water_litres=0,
                        capacity_litres=0, work_remaining=20, duty_end_at=60, total_work_remaining=20)],
        profile=dict(ground_setup_minutes=5, aircraft_setup_minutes=2, ground_refill_minutes=4,
                     air_refill_minutes=6, rest_minutes=10, flight_speed_kph=240),
        groups=[dict(id='g', count=4, destination_id='d', status='arrived')],
        destinations=[dict(id='d', capacity=5)])


def test_structural_screen_flags_impossible_input_without_rejecting_difficult_closure():
    scenario = sample()
    assert screen(scenario) == []
    invalid = copy.deepcopy(scenario)
    invalid['events'][0]['minute'] = 60
    invalid['destinations'][0]['capacity'] = 3
    invalid['resources'][0]['work_remaining'] = -1
    invalid['profile']['ground_setup_minutes'] = 0
    assert set(screen(invalid)) == {'event_outside_horizon', 'initial_destination_over_capacity',
                                    'invalid_resource_lifecycle', 'invalid_duration_or_speed'}


def test_partition_covers_catalog_once():
    from eval.review_scenario_realism import case_groups
    from envs.wildfire_response.incident_day.scenarios import ALL_CASE_IDS
    ids = [case for unused, group in case_groups(include_archived=True) for case in group]
    assert len(ids) == len(set(ids)) == len(ALL_CASE_IDS) == 32
    assert set(ids) == set(ALL_CASE_IDS)


def test_event_may_continue_beyond_horizon_but_must_end_after_start():
    scenario = sample()
    scenario['events'][0]['until'] = 90
    assert 'invalid_event_duration' not in screen(scenario)
    scenario['events'][0]['until'] = 20
    assert 'invalid_event_duration' in screen(scenario)


def test_finite_work_budget_and_ordered_duty_window_can_cross_day_end():
    scenario = sample()
    scenario['resources'][0]['duty_end_at'] = 90
    assert screen(scenario) == []
    for field, value in (('duty_end_at', -1), ('total_work_remaining', float('inf')),
                         ('total_work_remaining', 1)):
        invalid = copy.deepcopy(scenario)
        invalid['resources'][0][field] = value
        assert 'invalid_resource_lifecycle' in screen(invalid)


def test_active_partition_excludes_superseded_and_ineffective_cases():
    from eval.review_scenario_realism import case_groups
    from wildfire_core.catalog_lifecycle import ACTIVE_FIXTURE_CASE_IDS, ACTIVE_REAL_MAP_CASE_IDS
    ids = [case for unused, group in case_groups() for case in group]
    assert len(ids) == 12
    assert set(ids) == set(ACTIVE_FIXTURE_CASE_IDS + ACTIVE_REAL_MAP_CASE_IDS)
