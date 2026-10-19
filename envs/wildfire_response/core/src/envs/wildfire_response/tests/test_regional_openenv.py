import json
import os
import unittest

from envs.wildfire_response.regional import RegionalResponseEnv

try:
    from envs.wildfire_response.regional.openenv.client import RegionalClient
    from envs.wildfire_response.regional.openenv.environment import RegionalEnvironment
    from envs.wildfire_response.regional.openenv.models import RegionalAction
    AVAILABLE=True
except ImportError:
    AVAILABLE=False


@unittest.skipUnless(AVAILABLE,'Install optional OpenEnv dependencies')
class RegionalAdapterTests(unittest.TestCase):
    def test_checks_and_advance_match_the_core_without_private_state(self):
        core=RegionalResponseEnv()
        adapter=RegionalEnvironment()
        observation,_=core.reset('regional-train-002',30002)
        remote=adapter.reset(scenario_id='regional-train-002',seed=30002)
        self.assertEqual(remote.data,observation)
        check=core.check_action({})
        checked=adapter.step(RegionalAction(operation='check'))
        self.assertEqual(checked.data,observation)
        self.assertEqual(checked.metadata['action_check'],check)
        self.assertEqual(checked.reward,0)
        self.assertEqual(adapter.state.step_count,0)
        local=core.step({})
        remote=adapter.step(RegionalAction())
        self.assertEqual(remote.data,local[0])
        self.assertEqual(remote.reward,local[1])
        for name in ('events','fire_checkpoint','audit_minutes','family'):
            self.assertNotIn('"'+name+'"',json.dumps(adapter.state.model_dump()))

    def test_discovery_is_opaque_and_wire_schema_is_bounded(self):
        adapter=RegionalEnvironment()
        self.assertEqual([adapter.num_tasks(split) for split in adapter.list_splits()],[24,6,6])
        self.assertEqual(set(adapter.get_task('train',0)),{'id','split'})
        for payload in [dict(events=[]),dict(operation='reveal_truth'),dict(requests=[{}])]:
            with self.assertRaises(ValueError):
                RegionalAction(**payload)
        with self.assertRaises(ValueError):
            adapter.get_task('train',-1)

    @unittest.skipUnless(os.environ.get('REGIONAL_TEST_URL'),'Set REGIONAL_TEST_URL for external client checks')
    def test_external_client_checks_and_finishes_the_same_episode(self):
        core=RegionalResponseEnv()
        observation,_=core.reset('regional-train-002',30002)
        with RegionalClient(base_url=os.environ['REGIONAL_TEST_URL']).sync() as client:
            result=client.reset(scenario_id='regional-train-002',seed=30002)
            self.assertEqual(result.observation.data,observation)
            check=core.check_action({})
            remote_check=client.step(RegionalAction(operation='check'))
            self.assertEqual(remote_check.observation.metadata['action_check'],check)
            self.assertEqual(remote_check.observation.data,observation)
            while not result.done:
                observation,value,terminated,truncated,info=core.step({})
                result=client.step(RegionalAction())
                self.assertEqual(result.observation.data,observation)
                self.assertEqual(result.reward,value)
                self.assertEqual(result.done,terminated or truncated)
            self.assertTrue(result.observation.terminated)


if __name__=='__main__':
    unittest.main()
