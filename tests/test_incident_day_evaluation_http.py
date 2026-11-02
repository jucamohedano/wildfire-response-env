"""HTTP split contracts without scenario compilation, sockets or simulation."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from eval.incident_day_server import create_app
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
from envs.wildfire_response.incident_day.scenarios import EVALUATION_CASE_IDS, REAL_MAP_DEVELOPMENT_CASE_IDS
from wildfire_core.incident_day import REAL_MAP_V2_CASE_IDS
from wildfire_openenv.incident_day import IncidentDayEnvironment
from wildfire_openenv.incident_day_client import IncidentDayClient


@pytest.fixture
def environment():
    env = IncidentDayEnvironment.__new__(IncidentDayEnvironment)
    env.core = SimpleNamespace(inputs={'sha256': PACK_PIN}, network=SimpleNamespace(sha256=NETWORK_PIN))
    env.allow_archived_scenarios = False
    env.core.reset = Mock(return_value=({'task_id': EVALUATION_CASE_IDS[0], 'minute': 0,
        'checkpoint': 'private'}, {'sha256': 'private'}))
    return env


def connection(env):
    return TestClient(create_app(env), base_url='http://127.0.0.1:8005', client=('127.0.0.1', 12345))


def test_split_discovery_and_explicit_reset_preserve_default(environment):
    with connection(environment) as http:
        development = http.get('/tasks')
        assert development.status_code == 200
        assert development.json() == http.get('/tasks?split=development').json()
        assert [t['id'] for t in development.json()['tasks']] == list(REAL_MAP_V2_CASE_IDS + REAL_MAP_DEVELOPMENT_CASE_IDS)
        assert development.json()['default_scenario_id'] == REAL_MAP_V2_CASE_IDS[0]
        response = http.get('/tasks?split=evaluation')
        assert response.status_code == 200
        assert response.json() == {'tasks': [dict(id=identifier, split='evaluation') for identifier in EVALUATION_CASE_IDS],
                                   'default_scenario_id': None}
        task_id = response.json()['tasks'][0]['id']
        reset = http.post('/reset', json={'scenario_id': task_id})
        assert reset.status_code == 200
        assert reset.json()['data'] == {'task_id': task_id, 'minute': 0}
        assert reset.json()['metadata'] == {}
        environment.core.reset.assert_called_once_with(task_id, None)
        assert http.get('/tasks').json() == development.json()


@pytest.mark.parametrize('query', ['split=train', 'split=', 'split', 'unknown=evaluation',
    'split=evaluation&split=development', 'split=evaluation&extra=1', 'split=%FF', 'split=' + 'x' * 130])
def test_unsupported_and_ambiguous_queries_are_errors(environment, query):
    with connection(environment) as http:
        assert http.get('/tasks?' + query).status_code == 400
        assert http.get('/state?split=evaluation').status_code == 400
        assert http.post('/reset?split=evaluation', json={}).status_code == 400
    environment.core.reset.assert_not_called()


@pytest.mark.parametrize('input_pin,network_pin', [('wrong', NETWORK_PIN), (PACK_PIN, 'wrong'), ('wrong', 'wrong')])
def test_evaluation_wrong_pins_fail_discovery_and_reset(environment, input_pin, network_pin):
    environment.core.inputs['sha256'] = input_pin
    environment.core.network.sha256 = network_pin
    environment.allow_archived_scenarios = True
    with connection(environment) as http:
        response = http.get('/tasks?split=evaluation')
        assert response.status_code == 400
        assert 'unavailable' in response.json()['detail']
        assert 'wrong' not in response.text
        assert http.post('/reset', json={'scenario_id': EVALUATION_CASE_IDS[0]}).status_code == 400
    environment.core.reset.assert_not_called()


def test_missing_evaluation_catalog_is_an_error(environment):
    environment.list_tasks = Mock(return_value=[])
    with connection(environment) as http:
        assert http.get('/tasks?split=evaluation').status_code == 400
        assert http.get('/tasks').status_code == 200


def test_client_retains_default_path_and_selects_evaluation():
    client = IncidentDayClient()
    with patch.object(client, '_request', return_value={'tasks': []}) as request:
        client.list_tasks()
        request.assert_called_with('/tasks')
        client.list_tasks(split='development')
        request.assert_called_with('/tasks')
        client.list_tasks(split='evaluation')
        request.assert_called_with('/tasks?split=evaluation')
        count = request.call_count
        for invalid in ('train', '', None, 1):
            with pytest.raises(ValueError): client.list_tasks(invalid)
        assert request.call_count == count


def test_split_query_keeps_loopback_and_origin_guards(environment):
    with connection(environment) as http:
        assert http.get('/tasks?split=evaluation', headers={'host': 'evil.test'}).status_code == 403
        assert http.get('/tasks?split=evaluation', headers={'origin': 'http://evil.test'}).status_code == 403
