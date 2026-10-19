import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envs.wildfire_response.director.batch import batch
from envs.wildfire_response.director.policy import ProviderHTTPError


class BatchTests(unittest.TestCase):
    def test_first_provider_limit_stops_entire_batch(self):
        calls = []
        def transport(payload):
            calls.append(payload)
            raise ProviderHTTPError(429)
        def fake_run(output, identifier, **kwargs):
            return kwargs['transport']({})
        with tempfile.TemporaryDirectory() as directory, \
             patch('envs.wildfire_response.director.batch.run', side_effect=fake_run):
            root = Path(directory) / 'batch'
            result = batch(root, transport=transport)
            self.assertEqual(result['stop_reason'], 'provider_limit')
            self.assertEqual(result['provider_status'], 429)
            self.assertEqual(result['requests'], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['completed'], [])
            self.assertEqual(json.loads((root / 'summary.json').read_text())['status'], 'stopped')

    def test_local_budget_is_shared_across_scenarios_and_not_a_provider_limit(self):
        calls = []
        def transport(payload):
            calls.append(payload)
            return {}
        def fake_run(output, identifier, **kwargs):
            kwargs['transport']({})
            return dict(scenario_id=identifier)
        with tempfile.TemporaryDirectory() as directory, \
             patch('envs.wildfire_response.director.batch.run', side_effect=fake_run):
            result = batch(Path(directory) / 'batch', max_requests=1, transport=transport)
            self.assertEqual(result['stop_reason'], 'local_request_budget')
            self.assertIsNone(result['provider_status'])
            self.assertEqual(result['requests'], 1)
            self.assertEqual(len(result['completed']), 1)
            self.assertEqual(len(calls), 1)

    def test_access_rejection_is_not_misreported_as_quota(self):
        def fake_run(output, identifier, **kwargs):
            raise ProviderHTTPError(403)
        with tempfile.TemporaryDirectory() as directory, \
             patch('envs.wildfire_response.director.batch.run', side_effect=fake_run):
            result = batch(Path(directory) / 'batch', transport=lambda payload: {})
            self.assertEqual(result['stop_reason'], 'provider_access_or_service_error')
            self.assertEqual(result['provider_status'], 403)

    def test_held_out_cases_are_rejected_before_transport_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                batch(Path(directory) / 'batch', transport=lambda payload: {}, scenarios=['test-100'])

    def test_runtime_error_type_is_preserved_without_retry(self):
        def fake_run(output, identifier, **kwargs):
            raise ValueError('test failure')
        with tempfile.TemporaryDirectory() as directory, \
             patch('envs.wildfire_response.director.batch.run', side_effect=fake_run) as runner:
            result = batch(Path(directory) / 'batch', transport=lambda payload: {})
            self.assertEqual(result['error_type'], 'ValueError')
            self.assertEqual(result['stop_reason'], 'transport_or_runtime_error')
            self.assertEqual(runner.call_count, 1)


if __name__ == '__main__':
    unittest.main()
