import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    from wildfire_core.incident_day import CASE_IDS, fixture_inputs
    from wildfire_openenv.incident_day import IncidentDayAction, IncidentDayEnvironment, ReportQuery
    from wildfire_openenv.incident_day_client import IncidentDayClient
    from eval.incident_day_server import create_app
    AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ('fastapi', 'pydantic', 'httpx', 'wildfire_core', 'wildfire_openenv', 'uvicorn'):
        raise
    AVAILABLE = False


def make_environment():
    inputs, network = fixture_inputs(24)
    return IncidentDayEnvironment(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                  expected_network_sha256=network['sha256'])


@unittest.skipUnless(AVAILABLE, 'Optional FastAPI/Pydantic dependencies and core required')
class IncidentDayAdapterTests(unittest.TestCase):
    def setUp(self):
        self.environment = make_environment()

    def test_typed_actions_and_queries_reject_malformed_values(self):
        invalid_actions = [
            {'advance_minutes': 0}, {'advance_minutes': 61}, {'advance_minutes': True},
            {'advance_minutes': '2'}, {'advance_minutes': None},
            {'advance_minutes': 3, 'requests': []}, {'unknown': 1},
            {'requests': [{'resource_id': 'crew-0', 'sector_id': 'incident'}]},
            {'requests': [{'resource_id': 'crew-0', 'sector_id': 'incident', 'approach_id': [], 'extra': 1}]},
            {'status_requests': [{'center_id': 'center-0', 'subject': 'secret'}]},
            {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter'}]},
            {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate', 'destination_id': None}]},
            {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate', 'destination_id': 'destination-0'}]},
            {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter', 'destination_id': None, 'approach_id': 'route-1'}]},
            {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate', 'destination_id': 'destination-0', 'approach_id': 7}]},
            {'releases': ['crew-0'] * 7}, {'metadata': {'private': True}},
        ]
        for action in invalid_actions:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                IncidentDayAction.model_validate(action)
        for query in ({'limit': 21}, {'limit': True}, {'after_cursor': -1},
                      {'ids': ['a'] * 21}, {'ids': [3]}, {'unknown': 1}):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                ReportQuery.model_validate(query)

    def test_manifest_and_client_reject_nonlocal_urls(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'envs/wildfire_response/openenv/incident_day_manifest.json').read_text())
        self.assertEqual(manifest['scenario_ids'], list(CASE_IDS))
        self.assertEqual(manifest['transport'], 'loopback-http-json')
        for url in ('https://127.0.0.1:8005', 'http://example.com', 'http://localhost.evil',
                    'http://localhost:8005/path', 'http://user@localhost'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                IncidentDayClient(url)

    def test_protection_approach_wire_contract(self):
        shelter = IncidentDayAction(protection_orders=[{'group_id': 'population-0', 'action': 'shelter',
                                                        'destination_id': None}])
        self.assertEqual(shelter.core_action()['protection_orders'][0]['approach_id'], None)
        evacuation = IncidentDayAction(protection_orders=[{'group_id': 'population-0', 'action': 'evacuate',
            'destination_id': 'destination-0', 'approach_id': 'route-1'}])
        self.assertEqual(evacuation.core_action()['protection_orders'][0]['approach_id'], 'route-1')

    def test_evacuation_options_adapter_and_http_contract(self):
        self.environment.reset()
        result = {'status': 'reachable', 'options': [{'id': 'route-1'}]}
        with patch.object(self.environment.core, 'evacuation_options', return_value=result, create=True) as query:
            direct = self.environment.evacuation_options('population-0', 'destination-0')
            direct['options'].clear()
            self.assertTrue(result['options'])
            with TestClient(create_app(self.environment), base_url='http://127.0.0.1:8005',
                            client=('127.0.0.1', 12345)) as client:
                response = client.post('/evacuation-options', json={'group_id': 'population-0',
                                                                     'destination_id': 'destination-0'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), result)
                self.assertEqual(client.post('/evacuation-options', json={'group_id': 'population-0'}).status_code, 422)
                self.assertEqual(client.post('/evacuation-options', json={'group_id': 7, 'destination_id': 'destination-0'}).status_code, 422)
                self.assertEqual(client.post('/evacuation-options', json={'group_id': 'population-0',
                    'destination_id': 'destination-0', 'extra': True}).status_code, 422)
            self.assertEqual(query.call_count, 2)
            query.assert_called_with('population-0', 'destination-0')
        self.assertEqual(self.environment.state.minute, 0)
        self.assertEqual(len(self.environment.trajectory()), 1)

    def test_evacuation_options_core_integration(self):
        if not hasattr(self.environment.core, 'evacuation_options'):
            self.skipTest('Core evacuation_options is not available yet')
        self.environment.reset()
        expected = self.environment.core.evacuation_options('population-0', 'destination-0')
        result = self.environment.evacuation_options('population-0', 'destination-0')
        self.assertEqual(result, expected)
        self.assertEqual(result['status'], 'reachable')
        self.assertEqual(self.environment.state.minute, 0)
        with TestClient(create_app(self.environment), base_url='http://127.0.0.1:8005',
                        client=('127.0.0.1', 12345)) as client:
            response = client.post('/evacuation-options', json={'group_id': 'population-0',
                                                                 'destination_id': 'destination-0'})
            self.assertEqual(response.json(), result)
            self.assertEqual(self.environment.state.minute, 0)
            shelter = {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter',
                                               'destination_id': None}]}
            self.assertTrue(client.post('/check', json={'action': shelter}).json()['valid'])
            evacuation = {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate',
                'destination_id': 'destination-0', 'approach_id': response.json()['options'][0]['id']}]}
            self.assertTrue(client.post('/check', json={'action': evacuation}).json()['valid'])
            submitted = client.post('/step', json={'action': evacuation})
            self.assertEqual(submitted.status_code, 200)
            self.assertIsNone(submitted.json()['metadata']['error'])

    def test_state_isolation_and_route_read_does_not_advance_clock(self):
        self.assertFalse(self.environment.SUPPORTS_CONCURRENT_SESSIONS)
        with self.assertRaises(RuntimeError):
            self.environment.get_situation()
        observation = self.environment.reset(episode_id='test-session')
        self.assertEqual(observation.data['minute'], 0)
        state = self.environment.state
        state.minute = 999
        observation.data['resources'].clear()
        self.assertEqual(self.environment.state.minute, 0)
        self.assertTrue(self.environment.get_situation()['resources'])
        route = self.environment.route_options('plane-0')
        self.assertEqual(route['status'], 'reachable')
        self.assertEqual(self.environment.state.minute, 0)
        self.assertTrue(self.environment.check_action(IncidentDayAction(advance_minutes=60))['valid'])
        self.assertEqual(self.environment.state.minute, 0)
        moved = self.environment.request_status('center-0', 'resources')
        self.assertEqual(moved.data['minute'], 1)
        self.assertEqual(len(self.environment.trajectory()), 2)
        self.environment.trajectory()[0]['resources'].clear()
        self.assertTrue(self.environment.trajectory()[0]['resources'])
        self.environment.reset(task_id=CASE_IDS[0])
        self.assertEqual(self.environment.state.minute, 0)
        self.assertEqual(len(self.environment.trajectory()), 1)

    def test_native_openenv_websocket_contract(self):
        try:
            from openenv.core.env_server import create_app as create_openenv_app
            from wildfire_openenv.incident_day import IncidentDayObservation, IncidentDayState
        except ModuleNotFoundError:
            self.skipTest('OpenEnv is optional')
        app = create_openenv_app(lambda: make_environment(), IncidentDayAction, IncidentDayObservation,
                                 env_name='incident_day', max_concurrent_envs=1,
                                 state_cls=IncidentDayState, mode='simulation')
        with TestClient(app) as client, client.websocket_connect('/ws') as websocket:
            websocket.send_json({'type': 'reset', 'data': {'task_id': CASE_IDS[0]}})
            reset = websocket.receive_json()
            self.assertEqual(reset['type'], 'observation', reset)
            self.assertEqual(reset['data']['observation']['data']['minute'], 0)
            websocket.send_json({'type': 'step', 'data': {'advance_minutes': 60}})
            step = websocket.receive_json()
            self.assertEqual(step['type'], 'observation', step)
            self.assertGreater(step['data']['observation']['data']['minute'], 0)
            websocket.send_json({'type': 'state'})
            state = websocket.receive_json()
            self.assertEqual(state['data']['minute'], step['data']['observation']['data']['minute'])
            self.assertNotIn('sha256', json.dumps(state))

    def test_pending_request_and_aircraft_operability_are_public(self):
        observation = self.environment.reset()
        self.assertEqual(observation.data['pending_resource_requests'], [])
        self.assertEqual(observation.data['access_restrictions'], self.environment.core.get_situation()['access_restrictions'])
        for resource in observation.data['resources']:
            self.assertTrue({'duty_end_at', 'total_work_remaining', 'facility_id'} <= resource.keys())
        self.assertIsInstance(observation.data['aircraft_operable'], bool)
        option = self.environment.route_options('plane-0')['options'][0]
        action = IncidentDayAction(requests=[{'resource_id': 'plane-0', 'sector_id': 'incident',
                                               'approach_id': option['id']}])
        result = self.environment.step(action)
        self.assertIn('plane-0', result.data['pending_resource_requests'])
        self.assertEqual(result.data['aircraft_operable'], self.environment.core.get_situation()['aircraft_operable'])

    def test_http_validation_privacy_and_loopback_guards(self):
        with TestClient(create_app(self.environment), base_url='http://127.0.0.1:8005',
                        client=('127.0.0.1', 12345)) as client:
            self.assertEqual(client.get('/state').status_code, 409)
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.post('/reset', json={}).status_code, 200)
            self.assertEqual(client.post('/step', json={'action': {'advance_minutes': 61}}).status_code, 422)
            self.assertEqual(client.post('/check', json={'action': {'advance_minutes': '5'}}).status_code, 422)
            self.assertEqual(client.post('/routes', json={'resource_id': 'plane-0', 'extra': 1}).status_code, 422)
            self.assertEqual(client.post('/reports', json={'limit': 21}).status_code, 422)
            self.assertEqual(client.post('/status', json={'center_id': 'center-0', 'subject': 'secret'}).status_code, 422)
            self.assertEqual(client.post('/reset', content=b'{"seed":1,"seed":2}', headers={'content-type': 'application/json'}).status_code, 422)
            self.assertEqual(client.post('/step', content=b' ' * 65537).status_code, 413)
            self.assertEqual(client.post('/routes', json={'resource_id': 'plane-0'}).status_code, 200)
            self.assertEqual(client.get('/state').json()['state']['minute'], 0)
            self.assertEqual(client.post('/reset', json={}, headers={'origin': 'http://evil.test'}).status_code, 403)
            self.assertEqual(client.post('/reset', json={}, headers={'origin': 'http://localhost:8005'}).status_code, 403)
            self.assertEqual(client.get('/state', headers={'host': 'evil.test'}).status_code, 403)
            self.assertEqual(client.get('/state?limit=999').status_code, 400)
            self.assertEqual(client.get('/checkpoint').status_code, 404)
            self.assertNotIn('access-control-allow-origin', client.get('/').headers)
            public = json.dumps(client.get('/state').json())
            for private in ('checkpoint', 'sha256', 'outcomes', 'graded_score', '_audit'):
                self.assertNotIn(private, public)
        with TestClient(create_app(self.environment), base_url='http://127.0.0.1:8005',
                        client=('192.0.2.10', 12345)) as client:
            self.assertEqual(client.get('/').status_code, 403)

    def test_external_http_client_completes_fixture_day(self):
        script = '''
import sys, uvicorn
from wildfire_core.incident_day import fixture_inputs
from wildfire_openenv.incident_day import IncidentDayEnvironment
from eval.incident_day_server import create_app
inputs, network = fixture_inputs(24)
environment = IncidentDayEnvironment(inputs, network, expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
uvicorn.run(create_app(environment), host='127.0.0.1', port=int(sys.argv[1]), log_level='error')
'''
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-c', script, str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
        base = f'http://127.0.0.1:{port}'

        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            headers = {'Content-Type': 'application/json'} if body is not None else {}
            with urlopen(Request(base + path, data=data, headers=headers), timeout=20) as response:
                return json.load(response)

        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    result = request('/reset', {})
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            self.assertEqual(result['data']['minute'], 0)
            named = IncidentDayClient(base)
            self.assertTrue(named.check_action({'advance_minutes': 60})['valid'])
            self.assertEqual(named.route_options('plane-0')['status'], 'reachable')
            self.assertEqual(named.evacuation_options('population-0', 'destination-0')['status'], 'reachable')
            self.assertTrue(named.get_reports()['reports'])
            self.assertEqual(named.get_state()['state']['minute'], 0)
            self.assertEqual(named.request_status('center-0')['data']['minute'], 1)
            self.assertEqual(named.reset()['data']['minute'], 0)
            typed = make_environment()
            core = make_environment().core
            typed_initial = typed.reset()
            core_initial, _info = core.reset()
            self.assertEqual(typed_initial.data, core_initial)
            self.assertEqual(result['data'], core_initial)
            for _ in range(500):
                result = named.step({'advance_minutes': 60})
                typed_result = typed.step(IncidentDayAction(advance_minutes=60))
                core_observation, core_reward, core_terminated, core_truncated, _info = core.step({'advance_minutes': 60})
                self.assertEqual(result['data'], core_observation)
                self.assertEqual(typed_result.data, core_observation)
                self.assertEqual(result['reward'], core_reward)
                self.assertEqual(typed_result.reward, core_reward)
                self.assertEqual((result['terminated'], result['truncated']), (core_terminated, core_truncated))
                self.assertEqual((typed_result.terminated, typed_result.truncated), (core_terminated, core_truncated))
                if result['done']:
                    break
            self.assertTrue(result['terminated'])
            self.assertFalse(result['truncated'])
            self.assertEqual(result['data']['minute'], 1440)
            self.assertGreater(core_reward, 0.0)
            self.assertNotIn('outcomes', result['metadata'])
            state = request('/state')
            self.assertEqual(state['state']['minute'], 1440)
            self.assertEqual(state['trajectory'][-1]['minute'], 1440)
            self.assertGreater(len(state['trajectory']), 1)
            self.assertEqual(state['trajectory'], typed.trajectory())
            for private in ('checkpoint', 'sha256', 'outcomes', 'graded_score', '_audit'):
                self.assertNotIn(private, json.dumps(state))
            rollout = subprocess.run([sys.executable, '-m', 'wildfire_openenv.incident_day_rollout',
                '--base-url', base], capture_output=True, text=True, timeout=60, env=os.environ.copy())
            self.assertEqual(rollout.returncode, 0, rollout.stderr)
            self.assertEqual(json.loads(rollout.stdout)['minute'], 1440)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
