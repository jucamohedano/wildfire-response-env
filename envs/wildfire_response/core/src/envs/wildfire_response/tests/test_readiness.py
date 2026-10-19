import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envs.wildfire_response.baselines import passive
from envs.wildfire_response.core.environment import WildfireResponseEnv
from envs.wildfire_response.core.reward import WEIGHTS
from envs.wildfire_response.eval.audit import reward_audit, rollout, warning_fixture
from envs.wildfire_response.eval.hillclimb import VerificationPolicy, balanced_tasks, search


class ReadinessTests(unittest.TestCase):
    def test_reward_checks_fail_closed(self):
        def fake_rollout(task, policy, mode):
            values = dict.fromkeys(WEIGHTS, 0)
            reward = 0
            if mode in ('urgent_delayed', 'urgent_passive'):
                values['exposed_population'], reward = .1, -10
            if mode == 'false_alarm_blanket_evacuation':
                values['unnecessary_evacuation'], reward = 1, -.1
            if mode == 'repeated_notices':
                values['redundant_messages'], reward = 3, -.006
            if mode == 'invalid_abort':
                values['invalid_actions'], reward = 3, -102
            return dict(total_reward=reward, components=values, terminated=True, truncated=False)
        with tempfile.TemporaryDirectory() as directory:
            with patch('envs.wildfire_response.eval.audit.rollout', side_effect=fake_rollout):
                self.assertTrue(reward_audit(Path(directory) / 'good')['passed'])
            def broken(task, policy, mode):
                result = fake_rollout(task, policy, mode)
                if mode == 'repeated_notices':
                    result['total_reward'] = 1
                return result
            with patch('envs.wildfire_response.eval.audit.rollout', side_effect=broken):
                self.assertFalse(reward_audit(Path(directory) / 'bad')['passed'])

    def test_rollout_reward_reconstruction(self):
        task = warning_fixture(False)
        task['minimum_ticks'] = 8
        result = rollout(task, passive, 'quiet')
        self.assertEqual(result['total_reward'], result['reconstructed_reward'])
        self.assertTrue(result['terminated'])
        self.assertFalse(result['truncated'])

    def test_verification_policy_uses_public_candidates(self):
        env = WildfireResponseEnv()
        observation, _ = env.reset('train-001')
        action = VerificationPolicy(True)(observation)
        self.assertEqual(action['messages'], [])
        self.assertEqual(action['fleet']['scout_orders'][0]['waypoints'],
                         [observation['candidates']['reconnaissance'][0]])
        env._validate_action(action)

    def test_search_freezes_training_selection_before_validation(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = root / 'readiness.json'
            gate.write_text(json.dumps(dict(ready_for_bounded_training_search=True)))
            def fake_evaluate(tasks, verify_first, output):
                split = tasks[0]['split']
                calls.append((split, verify_first))
                if split == 'validation':
                    frozen = json.loads((root / 'search' / 'selected-policy.json').read_text())
                    self.assertTrue(frozen['verify_first'])
                score = (0 if verify_first else -1) if split == 'train' else (-10 if verify_first else 0)
                return dict(mean_reward=score, invalid_actions=0, rows=[])
            with patch('envs.wildfire_response.eval.hillclimb.evaluate', side_effect=fake_evaluate):
                result = search(root / 'search', gate)
            self.assertTrue(result['selected'])
            self.assertEqual(calls, [('train', False), ('train', True), ('validation', False), ('validation', True)])
            gate.write_text(json.dumps(dict(ready_for_bounded_training_search=False)))
            with self.assertRaises(ValueError):
                search(root / 'blocked', gate)
        with self.assertRaises(ValueError):
            balanced_tasks('test')


if __name__ == '__main__':
    unittest.main()
