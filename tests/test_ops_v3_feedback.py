"""Feedback changes neither public read effects nor legacy commit timing."""
import copy

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

from wildfire_ops_v3.feedback import FeedbackMixin

class ParentValidator:
    def _validate(self, action):
        self.delegated = action
        if action == {'parent_failure': True}:
            raise ValueError('parent validation still applies')
        return 'delegated'


class PublicOnly(FeedbackMixin, ParentValidator):
    def __init__(self):
        self._known_resources = {'crew-a': {}, 'crew-b': {}}
        self._known_groups = {'group-a': {}, 'group-b': {}}
        self._known_destinations = {'dest-a': {}, 'dest-b': {}}
        self._approaches = {
            'resource-route': {'resource_id': 'crew-a', 'sector_id': 'incident'},
            'evac-route': {'group_id': 'group-a', 'destination_id': 'dest-a'},
        }
    def __getattr__(self, name):
        if name in ('_scenario', 'ops', 'fire', '_closed_centers', '_restrictions'):
            raise AssertionError('Feedback read hidden state: ' + name)
        raise AttributeError(name)
    def route_options(self, *args, **kwargs):
        raise AssertionError('Feedback must not execute a lookup')
    def evacuation_options(self, *args, **kwargs):
        raise AssertionError('Feedback must not execute a lookup')
    def get_situation(self):
        raise AssertionError('Feedback must not execute a lookup')


def request(**changes):
    item = {'resource_id': 'crew-a', 'sector_id': 'incident', 'approach_id': 'resource-route'}
    item.update(changes)
    return {'requests': [item]}


def evacuate(**changes):
    item = {'group_id': 'group-a', 'action': 'evacuate',
            'destination_id': 'dest-a', 'approach_id': 'evac-route'}
    item.update(changes)
    return {'protection_orders': [item]}


@pytest.mark.parametrize('action,path,lookup', [
    (request(approach_id='evac-route'), 'requests[0].approach_id', "route_options(resource_id='crew-a')"),
    (request(resource_id='crew-b'), 'requests[0].resource_id', "route_options(resource_id='crew-b')"),
    (request(sector_id='wrong'), 'requests[0].sector_id', "route_options(resource_id='crew-a')"),
    (request(resource_id='unseen-resource'), 'requests[0].resource_id', 'get_situation()'),
    (request(approach_id='unseen-approach'), 'requests[0].approach_id', "route_options(resource_id='crew-a')"),
    (evacuate(approach_id='resource-route'), 'protection_orders[0].approach_id',
     "evacuation_options(group_id='group-a', destination_id='dest-a')"),
    (evacuate(group_id='group-b'), 'protection_orders[0].group_id',
     "evacuation_options(group_id='group-b', destination_id='dest-a')"),
    (evacuate(destination_id='dest-b'), 'protection_orders[0].destination_id',
     "evacuation_options(group_id='group-a', destination_id='dest-b')"),
    (evacuate(group_id='unseen-group'), 'protection_orders[0].group_id', 'get_situation()'),
    (evacuate(destination_id='unseen-destination'), 'protection_orders[0].destination_id', 'get_situation()'),
    (evacuate(approach_id='unseen-approach'), 'protection_orders[0].approach_id',
     "evacuation_options(group_id='group-a', destination_id='dest-a')"),
])
def test_actionable_feedback_uses_public_lookups_without_effects(action, path, lookup):
    env = PublicOnly()
    before, original = copy.deepcopy(env.__dict__), copy.deepcopy(action)
    with pytest.raises(ValueError) as error:
        env._validate(action)
    assert str(error.value).startswith(path + ':')
    assert 'Next lookup:' in str(error.value) and lookup in str(error.value)
    assert env.__dict__ == before and action == original
    assert 'unseen-' not in str(error.value)


def test_error_indices_are_exact_and_cross_kind_is_not_repaired():
    env = PublicOnly()
    action = {'requests': [request()['requests'][0], request(approach_id='evac-route')['requests'][0]]}
    before = copy.deepcopy(action)
    with pytest.raises(ValueError, match=r'^requests\[1\]\.approach_id:'):
        env._validate(action)
    assert action == before and 'delegated' not in env.__dict__


@pytest.mark.parametrize('action', [request(), evacuate(), {}, {'advance_minutes': 60},
    {'requests': [None]}, {'requests': [{'approach_id': []}]}, {'requests': 'malformed'},
    {'protection_orders': [{'group_id': [], 'action': 'evacuate'}]},
    {'protection_orders': [{'group_id': 'group-a', 'action': 'shelter', 'destination_id': None, 'approach_id': None}]}])
