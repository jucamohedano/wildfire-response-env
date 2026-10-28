import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agentenv_roundtrip', ROOT / 'eval/check_agentenv_dataset_roundtrip.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DatasetRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.trace = {'initial_observation': {'minute': 0}, 'transitions': [
            {'observation': {'minute': 2}, 'action': {'advance': True}, 'objective_reward': 0.5,
             'action_valid': True, 'terminated': False, 'truncated': False},
            {'observation': {'minute': 4}, 'action': {}, 'objective_reward': -1,
             'action_valid': True, 'terminated': True, 'truncated': False}]}
        self.rows = [dict(step=i, minute=2*i, next_minute=2*i+2,
                          observation=json.dumps({'minute': 2*i}), action=json.dumps(t['action']),
                          next_observation=json.dumps(t['observation']), reward=t['objective_reward'],
                          action_valid=t['action_valid'], terminated=t['terminated'], truncated=t['truncated'])
                     for i, t in enumerate(self.trace['transitions'])]

    def test_sorted_full_trace_roundtrip(self):
        self.assertEqual(checker.compare_transitions(self.trace, self.rows[::-1]), 2)

    def test_detects_changed_action_reward_and_terminal(self):
        for key, value in [('action', '{}'), ('reward', 0), ('terminated', True)]:
            rows = copy.deepcopy(self.rows)
            rows[0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                checker.compare_transitions(self.trace, rows)

    def test_rejects_missing_or_duplicate_step(self):
        with self.assertRaisesRegex(ValueError, 'count'):
            checker.compare_transitions(self.trace, self.rows[:1])
        rows = copy.deepcopy(self.rows)
        rows[1]['step'] = 0
        with self.assertRaisesRegex(ValueError, 'step'):
            checker.compare_transitions(self.trace, rows)

class FrameworkLinkTests(unittest.TestCase):
    def test_valid_shape_trace_hash_drift_is_rejected(self):
        import hashlib
        spec = importlib.util.spec_from_file_location('native_fixture_hash', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        trace = {'task_id': 'dev', 'transitions': [{'action': {}, 'objective_reward': 0.5}]}
        expected = hashlib.sha256(native.canonical(trace)).hexdigest()
        native.verify_loaded_trace(trace, expected)
        changed = copy.deepcopy(trace)
        changed['transitions'][0]['action'] = {'advance': True}
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            native.verify_loaded_trace(changed, expected)

    def test_native_reward_is_the_replayed_score(self):
        spec = importlib.util.spec_from_file_location('native_fixture_reward', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        native.verify_reward(0.42, 0.42 + 1e-13, 0.42)
        with self.assertRaisesRegex(ValueError, 'differs'):
            native.verify_reward(1.0, 0.42, 0.42)
        with self.assertRaisesRegex(ValueError, 'finite'):
            native.verify_reward(float('nan'), 0.42, 0.42)

    def test_requires_exact_completed_native_episode_and_driver(self):
        spec = importlib.util.spec_from_file_location('native_fixture', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        good = {'instance_id': 'run-1', 'status': 'completed',
                'completed_steps': [{'step_id': name, 'status': 'success'} for name in ('deploy', 'drive', 'verify')],
                'context': {'metadata': {'script_results': {'drive': {'model_used': False}}}}}
        self.assertEqual(native.choose_run([good], 'run-1', lambda x: x)[0], good)
        for instances, episode in [([], 'run-1'), ([good, good], 'run-1'), ([good], 'run-2')]:
            with self.subTest(instances=instances, episode=episode), self.assertRaises(ValueError):
                native.choose_run(instances, episode, lambda x: x)
        missing = copy.deepcopy(good)
        del missing['context']['metadata']['script_results']
        with self.assertRaisesRegex(ValueError, 'driver result'):
            native.choose_run([missing], 'run-1', lambda x: x)


if __name__ == '__main__':
    unittest.main()
