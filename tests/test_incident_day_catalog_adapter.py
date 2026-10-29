import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from eval.incident_day_server import create_app
from wildfire_core.incident_day import (ALL_CASE_IDS, CASE_IDS, COVERAGE_CASE_IDS,
    REAL_MAP_CASE_IDS, REAL_MAP_V2_CASE_IDS, fixture_inputs)
from wildfire_openenv.incident_day import IncidentDayEnvironment
from wildfire_openenv.incident_day_client import IncidentDayClient
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN


def environment(size=32, *, archived=False):
    inputs, network = fixture_inputs(size)
    return IncidentDayEnvironment(inputs, network, expected_inputs_sha256=inputs['sha256'],
        expected_network_sha256=network['sha256'], allow_archived_scenarios=archived)


def test_fixture_default_discovery_and_archive_rejection():
    env = environment()
    assert [task['id'] for task in env.list_tasks('development')] == list(COVERAGE_CASE_IDS)
    assert env.reset().data['task_id'] == COVERAGE_CASE_IDS[0]
    with pytest.raises(ValueError):
        env.reset(task_id=CASE_IDS[0])
    with pytest.raises(ValueError):
        env.reset(task_id=REAL_MAP_V2_CASE_IDS[0])
    env = environment(archived=True)
    assert env.reset(task_id=CASE_IDS[0]).data['task_id'] == CASE_IDS[0]
    assert [t['id'] for t in env.list_tasks('development')] == [
        i for i in ALL_CASE_IDS if i not in REAL_MAP_CASE_IDS + REAL_MAP_V2_CASE_IDS]
    with pytest.raises(ValueError):
        env.reset(task_id=REAL_MAP_CASE_IDS[0])


def test_unknown_pins_have_no_implicit_default_even_in_archive_mode():
    env = environment(24, archived=True)
    assert env.list_tasks('development')
    with pytest.raises(ValueError):
        env.reset()
    assert env.reset(task_id=CASE_IDS[0]).data['task_id'] == CASE_IDS[0]
    assert environment(24).list_tasks('development') == []


def test_real_map_context_discovery_and_default():
    env = IncidentDayEnvironment.__new__(IncidentDayEnvironment)
    env.core = SimpleNamespace(inputs={'sha256': PACK_PIN}, network=SimpleNamespace(sha256=NETWORK_PIN))
    env.allow_archived_scenarios = False
    assert [t['id'] for t in env.list_tasks('development')] == list(REAL_MAP_V2_CASE_IDS)
    assert env._select_case(None) == REAL_MAP_V2_CASE_IDS[0]
    with pytest.raises(ValueError):
        env._select_case(REAL_MAP_CASE_IDS[0])
    env.allow_archived_scenarios = True
    assert env._select_case(REAL_MAP_CASE_IDS[0]) == REAL_MAP_CASE_IDS[0]
    assert len(env.list_tasks('development')) == len(ALL_CASE_IDS)


def test_http_discovery_default_and_private_fields():
    env = environment()
    with TestClient(create_app(env), base_url='http://127.0.0.1:8005',
                    client=('127.0.0.1', 12345)) as client:
        tasks = client.get('/tasks').json()
        assert [t['id'] for t in tasks['tasks']] == list(COVERAGE_CASE_IDS)
        assert tasks['default_scenario_id'] == COVERAGE_CASE_IDS[0]
        assert client.post('/reset', json={}).json()['data']['task_id'] == tasks['default_scenario_id']
        assert client.post('/reset', json={'scenario_id': CASE_IDS[0]}).status_code == 400
        public = json.dumps([tasks, client.get('/state').json()])
        for private in ('sha256', '_audit', 'graded_score', 'curation'):
            assert private not in public
        assert '/tasks' in client.get('/').text


def test_client_default_does_not_send_legacy_scenario():
    client = IncidentDayClient()
    with patch.object(client, '_request') as request:
        client.reset()
        request.assert_called_once_with('/reset', {'scenario_id': None, 'seed': None})