def test_other_validation_is_delegated_without_repair(action):
    env = PublicOnly()
    original = copy.deepcopy(action)
    assert env._validate(action) == 'delegated'
    assert env.delegated is action and action == original


def test_parent_errors_and_public_only_mismatch_information_are_preserved():
    env = PublicOnly()
    with pytest.raises(ValueError, match='parent validation still applies'):
        env._validate({'parent_failure': True})
    env._approaches['resource-route']['resource_id'] = 'private-hidden-resource'
    with pytest.raises(ValueError) as error:
        env._validate(request())
    assert 'private-hidden-resource' not in str(error.value)
    assert "route_options(resource_id='crew-a')" in str(error.value)


class FeedbackEnvironment(FeedbackMixin, IncidentDayEnv):
    pass


@pytest.fixture
def real_environment():
    inputs, network = fixture_inputs()
    env = FeedbackEnvironment(inputs, network, expected_inputs_sha256=inputs['sha256'],
                              expected_network_sha256=network['sha256'])
    env.reset('incident-day-dev-000')
    return env


def cross_kind_draft(env):
    routes = env.route_options('plane-0')['options']
    assert routes
    return {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate',
        'destination_id': 'destination-0', 'approach_id': routes[0]['id']}]}


def test_real_draft_read_is_zero_time_and_byte_for_byte_checkpoint_unchanged(real_environment):
    env = real_environment
    action = cross_kind_draft(env)
    before, original = env.checkpoint(), copy.deepcopy(action)
    for _ in range(3):
        result = env.check_action(action)
        assert result['valid'] is False
        assert result['error'].startswith('protection_orders[0].approach_id:')
    assert env.checkpoint() == before and action == original


def test_real_invalid_commit_costs_one_minute_without_submitting_a_repaired_order(real_environment):
    env = real_environment
    action = cross_kind_draft(env)
    original = copy.deepcopy(action)
    observation, reward, terminated, truncated, info = env.step(action)
    assert env.minute == observation['minute'] == 1 and env.decision == 1
    assert info['error'].startswith('protection_orders[0].approach_id:')
    assert not terminated and not truncated and reward == 0
    assert env._actions[-1]['valid'] is False
    assert env.ops.orders == {} and env._commands == []
    assert action == original


def test_real_known_evacuation_approach_in_resource_request_has_field_error(real_environment):
    env = real_environment
    options = env.evacuation_options('population-0', 'destination-0')['options']
    assert options
    action = {'requests': [{'resource_id': 'crew-0', 'sector_id': 'incident', 'approach_id': options[0]['id']}]}
    before = env.checkpoint()
    result = env.check_action(action)
    assert result['valid'] is False
    assert result['error'].startswith('requests[0].approach_id:')
    assert 'route_options' in result['error']
    assert env.checkpoint() == before
    assert env.step(action)[-1]['error'].startswith('requests[0].approach_id:')
    assert env.minute == 1 and env._pending_requests == set()


def test_valid_public_resource_request_keeps_legacy_execution(real_environment):
    env = real_environment
    plain = IncidentDayEnv(env.inputs, env.network.document,
        expected_inputs_sha256=env.inputs['sha256'], expected_network_sha256=env.network.sha256)
    plain.reset('incident-day-dev-000')
    option = env.route_options('plane-0')['options'][0]
    assert option == plain.route_options('plane-0')['options'][0]
    action = {'requests': [{'resource_id': 'plane-0', 'sector_id': 'incident', 'approach_id': option['id']}]}
    assert env.check_action(action) == plain.check_action(action) == {'valid': True, 'error': None}
    assert env.step(action) == plain.step(action)
    assert env.checkpoint() == plain.checkpoint()


def test_main_v3_assembly_preserves_feedback_read_and_commit_contract(monkeypatch):
    from wildfire_ops_v3 import IncidentDayEnvV3

    inputs, network = fixture_inputs()
    env = IncidentDayEnvV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                           expected_network_sha256=network['sha256'])
    env.reset('incident-day-dev-000')
    action = cross_kind_draft(env)
    before = env.checkpoint()
    result = env.check_action(action)
    assert result['valid'] is False and result['error'].startswith('protection_orders[0].approach_id:')
    assert env.checkpoint() == before
    assert env.step(action)[-1]['error'] == result['error']
    assert env.minute == 1 and env.decision == 1 and env.ops.orders == {}
