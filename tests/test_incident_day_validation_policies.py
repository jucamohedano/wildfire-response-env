import json
from unittest.mock import patch

from envs.wildfire_response.incident_day import environment as environment_module

import pytest

from eval.incident_day_policies import POLICY_NAMES, make_policy
from wildfire_core.incident_day import IncidentDayEnv, IncidentDayTools, fixture_inputs


def create():
    inputs, network = fixture_inputs()
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'])


class PublicFacade:
    """Expose only the read/query surface handed to a policy."""
    __slots__ = ('get_situation', 'get_reports', 'route_options', 'evacuation_options', 'check_action')

    def __init__(self, tools):
        for name in self.__slots__:
            def call(*args, _name=name, **kwargs):
                names = {'route_options': ('resource_id', 'sector_id'),
                         'evacuation_options': ('group_id', 'destination_id'),
                         'check_action': ('action',), 'get_reports': ('ids',)}.get(_name, ())
                result = tools.execute(_name, dict(zip(names, args)) | kwargs)
                assert not isinstance(result, dict) or result.get('committed') is not False, result
                return result
            setattr(self, name, call)


def commit(tools, action):
    if 'advance_minutes' in action:
        assert 1 <= action['advance_minutes'] <= 60
        return tools.execute('advance_time', {'max_minutes': action['advance_minutes']})
    return tools.execute('commit_orders', {'action': action})


@pytest.mark.parametrize('name', POLICY_NAMES)
def test_short_public_rollout_and_checkpoint_roundtrip(name):
    env = create()
    observation, _ = env.reset()
    tools = IncidentDayTools(env)
    facade = PublicFacade(tools)
    policy = make_policy(name)
    assert not hasattr(facade, 'step') and not hasattr(facade, '_scenario')
    for _ in range(8):
        state = json.loads(json.dumps(policy.checkpoint(), allow_nan=False))
        resumed = make_policy(name)
        resumed.restore(state)
        action = policy.action(facade, observation)
        assert resumed.action(facade, observation) == action
        assert resumed.checkpoint() == policy.checkpoint()
        assert facade.check_action(action)['valid']
        result = commit(tools, action)
        assert 'error' not in result and not result['truncated']
        observation = result['observation']
        if result['terminated']:
            break
    if name == 'passive':
        assert all(call['arguments']['max_minutes'] == 60 for call in tools.calls
                   if call['name'] == 'advance_time')
    if name == 'conservative':
        orders = [call['arguments']['action'] for call in tools.calls if call['name'] == 'commit_orders']
        assert any('releases' in action for action in orders)
        shelters = [order for action in orders for order in action.get('protection_orders', [])]
        assert len(shelters) == 1 and shelters[0]['action'] == 'shelter'
        assert any('status_requests' in action for action in orders)
        assert any(report['kind'] == 'release_accepted' for report in observation['reports'])
        assert any(order['action'] == 'shelter' and order['state'] in ('acknowledged', 'completed')
                   for order in observation['protection_orders'])
        assert any(request['subject'] == 'situation' and request['status'] == 'answered'
                   for request in observation['status_requests'])
    if name == 'stress':
        assert any(call['name'] == 'get_reports' for call in tools.calls)
        assert any(call['name'] == 'check_action' for call in tools.calls)
        assert any(call['name'] == 'route_options' for call in tools.calls)


def test_dispatcher_checkpoint_keeps_mixed_asked_keys():
    policy = make_policy('dispatcher')
    policy.dispatcher.asked.update({('protection', 'g', 12), ('closure', 'g', 'd', 3), ('center', 0)})
    encoded = json.dumps(policy.checkpoint(), sort_keys=True)
    restored = make_policy('dispatcher')
    restored.restore(json.loads(encoded))
    assert restored.dispatcher.asked == policy.dispatcher.asked
    assert json.dumps(restored.checkpoint(), sort_keys=True) == encoded
    with pytest.raises(ValueError):
        make_policy('stress').restore(json.loads(encoded))
    with pytest.raises(ValueError):
        make_policy('unknown')


