import copy
import json
from pathlib import Path
import tempfile
import unittest

from wildfire_core.ops_model import OpsModelPolicy, CONTRACT_VERSION, action_schema, parse_action, public_context
from envs.wildfire_response.regional.ops.model import LIMITS, ScriptedOpsTransport
from envs.wildfire_response.tests.test_prepared_development import create
from eval.run_ops_model import BoundedTransport, read_key, run
from envs.wildfire_response.director.policy import ProviderHTTPError


def empty_action():
    return dict({field: [] for field in LIMITS}, explanation='Wait explicitly')


def response(action, tool='submit_action'):
    return dict(model='test-not-inference', choices=[dict(finish_reason='tool_calls', message=dict(
        tool_calls=[dict(id='test-call', type='function', function=dict(name=tool, arguments=json.dumps(action)))]) )])


class OpsModelTests(unittest.TestCase):
    def setUp(self):
        self.env = create()
        self.observation, self.info = self.env.reset('prepared-dev-003')

    def test_current_public_protocol_and_reward_fields_are_visible_but_private_nested_data_is_not(self):
        observation = copy.deepcopy(self.observation)
        observation.update(future_events='PRIVATE-SENTINEL', evaluator_evidence='PRIVATE-SENTINEL')
        for field in ('reports', 'resources', 'sensor_memory', 'task_objectives'):
            observation[field][0]['private_label'] = 'PRIVATE-SENTINEL'
        observation['rules']['prescribed_strategy'] = 'PRIVATE-SENTINEL'
        observation['landscape']['future_checkpoint'] = 'PRIVATE-SENTINEL'
        observation['weather_context']['records'][0]['private_label'] = 'PRIVATE-SENTINEL'
        context = public_context(observation)
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(context))
        for field in ('readbacks', 'report_assessments', 'clarification_requests', 'protection_orders',
                      'latest_protection_orders', 'reward_weights', 'weather_context', 'terrain_at_sectors'):
            self.assertIn(field, context)
        self.assertEqual(context['reward_weights']['population_safety'], .8)
        self.assertEqual(context['sensor_memory'], self.observation['sensor_memory'])
        self.assertEqual(context['rules']['protection'], self.observation['rules']['protection'])

    def test_future_reports_and_sensor_samples_are_excluded(self):
        observation = copy.deepcopy(self.observation)
        observation['reports'].append(dict(observation['reports'][0], id='future', received_at=100))
        observation['sensor_memory'].append(dict(x=0, y=0, burning=True, observed_at=100))
        context = public_context(observation)
        self.assertNotIn('future', {report['id'] for report in context['reports']})
        self.assertTrue(all(cell['observed_at'] <= context['minute'] for cell in context['sensor_memory']))

    def test_schema_has_all_current_fields_and_dynamic_readback_offer_and_nullable_order_ids(self):
        schema = action_schema(public_context(self.observation))
        self.assertEqual(set(schema['required']), {*LIMITS, 'explanation'})
        self.assertEqual(schema['properties']['messages']['items']['properties']['purpose']['enum'], ['status', 'inform'])
        self.assertEqual(schema['properties']['clarifications']['maxItems'], 0)
        observation, *_ = self.env.request_resources([dict(resource_id=self.observation['resources'][0]['id'],
            sector_id=self.observation['sectors'][0]['id'], mission='recon')])
        schema = action_schema(public_context(observation))
        self.assertEqual(schema['properties']['authorizations']['items']['enum'], [observation['offers'][0]['id']])
        self.assertEqual(schema['properties']['readback_confirmations']['items']['enum'], [observation['readbacks'][0]['id']])
        self.assertEqual(schema['properties']['protection_orders']['items']['properties']['supersedes']['anyOf'][-1], {'type': 'null'})

    def test_parser_accepts_current_orders_and_rejects_legacy_messaging_and_structural_holes(self):
        action = empty_action()
        action['protection_orders'] = [dict(recipient='settlement-west', action='evacuate', channel='broadcast',
                                          resource_id=None, supersedes=None)]
        self.assertEqual(parse_action(json.dumps(action)), action)
        for broken in (
            dict(action, messages=[dict(recipient='settlement-west', purpose='evacuate')]),
            dict(action, protection_orders=[dict(recipient='settlement-west', action='evacuate')]),
            dict(action, clarifications=['a']*5), dict(action, triage=[dict(report_ids=[], assessment='defer')]),
            dict(action, unknown=[]), dict(action, authorizations=[False]),
        ):
            with self.assertRaises(ValueError):
                parse_action(json.dumps(broken))
        for raw in ('[]', '{"requests":[],"requests":[]}', '{"explanation":NaN}'):
            with self.assertRaises(ValueError):
                parse_action(raw)

    def test_checks_strip_only_unscored_explanation_and_submission_covers_readback_authorization(self):
        self.observation, _ = self.env.reset('prepared-dev-000')
        observation, *_ = self.env.request_resources([dict(resource_id=self.observation['resources'][0]['id'],
            sector_id=self.observation['sectors'][0]['id'], mission='recon')])
        action = empty_action()
        action['authorizations'] = [observation['offers'][0]['id']]
        action['readback_confirmations'] = [observation['readbacks'][0]['id']]
        replies = [response(action, 'check_action'), response(action)]
        actor = OpsModelPolicy(transport=lambda payload: replies.pop(0), interval=0)
        before = observation['minute']
        submitted = actor(observation, check_action=self.env.check_orders)
        self.assertNotIn('explanation', submitted)
        self.assertEqual(self.env.get_situation()['minute'], before)
        self.assertTrue(actor.records[0]['action_check']['valid'])
        observation, _, _, _, info = self.env.step(submitted)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['minute'], before+2)
        self.assertEqual(actor.records[-1]['contract_version'], CONTRACT_VERSION)

    def test_mismatching_readback_is_corrected_before_confirmation_and_authorization(self):
        observation, *_ = self.env.request_resources([dict(resource_id=self.observation['resources'][0]['id'],
            sector_id=self.observation['sectors'][0]['id'], mission='recon')])
        offer, readback = observation['offers'][0], observation['readbacks'][0]
        self.assertNotEqual(offer['mission'], readback['mission'])
        correction = empty_action()
        correction['readback_corrections'] = [dict(readback_id=readback['id'], **{
            field: offer[field] for field in ('resource_id', 'sector_id', 'mission')})]
        replies = [response(correction, 'check_action'), response(correction)]
        actor = OpsModelPolicy(transport=lambda payload: replies.pop(0), interval=0)
        action = actor(observation, check_action=self.env.check_orders)
        self.assertTrue(actor.records[0]['action_check']['valid'])
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        matching = next(record for record in observation['readbacks'] if record['status']=='awaiting_confirmation')
        self.assertEqual(matching['mission'], offer['mission'])
        confirmed = empty_action()
        confirmed['readback_confirmations'] = [matching['id']]
        confirmed['authorizations'] = [offer['id']]
        actor = OpsModelPolicy(transport=lambda payload: response(confirmed), interval=0)
        _, _, _, _, info = self.env.step(actor(observation))
        self.assertIsNone(info['error'])

    def test_scripted_full_episode_roundtrips_the_actual_new_tool_contract_and_verifier(self):
        observation, info = self.env.reset('prepared-dev-001')
        actor = OpsModelPolicy(transport=ScriptedOpsTransport(), interval=0, max_calls=120)
        while True:
            action = actor(observation, feedback=info, check_action=self.env.check_orders)
            observation, _, terminated, truncated, info = self.env.step(action)
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        self.assertTrue(self.env.verify_episode()['passed'])
        self.assertTrue(any(record.get('tool') == 'check_action' for record in actor.records))
        self.assertTrue(all(record['protocol_error'] is None for record in actor.records))

    def test_malformed_outputs_are_penalized_not_replaced_by_wait(self):
        actor = OpsModelPolicy(transport=lambda payload: response({}), interval=0)
        action = actor(self.observation)
        self.assertIn('invalid_model_action', action)
        _, _, _, _, info = self.env.step(action)
        self.assertIsNotNone(info['error'])

    def test_key_loader_reads_only_the_requested_assignment_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('OTHER_SECRET=never-read\nexport OR_API_KEY="fake-test-value" # note\n')
            self.assertEqual(read_key(path), 'fake-test-value')
            path.write_text('OR_API_KEY=a\nOR_API_KEY=b\n')
            with self.assertRaises(ValueError):
                read_key(path)

    def test_global_transport_budget_and_nonzero_cost_stop_without_retry(self):
        calls = []
        def transport(payload):
            calls.append(payload)
            return dict(usage={'cost': 0})
        bounded = BoundedTransport(transport, max_requests=1, interval=0)
        bounded({})
        with self.assertRaises(RuntimeError):
            bounded({})
        self.assertEqual(len(calls), 1)
        paid = BoundedTransport(lambda payload: dict(usage={'cost': .01}), interval=0)
        with self.assertRaises(RuntimeError):
            paid({})
        limited = BoundedTransport(lambda payload: dict(error={'code':429}), interval=0)
        with self.assertRaises(ProviderHTTPError):
            limited({})
        self.assertEqual(limited.calls, 1)

    def test_quota_failure_stops_the_suite_and_saves_an_unscored_partial(self):
        def limited(payload):
            raise ProviderHTTPError(429)
        with tempfile.TemporaryDirectory() as directory:
            def factory():
                env = create()
                original = env.reset
                env.reset = lambda task_id: original(task_id.replace('topo-', 'prepared-'))
                return env
            summary = run(Path(directory)/'trial', factory, limited, model='test:free', interval=0)
            self.assertEqual(summary['status'], 'stopped')
            self.assertEqual(summary['requests'], 1)
            self.assertEqual(len(summary['episodes']), 1)
            self.assertFalse(summary['episodes'][0]['scored'])
            self.assertEqual(summary['stop_reason']['http_status'], 429)
            self.assertTrue((Path(directory)/'trial'/'topo-dev-001'/'partial-trajectory.json').exists())


if __name__ == '__main__':
    unittest.main()
