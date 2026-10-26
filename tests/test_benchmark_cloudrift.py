import gzip
import json
from pathlib import Path
import tempfile
import unittest

from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkCloudRiftPolicy
from envs.wildfire_response.regional.ops.cloudrift import MODEL, CloudRiftHTTPError
from eval.run_benchmark_cloudrift import run
from test_benchmark_core import create


class BenchmarkCloudRiftTests(unittest.TestCase):
    def test_policy_requires_opt_in_exact_model_and_new_contract(self):
        with self.assertRaises(ValueError):
            BenchmarkCloudRiftPolicy(model=MODEL, transport=lambda payload: {})
        with self.assertRaises(ValueError):
            BenchmarkCloudRiftPolicy(model='another:free', transport=lambda payload: {}, allow_cloudrift=True)
        actor = BenchmarkCloudRiftPolicy(model=MODEL, transport=lambda payload: {}, allow_cloudrift=True)
        self.assertEqual(actor.contract_version, 'wildfire-benchmark-model-tools-v1')
        environment = create()
        observation, _ = environment.reset(environment.catalog['development'][0]['id'])
        observation['observation_version'] = 'old-contract'
        with self.assertRaisesRegex(ValueError, 'delivered-evidence'):
            actor(observation)

    def test_complete_fake_episode_is_verified_but_never_labelled_model_inference(self):
        payloads = []
        def transport(payload):
            payloads.append(payload)
            return dict(model='fake-transport', choices=[dict(finish_reason='tool_calls', message=dict(
                tool_calls=[dict(type='function', id='wait', function=dict(name='advance_time', arguments='{}'))]))])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'run'
            result = run(output, create, transport, families=('false_alarm',), max_requests=60)
            self.assertEqual(result['mode'], 'injected_transport_test')
            self.assertEqual(result['status'], 'complete')
            episode = result['episodes'][0]
            self.assertTrue(episode['scored'])
            self.assertEqual(episode['minute'], 120)
            self.assertEqual(episode['decisions'], 60)
            self.assertEqual(episode['outcomes']['graded_score'], 1)
            self.assertEqual(episode['invalid_actions'], 0)
            self.assertFalse(result['rank_eligible'])
            self.assertFalse(result['training_eligible'])
            self.assertEqual(len(payloads), 60)
            self.assertIn('get_events', [tool['function']['name'] for tool in payloads[0]['tools']])
            directory = output/episode['task_id']/'model'
            trace = json.loads(gzip.decompress((directory/'trajectory.json.gz').read_bytes()))
            self.assertEqual(len(trace['transitions']), 60)
            self.assertTrue(json.loads((directory/'private'/'verification.json').read_text())['passed'])
            self.assertTrue((output/'report.html').exists())
            from eval.render_benchmark_cloudrift import render
            report = Path(temporary)/'public'/'report.html'
            render(output, report)
            artifact = json.loads(report.with_suffix('.json').read_text())
            self.assertIn('0 of 1 attempted episodes abandoned', artifact['interpretation'])
            self.assertIn('1 reached the full120-minute horizon', artifact['interpretation'])
            self.assertEqual(artifact['failure_counts'], {})
            self.assertNotIn('Protocol abandonment score', report.read_text())
            self.assertIn('consecutive wait actions', report.read_text())
            comparison = Path(temporary)/'comparison.json'
            comparison.write_text(json.dumps(artifact))
            render(output, report, comparison)
            compared = json.loads(report.with_suffix('.json').read_text())
            self.assertFalse(compared['comparison']['model_only_comparison'])
            artifact['summary']['experiment']['ticks_per_action'] = 1
            comparison.write_text(json.dumps(artifact))
            with self.assertRaisesRegex(ValueError, 'different scenario, clock'):
                render(output, report, comparison)

    def test_rejected_response_details_retained_and_three_strikes_abandon(self):
        def malformed(payload):
            return dict(choices=[dict(finish_reason='tool_calls', message=dict(tool_calls=[
                dict(type='function', function=dict(name='advance_time', arguments='{"unexpected":1}'))]))])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'run'
            result = run(output, create, malformed, families=('false_alarm',))
            episode = result['episodes'][0]
            self.assertEqual(episode['decisions'], 3)
            self.assertEqual(episode['protocol_errors'], 3)
            self.assertTrue(episode['outcomes']['invalid_abandonment'])
            self.assertEqual(episode['outcomes']['graded_score'], 0)
            records = json.loads((output/episode['task_id']/'model'/'requests.json').read_text())
            self.assertIn('unexpected', json.dumps(records[0]['response_diagnostics']))

    def test_provider_limit_stops_entire_sample_and_preserves_unscored_partial(self):
        attempts = []
        def limited(payload):
            attempts.append(payload)
            raise CloudRiftHTTPError(429, 'Provider limit')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'run'
            result = run(output, create, limited, families=('false_alarm', 'uncertain_smoke'))
            self.assertEqual(len(attempts), 1)
            self.assertEqual(result['status'], 'stopped')
            self.assertEqual(result['stop_reason']['http_status'], 429)
            self.assertEqual(len(result['episodes']), 1)
            episode = result['episodes'][0]
            self.assertFalse(episode['scored'])
            self.assertNotIn('outcomes', episode)
            self.assertEqual(episode['decisions'], 0)
            self.assertTrue((output/episode['task_id']/'model'/'trajectory.json.gz').exists())

    def test_budget_refusal_does_not_become_an_invalid_model_action(self):
        def refused(payload):
            raise RuntimeError('Conservative CloudRift estimated cost cap reached before next request')
        with tempfile.TemporaryDirectory() as temporary:
            result = run(Path(temporary)/'run', create, refused, families=('false_alarm',))
            self.assertEqual(result['status'], 'stopped')
            self.assertIn('cap reached', result['stop_reason']['reason'])
            episode = result['episodes'][0]
            self.assertEqual(episode['invalid_actions'], 0)
            self.assertEqual(episode['protocol_errors'], 0)
            self.assertFalse(episode['scored'])


if __name__ == '__main__':
    unittest.main()