def test_stress_scheduled_release_is_bounded_and_valid():
    env = create()
    observation, _ = env.reset()
    tools = IncidentDayTools(env)
    facade = PublicFacade(tools)
    policy = make_policy('stress')
    result = tools.execute('advance_time', {'max_minutes': 5})
    observation = result['observation']
    # Actionable deliveries may interrupt a wait before the scheduled minute.
    while observation['minute'] < 5:
        result = tools.execute('advance_time', {'max_minutes': 5 - observation['minute']})
        observation = result['observation']
    action = policy.action(facade, observation)
    assert action.get('releases') == ['engine-0']
    assert facade.check_action(action)['valid']
    assert 'error' not in commit(tools, action)
    assert json.loads(json.dumps(policy.checkpoint()))['released'] == ['engine-0']


def test_default_at_home_group_receives_shelter_through_public_facade():
    # The normal fixture explicitly uses unwarned; omit that field to exercise
    # the actual Operations default through an otherwise unchanged scenario.
    build = environment_module.build_scenario

    def scenario_with_default_group_status(*args, **kwargs):
        scenario = build(*args, **kwargs)
        for group in scenario['groups']:
            group.pop('status', None)
        return scenario

    env = create()
    with patch.object(environment_module, 'build_scenario', scenario_with_default_group_status):
        observation, _ = env.reset()
    assert all(group['status'] == 'at_home' for group in observation['districts'])
    tools = IncidentDayTools(env)
    facade = PublicFacade(tools)
    policy = make_policy('conservative')
    actions = []
    for _ in range(8):
        action = policy.action(facade, observation)
        actions.append(action)
        assert facade.check_action(action)['valid']
        result = commit(tools, action)
        assert not result['truncated'] and 'error' not in result
        observation = result['observation']
    shelters = [order for action in actions for order in action.get('protection_orders', [])]
    assert len(shelters) == 1 and shelters[0]['action'] == 'shelter'
    assert any(report['kind'] == 'release_accepted' for report in observation['reports'])
    assert any(order['group_id'] == shelters[0]['group_id'] and order['action'] == 'shelter'
               and order['state'] in ('acknowledged', 'completed')
               for order in observation['protection_orders'])
    assert any(request['subject'] == 'situation' and request['status'] == 'answered'
               for request in observation['status_requests'])


def test_stress_reads_report_ledger_in_pages_and_restores_cursor():
    env = create()
    observation, _ = env.reset()
    tools = IncidentDayTools(env)
    facade = PublicFacade(tools)
    while observation['minute'] < 35:
        result = tools.execute('advance_time', {'max_minutes': 35 - observation['minute']})
        assert not result['truncated']
        observation = result['observation']
    records = facade.get_reports(after_cursor=0, limit=20)
    assert isinstance(records, list) and len(records) == 20
    assert observation['report_count'] > 20
    policy = make_policy('stress')
    policy.action(facade, observation)
    assert policy.checkpoint()['report_cursor'] == len(records)
    saved = json.loads(json.dumps(policy.checkpoint()))
    restored = make_policy('stress')
    restored.restore(saved)
    second_page = facade.get_reports(after_cursor=len(records), limit=20)
    restored.action(facade, observation)
    assert restored.checkpoint()['report_cursor'] == len(records) + len(second_page)
    assert set(record['id'] for record in records).isdisjoint(record['id'] for record in second_page)


def test_restore_rejects_malformed_state_without_mutating_policy():
    policy = make_policy('dispatcher')
    original = policy.checkpoint()
    for field, bad in [('asked', [['protection', 'g', []]]), ('released', 'unit'),
                       ('sheltered', [None]), ('status_sent', 1), ('report_cursor', True)]:
        state = json.loads(json.dumps(original))
        state[field] = bad
        with pytest.raises(ValueError):
            policy.restore(state)
        assert policy.checkpoint() == original
