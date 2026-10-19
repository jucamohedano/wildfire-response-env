import os
import unittest

from envs.wildfire_response import WildfireResponseEnv
from envs.wildfire_response.baselines import DeterministicBaseline, passive

try:
    from envs.wildfire_response.openenv.client import WildfireClient
    from envs.wildfire_response.openenv.environment import WildfireEnvironment
    from envs.wildfire_response.openenv.models import WildfireAction
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Install the optional openenv dependencies')
class AdapterTests(unittest.TestCase):
    def test_adapter_matches_core_and_has_safe_state(self):
        local = WildfireResponseEnv()
        adapter = WildfireEnvironment()
        observation, _ = local.reset('train-000', 4)
        remote = adapter.reset(scenario_id='train-000', seed=4)
        self.assertEqual(remote.data, observation)
        for _ in range(2):
            action = passive(observation)
            observation, value, terminated, truncated, info = local.step(action)
            remote = adapter.step(WildfireAction(**action))
            self.assertEqual(remote.data, observation)
            self.assertEqual(remote.reward, value)
            self.assertEqual(remote.metadata, info)
            self.assertEqual(remote.done, terminated or truncated)
        self.assertNotIn('checkpoint', adapter.state.model_dump())

    def test_discovery_excludes_events_and_annotations(self):
        adapter = WildfireEnvironment()
        self.assertEqual(adapter.num_tasks('train'), 80)
        self.assertEqual(set(adapter.get_task('test', 0)), {'id', 'split'})
        with self.assertRaises(ValueError):
            adapter.list_tasks('unknown')

    @unittest.skipUnless(os.environ.get('WILDFIRE_TEST_URL'), 'Set WILDFIRE_TEST_URL for an external server')
    def test_external_client_completes_same_episode(self):
        local = WildfireResponseEnv()
        observation, _ = local.reset('train-000', 10000)
        policy = passive
        with WildfireClient(base_url=os.environ['WILDFIRE_TEST_URL']).sync() as client:
            remote = client.reset(scenario_id='train-000', seed=10000)
            self.assertEqual(remote.observation.data, observation)
            while not remote.done:
                action = policy(observation)
                observation, value, terminated, truncated, info = local.step(action)
                remote = client.step(WildfireAction(**action))
                self.assertEqual(remote.observation.data, observation)
                self.assertEqual(remote.reward, value)
                self.assertEqual(remote.done, terminated or truncated)
                self.assertEqual(remote.observation.metadata, info)
            self.assertTrue(remote.done)


if __name__ == '__main__':
    unittest.main()
