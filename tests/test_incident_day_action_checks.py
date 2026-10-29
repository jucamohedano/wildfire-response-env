import copy
import json

import pytest

from wildfire_core.incident_day import IncidentDayEnv, IncidentDayTools, fixture_inputs
from eval.incident_day_action_checks import audit_runtime


@pytest.fixture
def exercised():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    tools = IncidentDayTools(env)
    calls = []

    def call(name, arguments):
        minute, decision = env.minute, env.decision
        result = tools.execute(name, arguments)
        # Match the parent's private metadata-only journal.
        metadata = {key: result[key] for key in ('info', 'reward', 'terminated', 'truncated', 'valid', 'error', 'committed') if key in result} if isinstance(result, dict) else {}
        calls.append(dict(name=name, arguments=copy.deepcopy(arguments), before_minute=minute,
                          after_minute=env.minute, before_decision=decision, after_decision=env.decision,
                          result=metadata))
        return result

    call('get_situation', {})
    call('get_reports', {})
    route = call('route_options', {'resource_id': 'crew-1'})['options'][0]
    evacuation = call('evacuation_options', {'group_id': 'population-0', 'destination_id': 'destination-0'})['options'][0]
    call('check_action', {'action': {}})
    call('commit_orders', {'action': {
        'requests': [dict(resource_id='crew-1', sector_id='incident', approach_id=route['id'])],
        'protection_orders': [dict(group_id='population-0', action='evacuate', destination_id='destination-0', approach_id=evacuation['id']),
                              dict(group_id='population-1', action='shelter', destination_id=None, approach_id=None)]}})
    call('request_status', {'center_id': 'center-0', 'subject': 'situation'})
    for _ in range(12):
        call('advance_time', {'max_minutes': 3})
        offers = env.get_situation()['offers']
        if offers and offers[0]['status'] == 'offered':
            call('commit_orders', {'action': {'authorizations': [offers[0]['id']]}})
        if offers and offers[0]['status'] == 'authorized':
            break
    call('commit_orders', {'action': {'releases': ['crew-1']}})
    for _ in range(4):
        call('advance_time', {'max_minutes': 3})
    return env, calls


def codes(env, calls):
    return {item['code'] for item in audit_runtime(env, calls)['findings']}


def test_valid_short_run_all_tools_and_actions(exercised):
    env, calls = exercised
    result = audit_runtime(env, calls)
    assert not [item for item in result['findings'] if item['category'] == 'contradiction']
    assert all(result['coverage'][name]['status'] == 'complete' for name in
               ('get_situation', 'get_reports', 'route_options', 'evacuation_options', 'check_action',
                'commit_orders', 'request_status', 'advance_time', 'requests', 'authorizations',
                'releases', 'shelter', 'evacuate', 'status_requests', 'waits'))
    assert result['coverage']['actual_routes_and_clearance']['status'] == 'unsupported'
    json.dumps(result, allow_nan=False)
    assert any(item.get('censored') for item in result['durations'])


@pytest.mark.parametrize('kind,expected', [
    ('read_time', 'read_advanced_time'), ('read_commit', 'read_committed'),
    ('commit_time', 'commit_duration'), ('wait_time', 'wait_duration'),
    ('early_wake', 'wait_unexplained_early_wake'), ('transition', 'transition_commit_duration'),
    ('stock', 'resource_capacity_exceeded'), ('nonfinite', 'resource_invalid_stock'),
    ('facility', 'facility_stock_conservation'), ('population', 'population_count_mismatch'),
    ('exposure', 'population_exposure_invalid'), ('destination', 'destination_capacity'),
    ('report', 'communication_order'), ('order', 'protection_order_sequence'),
    ('order_delay', 'protection_phase_too_short'), ('order_destination', 'invalid_order_destination'),
])
def test_fault_injection(exercised, kind, expected):
    env, calls = exercised
    calls = copy.deepcopy(calls)
    if kind == 'read_time':
        calls[0]['after_minute'] += 1
    elif kind == 'read_commit':
        calls[0]['after_decision'] += 1
    elif kind == 'commit_time':
        next(c for c in calls if c['name'] == 'commit_orders')['after_minute'] += 1
    elif kind in ('wait_time', 'early_wake'):
        call = next(c for c in calls if c['name'] == 'advance_time')
        call['after_minute'] = call['before_minute'] + (4 if kind == 'wait_time' else 1)
        call['result']['info']['wake_reason'] = 'watch_timeout'
    elif kind == 'transition':
        env._actions[0]['minute'] += 1
    elif kind == 'stock':
        env._audit[-1]['resources']['engine-1']['water_litres'] = 999999
    elif kind == 'nonfinite':
        env._audit[-1]['resources']['engine-1']['water_litres'] = float('nan')
    elif kind == 'facility':
        env.ops.profile['facilities']['center-0']['water_litres'] -= 1
    elif kind == 'population':
        env._audit[-1]['population']['population-0']['count'] += 1
    elif kind == 'exposure':
        env._audit[-1]['population']['population-0']['exposed'] = -1
    elif kind == 'destination':
        env.ops.destinations['destination-0']['capacity'] = 0
    elif kind == 'report':
        env.bus.reports[0]['observed_at'] = env.bus.reports[0]['received_at'] + 1
    elif kind == 'order':
        order = next(iter(env.ops.orders.values()))
        order['delivered_at'] = order['issued_at'] - 1
    elif kind == 'order_delay':
        order = next(iter(env.ops.orders.values()))
        order['delivered_at'] = order['issued_at']
    elif kind == 'order_destination':
        next(order for order in env.ops.orders.values() if order['action'] == 'evacuate')['destination_id'] = 'missing'
    assert expected in codes(env, calls)


