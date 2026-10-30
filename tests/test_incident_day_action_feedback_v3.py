import copy
import random

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_control_v3.action_feedback_v3 import check_action_v3


@pytest.fixture
def env():
    inputs, network = fixture_inputs()
    instance = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                              expected_network_sha256=network['sha256'])
    instance.reset()
    return instance


def test_public_approach_bindings_and_read_only(env):
    route = env.route_options('crew-1')['options'][0]['id']
    evac = env.evacuation_options('population-0', 'destination-0')['options'][0]['id']
    other_resource = next(item['id'] for item in env.get_situation()['resources'] if item['id'] != 'crew-1')
    request = dict(resource_id='crew-1', sector_id='incident', approach_id=route)
    order = dict(group_id='population-0', action='evacuate', destination_id='destination-0', approach_id=evac)
    cases = [
        ({}, None, None, None),
        ({'requests': [request]}, None, None, None),
        ({'requests': [{**request, 'approach_id': evac}]}, 'invalid_resource_approach', 'requests', 'route_options'),
        ({'requests': [{**request, 'resource_id': other_resource}]}, 'invalid_resource_approach', 'requests', 'route_options'),
        ({'protection_orders': [{**order, 'approach_id': route}]}, 'invalid_evacuation_approach', 'protection_orders', 'evacuation_options'),
        ({'protection_orders': [{**order, 'group_id': 'population-1'}]}, 'invalid_evacuation_approach', 'protection_orders', 'evacuation_options'),
        ({'requests': [request, request]}, 'duplicate_resource_order', 'requests', None),
        ({'protection_orders': [order, order]}, 'duplicate_protection_order', 'protection_orders', None),
        ({'releases': ['no-such-resource']}, 'unknown_resource', 'releases', 'get_situation'),
    ]
    checkpoint = copy.deepcopy(env.checkpoint())
    py_random = random.getstate()
    for action, code, field, tool in cases:
        before_action = copy.deepcopy(action)
        result = check_action_v3(env, action)
        assert result['valid'] == (code is None)
        assert result['code'] == code
        if code:
            assert result['field'] == field
            assert result['order_index'] == (1 if code.startswith('duplicate') else 0)
            assert result.get('next_tool') == tool
            if code in ('invalid_resource_approach', 'invalid_evacuation_approach'):
                assert result['field_path'] == f'{field}[0].approach_id'
                assert "'resource_id'" not in result['error']
        assert action == before_action
        assert result == check_action_v3(env, action)
    assert env.checkpoint() == checkpoint
    assert random.getstate() == py_random


@pytest.mark.parametrize('action,code', [
    (None, 'invalid_action_format'),
    ({'unknown': []}, 'unknown_action_field'),
    ({'advance_minutes': True}, 'invalid_advance_minutes'),
    ({'releases': 'crew-1'}, 'invalid_order_list'),
    ({'requests': [None]}, 'invalid_request'),
    ({'requests': [{'resource_id': [], 'sector_id': 'incident', 'approach_id': {}}]}, 'invalid_request'),
    ({'authorizations': [[]]}, 'invalid_offer_id'),
    ({'authorizations': ['missing']}, 'unknown_offer'),
    ({'status_requests': [{'center_id': [], 'subject': 'situation'}]}, 'invalid_status_request'),
    ({'protection_orders': [{'group_id': [], 'action': 'shelter', 'destination_id': None, 'approach_id': None}]}, 'unknown_group'),
    ({'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate', 'destination_id': [], 'approach_id': {}}]}, 'unknown_destination'),
])
def test_malformed_actions(env, action, code):
    result = check_action_v3(env, action)
    assert not result['valid']
    assert result['code'] == code
    assert isinstance(result['error'], str)


def test_offer_lifecycle_and_duplicate(env):
    route = env.route_options('crew-1')['options'][0]['id']
    env.step({'requests': [dict(resource_id='crew-1', sector_id='incident', approach_id=route)]})
    for _ in range(30):
        offers = env.get_situation()['offers']
        if offers:
            break
        env.advance_time(3)
    assert offers
    offer = offers[0]
    identifier = offer['id']
    assert check_action_v3(env, {'authorizations': [identifier]})['valid']
    duplicate = check_action_v3(env, {'authorizations': [identifier, identifier]})
    assert (duplicate['code'], duplicate['order_index']) == ('duplicate_resource_order', 1)
    env.step({'authorizations': [identifier]})
    assert check_action_v3(env, {'authorizations': [identifier]})['code'] == 'offer_not_live'
    # Publicly delivered offer expiry is also classified without advancing time.
    env._offers[identifier]['status'] = 'offered'
    env._offers[identifier]['expires_at'] = env.minute
    assert check_action_v3(env, {'authorizations': [identifier]})['code'] == 'offer_not_live'


def test_unobserved_state_does_not_change_feedback(env):
    action = {'authorizations': ['unknown']}
    before = check_action_v3(env, action)
    env._audit.append({'secret_marker': 'private-audit-content'})
    env._commands.append({'secret_marker': 'future-command-content'})
    after = check_action_v3(env, action)
    assert after == before
    assert 'secret_marker' not in repr(after)
    assert 'private-audit-content' not in repr(after)
