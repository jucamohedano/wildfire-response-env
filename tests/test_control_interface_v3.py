import copy
import json
from pathlib import Path

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_control_v3.control import ControlCenterV3


@pytest.fixture
def wrapped():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset('incident-day-egress-v1-000')
    return ControlCenterV3(env)


def test_core_implementation_pin_is_unchanged():
    release = json.loads((Path(__file__).parents[1] / 'data/hub_runtime_v1/release.json').read_text())
    assert implementation_digest() == release['engine_implementation_sha256']


def test_repeated_invalid_checks_do_not_mutate_time_or_block_valid_orders(wrapped):
    env = wrapped.environment
    before = copy.deepcopy(env.get_situation())
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        assert wrapped.execute('check_action', invalid)['valid'] is False
    guard = wrapped.execute('check_action', invalid)
    assert guard['code'] == 'repeated_invalid_draft'
    assert env.minute == 0 and env.get_situation() == before
    assert wrapped.execute('get_situation', {}) == before
    valid = {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter',
                                     'destination_id': None, 'approach_id': None}]}
    assert wrapped.execute('check_action', {'action': valid})['valid'] is True
    committed = wrapped.execute('commit_orders', {'action': valid})
    assert committed['info']['error'] is None and committed['observation']['minute'] == 1
    assert wrapped.execute('check_action', invalid).get('code') != 'repeated_invalid_draft'


def test_advance_alias_preserves_existing_wait_and_validates_bounds(wrapped):
    env = wrapped.environment
    assert wrapped.execute('advance', {'max_minutes': 0})['info']['error'] is not None
    # The underlying invalid committed action consumes a minute, exactly as v2 does.
    assert env.minute == 1
    result = wrapped.execute('advance', {'max_minutes': 1})
    assert result['observation']['minute'] == 2
    assert wrapped.execute('advance', {'unexpected': 1})['committed'] is False
    assert env.minute == 2


def test_fresh_route_query_resets_identical_check_guard(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'
    wrapped.execute('route_options', {'resource_id': 'plane-0'})
    assert wrapped.execute('check_action', invalid).get('code') != 'repeated_invalid_draft'
