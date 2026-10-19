import json
from pathlib import Path
import tempfile
import unittest

from envs.wildfire_response.regional.baselines import CooperativeBaseline
from envs.wildfire_response.regional.run import run
from envs.wildfire_response.regional.training.environment import RegionalTrainingEnv, outcome_reward
from envs.wildfire_response.regional.training.pilot import run as run_pilot


def complete_action(action):
    return {field: action.get(field, '' if field=='explanation' else []) for field in (
        'requests', 'authorizations', 'releases', 'messages', 'explanation')}


class RegionalTrainingTests(unittest.TestCase):
    def test_pilot_rejects_unbounded_or_incompatible_requests_before_importing_gpu_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            for options in (dict(steps=0), dict(steps=9), dict(steps=True), dict(tool_iterations=2),
                            dict(completion_tokens=9999), dict(model_id='unreviewed/model'), dict(revision='main')):
                with self.assertRaises(ValueError):
                    run_pilot(Path(directory)/'invalid', **options)
            self.assertFalse((Path(directory)/'invalid').exists())

    def test_early_stop_is_scored_at_the_full_horizon_not_as_free_waiting(self):
        environment = RegionalTrainingEnv()
        public = json.loads(environment.reset('regional-train-001'))
        self.assertNotIn('scenario', public)
        self.assertNotIn('events', public['observation'])
        self.assertEqual(environment.reward, 0)
        reward = outcome_reward([environment])[0]
        self.assertLess(reward, -2)
        self.assertEqual(environment._core.decisions, 60)
        self.assertEqual(environment.model_submissions, 0)
        self.assertTrue(environment._verification['passed'])
        self.assertEqual(environment._finish(), reward)

    def test_cooperative_tools_match_baseline_without_hidden_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = RegionalTrainingEnv(Path(directory)/'audit')
            result = json.loads(environment.reset('regional-train-001'))
            policy = CooperativeBaseline()
            while not result['done']:
                action = complete_action(policy(result['observation']))
                minute = result['observation']['minute']
                self.assertTrue(json.loads(environment.check_action(**action))['valid'])
                self.assertEqual(environment._core.sim.tick, minute)
                result = json.loads(environment.submit_action(**action))
            reference = run(Path(directory)/'reference', 'regional-train-001')
            self.assertEqual(environment._finish(), reference['total_reward'])
            self.assertEqual(len(list((Path(directory)/'audit').glob('*/verification.json'))), 1)
            with self.assertRaises(ValueError):
                environment.submit_action(**complete_action({}))

    def test_invalid_tools_preserve_engine_penalties_and_reset_isolation(self):
        environment = RegionalTrainingEnv()
        environment.reset('regional-train-001')
        invalid = complete_action(dict(authorizations=['not-an-offer']))
        for _ in range(3):
            environment.submit_action(**invalid)
        self.assertLess(environment._finish(), -102)
        self.assertEqual(environment._core.counters['invalid_actions'], 3)
        first = environment.reset('regional-train-001')
        self.assertEqual(first, environment.reset('regional-train-001'))
        self.assertEqual(environment.model_submissions, 0)
        self.assertEqual(environment.reward, 0)
        for options in (dict(scenario_id='regional-test-030'),dict(seed=999)):
            with self.assertRaises(ValueError):
                environment.reset(**options)


if __name__ == '__main__':
    unittest.main()
