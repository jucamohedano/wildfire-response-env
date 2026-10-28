import unittest

try:
    from fastapi.testclient import TestClient
    from eval.incident_day_server import create_app
    from wildfire_core.incident_day import EGRESS_CASE_IDS, IncidentDayEnv, fixture_inputs
    from wildfire_openenv.incident_day import IncidentDayEnvironment
    from envs.wildfire_response.incident_day.baselines import DayDispatcher
    AVAILABLE = True
except ModuleNotFoundError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Run with the dedicated OpenEnv/FastAPI runtime')
class IncidentDayHTTPDispatchTests(unittest.TestCase):
    def test_external_named_routes_and_dispatched_day_match_local_actions(self):
        self._assert_dispatched_day('incident-day-dev-000')

    def test_external_withdrawal_day_matches_local_execution(self):
        local = self._assert_dispatched_day(EGRESS_CASE_IDS[0])
        self.assertTrue(any(record['emergency_withdrawals'] for record in local._audit))

    def _assert_dispatched_day(self, task_id):
        inputs, network = fixture_inputs()
        arguments = dict(expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
        local = IncidentDayEnv(inputs, network, **arguments)
        remote = IncidentDayEnvironment(inputs, network, **arguments)
        policy = DayDispatcher()
        observation, unused = local.reset(task_id)
        parent = self
        with TestClient(create_app(remote), base_url='http://127.0.0.1:8005', client=('127.0.0.1', 12345)) as client:
            reset_response = client.post('/reset', json={'scenario_id': task_id})
            self.assertEqual(reset_response.status_code, 200, reset_response.text)
            initial = reset_response.json()
            self.assertEqual(initial['data'], observation)

            class RouteProxy:
                def route_options(self, resource_id):
                    expected = local.route_options(resource_id)
                    result = client.post('/routes', json=dict(resource_id=resource_id)).json()
                    parent.assertEqual(result, expected)
                    return result

                def evacuation_options(self, group_id, destination_id):
                    expected = local.evacuation_options(group_id, destination_id)
                    result = client.post('/evacuation-options', json=dict(group_id=group_id, destination_id=destination_id)).json()
                    parent.assertEqual(result, expected)
                    return result

            proxy = RouteProxy()
            for unused in range(1000):
                action = policy.action(proxy, observation)
                observation, reward, terminated, truncated, info = local.step(action)
                response = client.post('/step', json=dict(action=action))
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result['data'], observation)
                self.assertEqual(result['reward'], reward)
                self.assertEqual(result['terminated'], terminated)
                self.assertEqual(result['truncated'], truncated)
                if terminated or truncated:
                    break
        self.assertEqual(local.minute, 1440)
        self.assertTrue(local.terminated)
        self.assertTrue(any(action['action'].get('authorizations') for action in local.trajectory()['transitions']))
        self.assertTrue(local.verify_episode()['independent_reward_agreement'])
        self.assertEqual(local.trajectory(), remote.core.trajectory())
        return local


if __name__ == '__main__':
    unittest.main()
