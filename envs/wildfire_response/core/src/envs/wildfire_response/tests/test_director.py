import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from envs.wildfire_response.baselines import passive
from envs.wildfire_response.core.environment import WildfireResponseEnv
from envs.wildfire_response.director.policy import DirectorPolicy, OpenRouterTransport, public_context
from envs.wildfire_response.director.run import run
from envs.wildfire_response.director.choices import build_choices, translate


def hold_proposal(choices):
    return dict(fleet_choices={identifier: 'hold' for identifier in choices['fleet']},
                report_dispositions=[], messages=[], explanation='Hold')


def response(action, finish_reason='tool_calls'):
    return dict(model='test-model', usage=dict(total_tokens=100, cost=0), choices=[dict(
        finish_reason=finish_reason, message=dict(tool_calls=[dict(type='function', function=dict(
            name='submit_action', arguments=json.dumps(action)))]))])


class DirectorTests(unittest.TestCase):
    def test_transport_deadline_stops_whitespace_keepalive(self):
        transport = OpenRouterTransport(api_key='test-only-not-a-key')
        response_context = MagicMock()
        with patch('envs.wildfire_response.director.policy.urlopen', return_value=response_context), \
             patch('envs.wildfire_response.director.policy.time.monotonic', side_effect=[0, 91]):
            with self.assertRaisesRegex(RuntimeError, 'transport failed'):
                transport(dict(model='test'))
        response_context.__enter__.return_value.read1.assert_not_called()

    def setUp(self):
        self.env = WildfireResponseEnv(max_decisions=1)
        self.observation, _ = self.env.reset('train-000')

    def test_public_context_budget_and_free_routing(self):
        observation = dict(self.observation, private_annotations=['not for model'], future_events=['secret'])
        requests = []
        def transport(payload):
            requests.append(payload)
            return response(hold_proposal(build_choices(self.observation)))
        policy = DirectorPolicy(max_calls=1, transport=transport, interval=0)
        action = policy(observation, feedback=dict(error='Previous target rejected'))
        self.env._validate_action(action)
        self.assertTrue(all(order['command'] == 'hold' for key in ('scout_orders', 'extinguisher_orders', 'truck_orders')
                            for order in action['fleet'][key]))
        serialized = json.dumps(requests)
        self.assertNotIn('private_annotations', serialized)
        self.assertNotIn('future_events', serialized)
        self.assertNotIn('secret', serialized)
        self.assertEqual(requests[0]['provider']['max_price'], dict(prompt=0, completion=0))
        self.assertFalse(requests[0]['provider']['allow_fallbacks'])
        function = requests[0]['tools'][0]['function']
        self.assertTrue(function['strict'])
        schema = function['parameters']
        self.assertEqual(set(schema['required']), set(schema['properties']))
        scout = schema['properties']['fleet_choices']['properties']['scout-1']
        self.assertIn('hold', scout['enum'])
        self.assertNotIn('containment_position_0', scout['enum'])
        self.assertEqual(json.loads(requests[0]['messages'][1]['content'])['last_action_error'], 'Previous target rejected')
        with self.assertRaises(RuntimeError):
            policy(observation)
        self.assertEqual(len(requests), 1)
        with self.assertRaises(ValueError):
            DirectorPolicy(model='paid-model', transport=transport)

    def test_truncated_or_malformed_output_is_not_silently_repaired(self):
        policy = DirectorPolicy(transport=lambda payload: response(passive(self.observation), 'length'), interval=0)
        self.assertEqual(policy(self.observation), {})
        _, reward, _, _, info = self.env.step({})
        self.assertIsNotNone(info['error'])
        self.assertLess(reward, 0)
        self.assertIsNotNone(policy.records[0]['protocol_error'])

    def test_one_decision_rollout_replays_and_saves_matched_baseline(self):
        def transport(payload):
            context = json.loads(payload['messages'][1]['content'])
            return response(hold_proposal(context['choices']))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'rollout'
            summary = run(output, decisions=1, transport=transport)
            self.assertEqual(summary['requests'], 1)
            self.assertEqual(summary['invalid_actions'], 0)
            self.assertEqual(summary['director_reward'], summary['director_replayed_reward'])
            self.assertTrue((output / 'deterministic.json.gz').is_file())
            self.assertFalse((output / 'private').exists())
            with self.assertRaises(ValueError):
                run(output, decisions=1, transport=transport)

    def test_compaction_retains_all_report_evidence_and_fire_times(self):
        context = public_context(self.observation)
        self.assertEqual(context['reports'], self.observation['reports'])
        self.assertTrue(all(cell['burning'] and 'observed_at' in cell for cell in context['sensor_memory']))

    def test_catalog_translation_preserves_choices_and_rejects_unknown_ids(self):
        choices = build_choices(self.observation)
        for identifier, unit in choices['fleet'].items():
            for option, order in unit['options'].items():
                proposal = hold_proposal(choices)
                proposal['fleet_choices'][identifier] = option
                action = translate(proposal, choices)
                self.assertIn(order, action['fleet'][unit['order_array']])
                self.env._validate_action(action)
        for identifier, message in choices['messages'].items():
            proposal = hold_proposal(choices)
            proposal['messages'] = [dict(choice_id=identifier, report_ids=[])]
            action = translate(proposal, choices)
            self.assertEqual(action['messages'][0], dict(message, report_ids=[]))
            self.env._validate_action(action)
        proposal = hold_proposal(choices)
        proposal['fleet_choices']['scout-1'] = 'containment_position_0'
        with self.assertRaises(ValueError):
            translate(proposal, choices)
        del proposal['fleet_choices']['scout-1']
        with self.assertRaises(ValueError):
            translate(proposal, choices)

    def test_unknown_model_choice_consumes_invalid_action_without_fallback(self):
        proposal = hold_proposal(build_choices(self.observation))
        proposal['fleet_choices']['scout-1'] = 'invented-target'
        policy = DirectorPolicy(transport=lambda payload: response(proposal), interval=0)
        self.assertEqual(policy(self.observation), {})
        self.assertEqual(policy.records[0]['proposal'], proposal)
        self.assertIsNotNone(policy.records[0]['protocol_error'])

    def test_auto_tool_mode_omits_unsupported_forcing_but_validates_choices(self):
        requests = []
        def transport(payload):
            requests.append(payload)
            return response(hold_proposal(build_choices(self.observation)))
        policy = DirectorPolicy(model='thinkingmachines/inkling:free', transport=transport,
                                tool_mode='auto', interval=0)
        self.env._validate_action(policy(self.observation))
        self.assertNotIn('tool_choice', requests[0])
        self.assertNotIn('strict', requests[0]['tools'][0]['function'])
        self.assertTrue(requests[0]['provider']['require_parameters'])
        self.assertEqual(policy.records[0]['tool_mode'], 'auto')

    def test_risk_aware_profile_preserves_tools_and_uses_no_private_information(self):
        requests = []
        def transport(payload):
            requests.append(payload)
            return response(hold_proposal(build_choices(self.observation)))
        policy = DirectorPolicy(transport=transport, profile='risk-aware', interval=0)
        self.env._validate_action(policy(self.observation))
        self.assertIn('Do not apply a blanket wait-for-confirmation rule', requests[0]['messages'][0]['content'])
        self.assertEqual(policy.records[0]['profile'], 'risk-aware')


if __name__ == '__main__':
    unittest.main()
