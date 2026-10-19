import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envs.wildfire_response.director.policy import ProviderHTTPError
from envs.wildfire_response.eval.run import load
from envs.wildfire_response.regional import RegionalResponseEnv
from envs.wildfire_response.regional.director.contract import action_schema, parse_action, public_context
from envs.wildfire_response.regional.director.policy import RegionalDirectorPolicy, ScriptedTransport
from envs.wildfire_response.regional.director.run import run


def wait_action():
    return dict(requests=[], authorizations=[], releases=[], messages=[], explanation='Explicitly wait')


def response(action, name='submit_action', finish_reason='tool_calls'):
    return dict(model='test-not-inference', choices=[dict(finish_reason=finish_reason, message=dict(
        tool_calls=[dict(id='test-call', type='function', function=dict(name=name, arguments=json.dumps(action)))]))])


class RegionalDirectorTests(unittest.TestCase):
    def setUp(self):
        self.env = RegionalResponseEnv()
        self.observation, _ = self.env.reset('regional-train-001')

    def test_context_has_all_public_evidence_and_no_private_fields(self):
        observation = copy.deepcopy(self.observation)
        observation.update(future_events=['secret'], private_annotations=['secret'], family='secret')
        for field in ('resources', 'reports', 'districts', 'sectors'):
            observation[field][0]['private_label'] = 'secret'
        observation['geography']['fire_checkpoint'] = 'secret'
        context = public_context(observation)
        self.assertNotIn('secret', json.dumps(context))
        self.assertEqual(context['sensor_memory'], self.observation['sensor_memory'])
        self.assertEqual(context['reports'], self.observation['reports'])

    def test_named_checks_do_not_advance_and_revised_action_is_unchanged(self):
        proposals = []
        invalid = wait_action()
        invalid['authorizations'] = ['invented-offer']
        valid = wait_action()
        replies = [response(invalid, 'check_action'), response(valid, 'submit_action')]
        def transport(payload):
            proposals.append(payload)
            return replies.pop(0)
        policy = RegionalDirectorPolicy(transport=transport, interval=0)
        before = copy.deepcopy(self.env.evaluator_state())
        action = policy(self.observation, check_action=self.env.check_action)
        self.assertEqual(action, valid)
        self.assertEqual(before, self.env.evaluator_state())
        feedback = json.loads(proposals[1]['messages'][-1]['content'])
        self.assertFalse(feedback['valid'])
        self.assertEqual(policy.calls, 2)
        self.assertFalse(proposals[0]['provider']['allow_fallbacks'])
        self.assertFalse(proposals[0]['parallel_tool_calls'])
        self.assertEqual(proposals[0]['provider']['max_price'], dict(prompt=0, completion=0))
        self.assertEqual(proposals[0]['tool_choice'], 'required')
        self.assertTrue(all(tool['function']['strict'] for tool in proposals[0]['tools']))

    def test_three_checks_then_only_submission_is_offered(self):
        requests = []
        def transport(payload):
            requests.append(payload)
            return response(wait_action(), 'check_action' if len(requests)<4 else 'submit_action')
        policy = RegionalDirectorPolicy(max_calls=4, transport=transport, interval=0)
        self.assertEqual(policy(self.observation, check_action=self.env.check_action), wait_action())
        self.assertEqual(self.env.preview_remaining, 0)
        self.assertEqual([tool['function']['name'] for tool in requests[-1]['tools']], ['submit_action'])
        self.assertEqual(self.env.sim.tick, 0)

    def test_malformed_response_is_penalized_not_converted_to_legal_wait(self):
        policy = RegionalDirectorPolicy(transport=lambda payload: response(wait_action(), finish_reason='length'), interval=0)
        action = policy(self.observation)
        self.assertNotEqual(action, {})
        _, reward, _, _, info = self.env.step(action)
        self.assertIsNotNone(info['error'])
        self.assertEqual(self.env.counters['invalid_actions'], 1)
        self.assertLess(reward, 0)
        self.assertIsNotNone(policy.records[0]['protocol_error'])

    def test_protocol_parser_rejects_duplicate_fields_nonfinite_and_oversized_arguments(self):
        for arguments in ('{"requests":[],"requests":[]}', '{"explanation":NaN}', ' '*65537, '[]',
                          json.dumps(dict(wait_action(), requests=True))):
            with self.assertRaises(ValueError):
                parse_action(arguments)
        self.assertEqual(parse_action(json.dumps(wait_action())), wait_action())

    def test_schema_tracks_public_offers_and_releasable_resources(self):
        initial = action_schema(self.observation)
        self.assertEqual(initial['properties']['authorizations']['maxItems'], 0)
        observation, *_ = self.env.step(dict(requests=[dict(
            resource_id='engine-valverde', sector_id='report:report-0', mission='recon')]))
        schema = action_schema(observation)
        self.assertEqual(schema['properties']['authorizations']['items']['enum'], [observation['offers'][0]['id']])
        self.assertEqual(set(schema['required']), set(schema['properties']))

    def test_live_gate_and_request_budget_fail_without_credentials_or_retry(self):
        with patch('envs.wildfire_response.regional.director.policy.OpenRouterTransport') as live:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                RegionalDirectorPolicy()
            live.assert_not_called()
        for options in (dict(model='paid-model'), dict(max_calls=True), dict(interval=float('nan'))):
            with self.assertRaises(ValueError):
                RegionalDirectorPolicy(transport=ScriptedTransport(), **options)
        policy = RegionalDirectorPolicy(max_calls=1, transport=ScriptedTransport(), interval=0)
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            policy(self.observation, check_action=self.env.check_action)
        self.assertEqual(policy.calls, 1)
        self.assertEqual(self.env.sim.tick, 0)

    def test_auto_mode_and_prior_actions_preserve_public_feedback(self):
        requests = []
        def transport(payload):
            requests.append(payload)
            return response(wait_action())
        policy = RegionalDirectorPolicy(transport=transport, tool_mode='auto', interval=0)
        policy(self.observation)
        policy(self.observation, feedback=dict(error='Expired offer'))
        context = json.loads(requests[-1]['messages'][1]['content'])
        self.assertEqual(context['prior_actions'], [wait_action()])
        self.assertEqual(context['last_action_error'], 'Expired offer')
        self.assertEqual(context['episode_budget']['decisions_remaining'], 60)
        self.assertNotIn('tool_choice', requests[-1])
        self.assertNotIn('strict', requests[-1]['tools'][0]['function'])
        policy.reset()
        self.assertEqual(policy.calls, 0)
        self.assertEqual(policy.history, [])
        self.assertEqual(policy.records, [])
        self.assertIsNone(policy.last_call)

    def test_partial_provider_limit_is_recorded_and_not_scored_or_retried(self):
        requests = []
        def limited(payload):
            requests.append(payload)
            raise ProviderHTTPError(429)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'limited'
            with self.assertRaises(ProviderHTTPError):
                run(output, transport=limited, interval=0)
            status = load(output/'status.json')
            self.assertEqual(status['http_status'], 429)
            self.assertFalse(status['scored'])
            self.assertEqual(len(requests), 1)
            self.assertFalse((output/'summary.json').exists())
            self.assertTrue((output/'partial-trajectory.json.gz').is_file())

    def test_scripted_training_rollout_completes_verifies_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'scripted'
            summary = run(output, decisions=60, max_requests=120, transport=ScriptedTransport(), interval=0)
            self.assertEqual(summary['mode'], 'scripted_protocol_test')
            self.assertEqual(summary['served_models'], ['scripted-not-a-model'])
            self.assertTrue(summary['terminated'])
            self.assertFalse(summary['truncated'])
            self.assertEqual(summary['components']['invalid_actions'], 0)
            self.assertEqual(summary['total_reward'], summary['cooperative_reward'])
            self.assertEqual(summary['total_reward'], summary['replayed_reward'])
            self.assertTrue(summary['independent_verification'])
            self.assertEqual(summary['requests'], 2*summary['action_checks'])
            self.assertTrue((output/'private'/'evaluator.json.gz').is_file())
            with self.assertRaises(ValueError):
                run(Path(directory)/'heldout', scenario_id='regional-test-030', transport=ScriptedTransport())


if __name__ == '__main__':
    unittest.main()
