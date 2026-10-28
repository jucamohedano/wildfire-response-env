import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval import run_incident_day as runner


class FakeClock:
    def __init__(self):
        self.value = 0.

    def __call__(self):
        self.value += .1
        return self.value


class FakeDispatcher:
    def __init__(self, policy):
        self.asked = set()

    def action(self, environment, observation):
        self.asked.add(('center', observation['minute']))
        return {'advance_minutes': 480, 'asked': len(self.asked)}


class FakeEnv:
    truncate = False

    def __init__(self, inputs, network, **kwargs):
        self.inputs = inputs
        self.network = network
        self.minute = 0
        self.terminated = self.truncated = False
        self.actions = []
        self._audit = []

    def reset(self, scenario_id):
        self.case_id = scenario_id
        self._audit = [dict(minute=0, footprint=[1], new_footprint=[],
                            population={'group': {'count': 5, 'exposed': 0}})]
        return {'minute': self.minute}, {}

    def step(self, action):
        self.actions.append(action)
        previous = self.minute
        self.minute += 480
        self.truncated = self.truncate
        self.terminated = self.minute == 1440 and not self.truncated
        for minute in range(previous + 1, self.minute + 1):
            self._audit.append(dict(minute=minute, new_footprint=[2] if minute == 5 else [],
                                    population={'group': {'count': 5, 'exposed': int(minute >= 500)}}))
        return {'minute': self.minute}, 0., self.terminated, self.truncated, {}

    def trajectory(self):
        return {'task_id': self.case_id, 'actions': self.actions}

    def save_checkpoint(self, path):
        value = dict(minute=self.minute, case_id=self.case_id, actions=self.actions,
                     audit=self._audit, terminated=self.terminated, truncated=self.truncated)
        Path(path).write_bytes(gzip.compress(json.dumps(value).encode(), mtime=0))

    def restore(self, state):
        self.minute, self.case_id = state['minute'], state['case_id']
        self.actions, self._audit = state['actions'], state['audit']
        self.terminated, self.truncated = state['terminated'], state['truncated']
        return {'minute': self.minute}

    def verify_episode(self):
        return dict(passed=True, exact_replay=True, independent_reward_agreement=True, outcomes=dict(
            completed_minutes=self.minute, score_eligible=self.terminated, graded_score=.75))


class IncidentDayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'output'
        fixture = ({'sha256': 'a' * 64, 'approval_status': 'approved_synthetic_fixture'},
                   {'sha256': 'b' * 64})
        for name, value in [('fixture_inputs', lambda: fixture), ('IncidentDayEnv', FakeEnv),
                            ('DayDispatcher', FakeDispatcher), ('CASE_IDS', ('case-a', 'case-b'))]:
            patcher = patch.object(runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeEnv.truncate = False

    def test_budget_checkpoint_resumes_with_dispatcher_state(self):
        report = runner.run(self.output, wall_budget=.35, clock=FakeClock())
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['cases'], [])
        checkpoint = Path(report['checkpoint'])
        self.assertTrue(checkpoint.exists())
        self.assertNotIn('score', report['current_case'])
        finished = runner.run(self.output, resume=checkpoint, clock=FakeClock())
        self.assertEqual(finished['status'], 'complete')
        self.assertEqual([item['phase'] for item in finished['cases']], ['measurement', 'batch', 'batch'])
        with gzip.open(self.output / 'private/00-case-a/trajectory.json.gz', 'rt') as stream:
            trajectory = json.load(stream)
        self.assertEqual([action['asked'] for action in trajectory['actions']], [1, 2, 3])
        self.assertTrue((self.output / 'private/00-case-a/audit.json.gz').exists())
        self.assertIn('Synthetic fixture only', (self.output / 'report.html').read_text())
        self.assertTrue(all(item['verification'] == 'passed' for item in finished['cases']))

    def test_checkpoint_tamper_and_policy_mismatch_are_rejected(self):
        report = runner.run(self.output, wall_budget=0, clock=FakeClock())
        checkpoint = Path(report['checkpoint'])
        with self.assertRaisesRegex(ValueError, 'fixture or policy'):
            runner.run(self.output, policy_name='passive', resume=checkpoint, clock=FakeClock())
        checkpoint.write_bytes(checkpoint.read_bytes() + b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Checkpoint'):
            runner.run(self.output, resume=checkpoint, clock=FakeClock())

    def test_truncated_episode_never_gets_a_success_score(self):
        FakeEnv.truncate = True
        report = runner.run(self.output, clock=FakeClock())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['cases'][0]['status'], 'incomplete')
        self.assertNotIn('score', report['cases'][0])
        self.assertEqual(report['cases'][0]['verification'], 'passed_incomplete_episode')

    def test_unapproved_fixture_cannot_start(self):
        with patch.object(runner, 'fixture_inputs', return_value=(
                {'sha256': 'a' * 64, 'approval_status': 'unapproved_source_pack'}, {'sha256': 'b' * 64})):
            with self.assertRaisesRegex(ValueError, 'real data gate is blocked'):
                runner.run(self.output)
        self.assertFalse(self.output.exists())

    def test_missing_independent_verification_fails_without_score(self):
        with patch.object(FakeEnv, 'verify_episode', return_value=dict(passed=True, exact_replay=True)):
            report = runner.run(self.output, clock=FakeClock())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['cases'][0]['verification'], 'failed')
        self.assertNotIn('score', report['cases'][0])

    def test_audit_evidence_reports_observed_changes_not_case_labels(self):
        env = FakeEnv({}, {})
        env.reset('unrelated-label')
        for unused in range(3):
            env.step({'advance_minutes': 480})
        self.assertEqual(runner.audit_phenomena(env._audit), dict(
            inherited_burning_footprint_cells=1, new_burned_cells=1, new_exposed_population=1,
            resource_behavior='not_observable_in_audit'))
        env._audit[50]['minute'] = 51
        with self.assertRaisesRegex(ValueError, 'audit coverage'):
            runner.audit_phenomena(env._audit)


if __name__ == '__main__':
    unittest.main()
