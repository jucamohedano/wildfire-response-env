"""Small tests for synthetic assumptions and the source-approval boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval import check_incident_day_fuel_sensitivity as sensitivity
from eval.check_incident_day_fuel_sensitivity import compile_assumptions
from envs.wildfire_response.incident_day.data import digest, load_inputs


def repin(document):
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})


class FakeDayEnvironment:
    interrupt = False
    interrupt_verification = False
    censor = False
    verification_calls = 0

    def __init__(self, *args, **kwargs):
        self.terminated = self.truncated = self.censored = False
        self.minute = 0

    def reset(self, scenario):
        return {}, {}

    def step(self, action):
        if self.interrupt:
            raise TimeoutError('test budget exhausted')
        self.minute = 50 if self.censor else 1440
        self.censored = self.truncated = self.censor
        self.terminated = not self.censor

    def outcomes(self):
        return {'inherited_burned_hectares': 25, 'additional_burned_hectares': 50,
                'new_exposed_population': 0}

    def verify_episode(self):
        if self.interrupt_verification:
            raise TimeoutError('test replay budget exhausted')
        type(self).verification_calls += 1
        return {'passed': True, 'exact_replay': True, 'independent_reward_agreement': True}


class FuelSensitivityTests(unittest.TestCase):
    def fixture(self):
        path = Path(__file__).resolve().parents[1] / 'data/incident_day_v2/synthetic_fixture.json'
        data = json.loads(path.read_text())
        prepared, domain, network = data['prepared'], data['domain'], data['network']
        prepared['fuel_classes'][0][0] = 0
        prepared['fuel_valid_mask'][0][0] = 0
        repin(prepared)
        domain['prepared_inputs_sha256'] = prepared['sha256']
        repin(domain)
        return prepared, domain, network

    def test_assumptions_never_resolve_or_approve_source(self):
        packs = compile_assumptions(*self.fixture(), expected_unknown=1)
        self.assertEqual([pack['fuel_classes'][0][0] for pack in packs], [1, 4, 8])
        for pack in packs:
            self.assertEqual(pack['fuel_known_mask'][0][0], 0)
            self.assertEqual(pack['approval_status'], 'unapproved_source_pack')
            with self.assertRaisesRegex(ValueError, 'unapproved'):
                load_inputs(pack, pack['sha256'])

    def test_incorrect_unknown_count_fails(self):
        with self.assertRaisesRegex(ValueError, 'Expected 80 unknown cells'):
            compile_assumptions(*self.fixture())

    def test_invalid_wall_budgets_and_pin_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            for budget in (True, False, float('nan'), float('inf'), 0, 121):
                with self.subTest(budget=budget), self.assertRaises(ValueError):
                    sensitivity.run(directory, output, budget)
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                sensitivity.run(directory, output, expected_core_sha256='invalid')
            self.assertFalse(output.exists())

    def test_caller_pin_and_optional_verification(self):
        expected_pin = 'a' * 64
        packs = [{'sha256': str(fuel_class) * 64, 'approval_status': 'unapproved_source_pack'}
                 for fuel_class in sensitivity.CLASSES]
        FakeDayEnvironment.interrupt = False
        FakeDayEnvironment.verification_calls = 0
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sensitivity, 'implementation_digest', return_value=expected_pin), \
             patch.object(sensitivity, 'read_document', return_value={}), \
             patch.object(sensitivity, 'compile_assumptions', return_value=packs), \
             patch.object(sensitivity, 'IncidentDayEnv', FakeDayEnvironment):
            report = sensitivity.run(directory, Path(directory) / 'output',
                                     expected_core_sha256=expected_pin, verify_episode=True)
        self.assertEqual(report['status'], 'complete_unapproved_sensitivity')
        self.assertEqual(report['core_sha256'], expected_pin)
        self.assertEqual(FakeDayEnvironment.verification_calls, 3)
        self.assertTrue(all(item['verification'] == 'passed' and not item['graded'] for item in report['results']))
        self.assertTrue(all('graded_score' not in item for item in report['results']))

    def test_timeout_marks_partial_case_interrupted(self):
        expected_pin = 'b' * 64
        packs = [{'sha256': 'c' * 64, 'approval_status': 'unapproved_source_pack'}]
        FakeDayEnvironment.interrupt = True
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(sensitivity, 'implementation_digest', return_value=expected_pin), \
                 patch.object(sensitivity, 'read_document', return_value={}), \
                 patch.object(sensitivity, 'compile_assumptions', return_value=packs), \
                 patch.object(sensitivity, 'IncidentDayEnv', FakeDayEnvironment):
                output = Path(directory) / 'output'
                report = sensitivity.run(directory, output, expected_core_sha256=expected_pin)
                saved = json.loads((output / 'report.json').read_text())
            self.assertEqual(saved, report)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['results'][0]['status'], 'interrupted_ungraded')
            self.assertFalse(report['results'][0]['graded'])
        finally:
            FakeDayEnvironment.interrupt = False

    def test_timeout_during_replay_marks_verification_interrupted(self):
        expected_pin = 'b' * 64
        packs = [{'sha256': 'c' * 64, 'approval_status': 'unapproved_source_pack'}]
        FakeDayEnvironment.interrupt_verification = True
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(sensitivity, 'implementation_digest', return_value=expected_pin), \
                 patch.object(sensitivity, 'read_document', return_value={}), \
                 patch.object(sensitivity, 'compile_assumptions', return_value=packs), \
                 patch.object(sensitivity, 'IncidentDayEnv', FakeDayEnvironment):
                report = sensitivity.run(directory, Path(directory) / 'output',
                                         expected_core_sha256=expected_pin, verify_episode=True)
            self.assertEqual(report['results'][0]['status'], 'interrupted_ungraded')
            self.assertEqual(report['results'][0]['verification'], 'interrupted')
            self.assertFalse(report['results'][0]['graded'])
        finally:
            FakeDayEnvironment.interrupt_verification = False

    def test_censored_cases_can_be_verified_without_grade(self):
        expected_pin = 'b' * 64
        packs = [{'sha256': 'c' * 64, 'approval_status': 'unapproved_source_pack'} for fuel_class in sensitivity.CLASSES]
        FakeDayEnvironment.censor = True
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(sensitivity, 'implementation_digest', return_value=expected_pin), \
                 patch.object(sensitivity, 'read_document', return_value={}), \
                 patch.object(sensitivity, 'compile_assumptions', return_value=packs), \
                 patch.object(sensitivity, 'IncidentDayEnv', FakeDayEnvironment):
                report = sensitivity.run(directory, Path(directory) / 'output',
                                         expected_core_sha256=expected_pin, verify_episode=True)
            result = report['results'][0]
            self.assertEqual(result['status'], 'boundary_censored')
            self.assertEqual(result['verification'], 'passed')
            self.assertFalse(result['graded'])
            self.assertNotIn('graded_score', result)
        finally:
            FakeDayEnvironment.censor = False

    def test_mismatched_caller_pin_stops_before_data_read(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sensitivity, 'implementation_digest', return_value='a' * 64), \
             patch.object(sensitivity, 'read_document') as read_document:
            report = sensitivity.run(directory, Path(directory) / 'output', expected_core_sha256='b' * 64)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('digest differs', report['error'])
        read_document.assert_not_called()


if __name__ == '__main__':
    unittest.main()