def test_failed_adapter_call_and_invalid_core_commit_have_different_costs(exercised):
    env, calls = exercised
    adapter = IncidentDayTools(env)
    for name, args in [('get_situation', {'unexpected': True}), ('commit_orders', {'action': {'bad': []}})]:
        before, decision = env.minute, env.decision
        result = adapter.execute(name, args)
        calls.append(dict(name=name, arguments=args, result=result, before_minute=before,
                          after_minute=env.minute, before_decision=decision, after_decision=env.decision))
    assert calls[-2]['after_minute'] == calls[-2]['before_minute']
    assert calls[-1]['after_minute'] == calls[-1]['before_minute'] + 1
    assert not {'rejected_tool_advanced_time', 'commit_duration'} & codes(env, calls)


def test_missing_journal_and_inherited_phase_are_explicit(exercised):
    env, _ = exercised
    result = audit_runtime(env)
    assert all(result['coverage'][name]['status'] == 'not_exercised' for name in ('get_situation', 'advance_time'))
    assert result['coverage']['requests']['status'] == 'complete'
    assert any(d.get('inherited') for d in result['durations'])
    assert not any(f['code'] == 'read_committed' for f in result['findings'])


def test_evidence_qualified_command_delay(exercised):
    env, calls = exercised
    report = next(report for report in env.bus.reports if report['kind'] == 'resource_offer')
    report['observed_at'] = env._actions[0]['start_minute'] + 1
    assert 'offer_before_request_delay' in codes(env, calls)


def test_profile_water_consumption_corruption(exercised):
    env, calls = exercised
    # Work in the inherited assignment may be absent on this fixture. Inject an
    # applied-work record with unchanged stock to test the conservation rule.
    record = env._audit[1]
    previous = env._audit[0]['resources']['engine-1']
    record['applied_work'].append(dict(resource_id='engine-1', state='working',
                                       position=previous['position'], location=[16, 16]))
    record['resources']['engine-1']['water_litres'] = previous['water_litres']
    assert 'work_water_conservation' in codes(env, calls)


def test_short_wait_requires_exercise_wake_reason(exercised):
    env, calls = exercised
    call = next(c for c in calls if c['name'] == 'advance_time')
    call['after_minute'] = call['before_minute'] + 1
    call['result']['info']['wake_reason'] = 'boundary_censored'
    call['result']['truncated'] = True
    assert 'wait_unexplained_early_wake' not in codes(env, calls)
    call['result']['info']['wake_reason'] = 'hourly_checkpoint'
    assert 'wait_unexplained_early_wake' in codes(env, calls)
    call['result']['info']['wake_reason'] = 'invented'
    assert 'wait_unexplained_early_wake' in codes(env, calls)


def test_only_missing_decision_evidence_is_unsupported(exercised):
    env, calls = exercised
    del calls[0]['before_decision']
    result = audit_runtime(env, calls)
    assert result['coverage']['get_situation']['status'] == 'unsupported'
    assert result['coverage']['get_reports']['status'] == 'complete'


def test_existing_plausibility_audit_receives_route_queries(exercised, monkeypatch):
    import eval.incident_day_action_checks as checks

    env, calls = exercised
    original = checks.audit_case
    received = []

    def capture(*args, **kwargs):
        received.append(kwargs['queries'])
        return original(*args, **kwargs)

    monkeypatch.setattr(checks, 'audit_case', capture)
    audit_runtime(env, calls)
    assert received[-1] == env._query_log and env._query_log
    monkeypatch.delattr(env, '_query_log')
    audit_runtime(env, calls)
    assert received[-1] == ()
