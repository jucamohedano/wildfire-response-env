import os
import unittest

from envs.wildfire_response.regional import RegionalResponseEnv
from envs.wildfire_response.regional.director.policy import RegionalDirectorPolicy, ScriptedTransport

try:
    from envs.wildfire_response.regional.director.openenv import OpenEnvDirectorSession
    from envs.wildfire_response.regional.openenv.client import RegionalClient
    from envs.wildfire_response.regional.openenv.environment import RegionalEnvironment
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


def policy():
    return RegionalDirectorPolicy(transport=ScriptedTransport(), interval=0, max_calls=120)


@unittest.skipUnless(AVAILABLE, 'Install optional OpenEnv dependencies')
class RegionalDirectorOpenEnvTests(unittest.TestCase):
    def test_director_checks_and_actions_match_the_local_adapter(self):
        core, adapter = RegionalResponseEnv(), RegionalEnvironment()
        observation, info = core.reset('regional-train-001')
        result = adapter.reset(scenario_id='regional-train-001')
        local, remote = policy(), policy()
        from envs.wildfire_response.regional.openenv.models import RegionalAction
        while not result.done:
            action = local(observation, feedback=info, check_action=core.check_action)
            def check(proposal):
                return adapter.step(RegionalAction(operation='check', **proposal)).metadata['action_check']
            remote_action = remote(result.data, feedback=result.metadata, check_action=check)
            self.assertEqual(action, remote_action)
            observation, reward, terminated, truncated, info = core.step(action)
            result = adapter.step(RegionalAction(**remote_action))
            self.assertEqual(result.data, observation)
            self.assertEqual(result.reward, reward)
            self.assertEqual(result.done, terminated or truncated)
        self.assertTrue(result.terminated)

    def test_malformed_wire_action_stops_without_a_silent_wait(self):
        from envs.wildfire_response.regional.openenv.models import RegionalAction
        adapter = RegionalEnvironment()
        adapter.reset(scenario_id='regional-train-001')
        actor = RegionalDirectorPolicy(transport=lambda payload: {}, interval=0)
        action = actor(adapter.core.observe())
        with self.assertRaises(ValueError):
            RegionalAction(**action)
        self.assertEqual(adapter.state.minute, 0)

    @unittest.skipUnless(os.environ.get('REGIONAL_TEST_URL'), 'Set REGIONAL_TEST_URL for external director checks')
    def test_external_director_session_matches_a_complete_local_episode(self):
        core, actor = RegionalResponseEnv(), policy()
        observation, info = core.reset('regional-train-001', 30001)
        with RegionalClient(base_url=os.environ['REGIONAL_TEST_URL']).sync() as client:
            session = OpenEnvDirectorSession(client, policy())
            result = session.reset('regional-train-001', 30001)
            total = 0.0
            while not result.done:
                action = actor(observation, feedback=info, check_action=core.check_action)
                observation, reward, terminated, truncated, info = core.step(action)
                result = session.step()
                self.assertEqual(result.observation.data, observation)
                self.assertEqual(result.reward, reward)
                self.assertEqual(result.done, terminated or truncated)
                total += reward
            self.assertTrue(result.observation.terminated)
            self.assertEqual(session.total_reward, total)
            with self.assertRaises(RuntimeError):
                session.step()
            session.reset('regional-train-001', 30001)
            self.assertEqual(session.policy.calls, 0)
            self.assertEqual(session.policy.history, [])
            self.assertEqual(session.total_reward, 0.0)
            with self.assertRaises(ValueError):
                session.reset('regional-test-030')


if __name__ == '__main__':
    unittest.main()
