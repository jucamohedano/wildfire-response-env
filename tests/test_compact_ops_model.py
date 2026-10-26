import copy
import json
import unittest

from wildfire_core.compact_ops_model import (
    CompactOpsModelPolicy, CONTRACT_VERSION, action_schema, parse_action, public_context,
)
from wildfire_core.ops_model import (
    OpsModelPolicy, action_schema as v1_schema, parse_action as v1_parse_action,
    public_context as v1_context,
)
from envs.wildfire_response.regional.ops.compact_model import EVENT_FIELDS, FOCUSED_FIELDS
from envs.wildfire_response.regional.ops.model import LIMITS
from envs.wildfire_response.tests.test_prepared_development import create


def response(action, tool='submit_action', raw=False):
    return dict(model='test-not-inference', choices=[dict(finish_reason='tool_calls', message=dict(
        tool_calls=[dict(id='test-call', type='function', function=dict(
            name=tool, arguments=action if raw else json.dumps(action)))]) )])


class CompactOpsModelTests(unittest.TestCase):
    def setUp(self):
        self.env = create()
        self.observation, _ = self.env.reset('prepared-dev-000')

    def actor(self, action, tool='submit_action'):
        return CompactOpsModelPolicy(transport=lambda payload: response(action, tool), interval=0)

    def request(self):
        return dict(resource_id=self.observation['resources'][0]['id'],
                    sector_id=self.observation['sectors'][0]['id'], mission='recon')

    def test_focused_request_then_confirm_and_authorize_uses_real_checks(self):
        actor = self.actor({'requests': [self.request()]}, 'request_resources')
        requested = actor(self.observation, check_action=self.env.check_orders)
        observation, _, _, _, info = self.env.step(requested)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['minute'], 2)
        self.assertEqual(len(actor.history), 1)
        offer, readback = observation['offers'][0], observation['readbacks'][0]
        authorization = dict(authorizations=[offer['id']], readback_confirmations=[readback['id']])
        replies = [response(authorization, 'check_action'), response(authorization, 'authorize_dispatch')]
        payloads = []

        def transport(payload):
            payloads.append(payload)
            return replies.pop(0)

        actor = CompactOpsModelPolicy(transport=transport, interval=0)
        action = actor(observation, check_action=self.env.check_orders)
        self.assertEqual(self.env.get_situation()['minute'], 2)
        self.assertTrue(actor.records[0]['action_check']['valid'])
        self.assertNotIn('explanation', action)
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['minute'], 4)
        self.assertEqual(observation['offers'][0]['status'], 'committed')
        self.assertEqual(observation['readbacks'][0]['status'], 'confirmed')
        self.assertEqual(actor.records[-1]['contract_version'], CONTRACT_VERSION)
        self.assertEqual(actor.records[-1]['tool'], 'authorize_dispatch')
        schema = next(tool['function']['parameters'] for tool in payloads[0]['tools']
                      if tool['function']['name'] == 'authorize_dispatch')
        self.assertEqual(schema['properties']['authorizations']['items']['enum'], [offer['id']])
        self.assertEqual(schema['required'], ['authorizations'])

    def test_authorization_without_confirmation_remains_invalid_and_atomic(self):
        observation, *_ = self.env.request_resources([self.request()])
        original = copy.deepcopy(observation['resources'])
        action = self.actor({'authorizations': [observation['offers'][0]['id']]},
                            'authorize_dispatch')(observation)
        self.assertFalse(self.env.check_orders(action)['valid'])
        observation, _, _, _, info = self.env.step(action)
        self.assertIn('confirmed readback', info['error'])
        self.assertEqual(observation['resources'], original)
        self.assertEqual(observation['minute'], 4)

    def test_mismatching_readback_requires_focused_correction_before_dispatch(self):
        self.observation, _ = self.env.reset('prepared-dev-003')
        observation, *_ = self.env.request_resources([self.request()])
        offer, readback = observation['offers'][0], observation['readbacks'][0]
        authorization = dict(authorizations=[offer['id']], readback_confirmations=[readback['id']])
        invalid = self.actor(authorization, 'authorize_dispatch')(observation)
        self.assertFalse(self.env.check_orders(invalid)['valid'])
        correction = dict(readback_id=readback['id'], **{
            field: offer[field] for field in ('resource_id', 'sector_id', 'mission')})
        action = self.actor({'readback_corrections': [correction]}, 'correct_readbacks')(observation)
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        matching = next(record for record in observation['readbacks'] if record['status'] == 'awaiting_confirmation')
        authorization['readback_confirmations'] = [matching['id']]
        action = self.actor(authorization, 'authorize_dispatch')(observation)
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['offers'][0]['status'], 'committed')

    def test_focused_protection_executes_and_preserves_supersession_validation(self):
        order = dict(recipient='settlement-west', action='evacuate', channel='broadcast',
                     resource_id=None, supersedes=None)
        actor = self.actor({'protection_orders': [order]}, 'protect_population')
        action = actor(self.observation)
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        self.assertEqual(len(observation['protection_orders']), 1)
        recorded = observation['protection_orders'][0]
        self.assertEqual(recorded['recipient'], order['recipient'])
        self.assertEqual(recorded['action'], 'evacuate')
        stale = self.actor({'protection_orders': [order]}, 'protect_population')(observation)
        self.assertFalse(self.env.check_orders(stale)['valid'])
        for _ in range(3):
            observation, _, _, _, info = self.env.step(self.actor({}, 'advance_time')(observation))
            self.assertIsNone(info['error'])
        self.assertIsNotNone(observation['protection_orders'][0].get('executed_at'))

    def test_sparse_wait_and_explicit_advance_commit_once(self):
        for tool in ('submit_action', 'advance_time'):
            with self.subTest(tool=tool):
                observation, _ = self.env.reset('prepared-dev-000')
                actor = self.actor({}, tool)
                action = actor(observation)
                self.assertEqual(action, {field: [] for field in LIMITS})
                self.assertEqual(self.env.get_situation()['minute'], 0)
                observation, _, _, _, info = self.env.step(action)
                self.assertIsNone(info['error'])
                self.assertEqual(observation['minute'], 2)
                self.assertEqual(actor.calls, 1)
                self.assertEqual(len(actor.history), 1)

    def test_three_sparse_draft_checks_do_not_advance_time(self):
        payloads = []

        def transport(payload):
            payloads.append(payload)
            return response({}, 'check_action' if len(payloads) <= 3 else 'advance_time')

        actor = CompactOpsModelPolicy(transport=transport, interval=0, max_calls=4)
        action = actor(self.observation, check_action=self.env.check_orders)
        self.assertEqual(self.env.get_situation()['minute'], 0)
        self.assertEqual([record['action_check']['remaining_checks'] for record in actor.records[:3]], [2, 1, 0])
        tools = {tool['function']['name'] for tool in payloads[-1]['tools']}
        self.assertNotIn('check_action', tools)
        self.assertEqual(tools, {'submit_action', 'advance_time', *FOCUSED_FIELDS})
        observation, _, _, _, info = self.env.step(action)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['minute'], 2)

    def test_fourth_check_and_unavailable_tools_preserve_selection_diagnostics(self):
        actor = self.actor({}, 'check_action')
        action = actor(self.observation, check_action=self.env.check_orders)
        self.assertEqual(action, {'invalid_model_action': 'missing_or_invalid_named_action'})
        self.assertEqual(actor.calls, 4)
        self.assertEqual(actor.records[-1]['response_diagnostics']['stage'], 'tool_selection')
        self.assertEqual(self.env.get_situation()['minute'], 0)
        for tool in ('check_action', 'unknown'):
            actor = self.actor({}, tool)
            self.assertIn('invalid_model_action', actor(self.observation))
            self.assertEqual(actor.records[0]['response_diagnostics']['stage'], 'tool_selection')

    def test_malformed_json_and_unknown_fields_are_not_repaired(self):
        for raw in ('{"requests":', '{"requests": [],}', '{"unknown": []}',
                    '{"requests":[],"requests":[]}',
                    '{"requests":[{"resource_id":"one","resource_id":"two"}]}',
                    '{"explanation":NaN}', '{"explanation":Infinity}',
                    '{"explanation":1e999}', '[]', 'null', ' ' * 65537):
            with self.subTest(raw=raw[:70]):
                reply = response(raw, raw=True)
                actor = CompactOpsModelPolicy(transport=lambda payload: reply, interval=0)
                action = actor(self.observation)
                self.assertIn('invalid_model_action', action)
                self.assertEqual(actor.history, [])
                diagnostic = actor.records[0]['response_diagnostics']
                self.assertEqual(diagnostic['stage'], 'action_arguments')
                if len(raw) < 1000:
                    self.assertEqual(diagnostic['choices'][0]['message']['tool_calls'][0]['function']['arguments'], raw)
                self.assertEqual(self.env.get_situation()['minute'], 0)

    def test_invalid_commits_still_advance_and_truncate(self):
        actor = self.actor({'unknown': []})
        observation = self.observation
        for decision in range(1, 4):
            observation, _, _, truncated, info = self.env.step(actor(observation))
            self.assertIsNotNone(info['error'])
            self.assertEqual(observation['minute'], decision * 2)
            self.assertEqual(truncated, decision == 3)

    def test_sparse_contract_retains_item_types_required_fields_and_bounds(self):
        invalid = [
            dict(requests=None), dict(requests={}), dict(requests=[True]),
            dict(requests=[dict(resource_id='crew', sector_id='sector')]),
            dict(requests=[dict(self.request(), unknown=True)]),
            dict(authorizations=[False]), dict(releases=['']),
            dict(readback_confirmations=['id' * 128]), dict(explanation=1),
            dict(explanation='x' * 1001),
            dict(messages=[dict(recipient='settlement-west', purpose='evacuate')]),
            dict(triage=[dict(report_ids=[], assessment='defer')]),
            dict(triage=[dict(report_ids=['claim-0'], assessment='invented')]),
            dict(protection_orders=[dict(recipient='settlement-west', action='shelter')]),
        ]
        invalid.extend({field: [None] * (limit + 1)} for field, limit in LIMITS.items())
        for action in invalid:
            with self.subTest(action=action), self.assertRaises(ValueError):
                parse_action(json.dumps(action))
        with self.assertRaises(ValueError):
            parse_action({})
        with self.assertRaises(ValueError):
            parse_action(' ' * 65536 + '{}')

    def test_focused_tools_only_accept_their_fields_and_keep_full_item_schemas(self):
        context = public_context(self.observation)
        full_schema = v1_schema(context)
        for name, fields in FOCUSED_FIELDS.items():
            with self.subTest(tool=name):
                schema = action_schema(context, name)
                self.assertEqual(set(schema['properties']), {*fields, 'explanation'})
                self.assertEqual(schema['required'], [fields[0]])
                self.assertFalse(schema['additionalProperties'])
                for field in fields:
                    self.assertEqual(schema['properties'][field], full_schema['properties'][field])
                supplied = {fields[0]: []}
                self.assertEqual(parse_action(json.dumps(supplied), name),
                                 dict({field: [] for field in LIMITS}, explanation=''))
                for invalid in ({}, dict(supplied, unknown=[]), {fields[0]: False}):
                    with self.assertRaises(ValueError):
                        parse_action(json.dumps(invalid), name)
                other = next(field for field in LIMITS if field not in fields)
                with self.assertRaises(ValueError):
                    parse_action(json.dumps(dict(supplied, **{other: []})), name)
        self.assertEqual(action_schema(context, 'advance_time'),
                         dict(type='object', properties={}, required=[], additionalProperties=False))
        for invalid in ({'explanation': ''}, {'requests': []}, {'minutes': 2}):
            with self.assertRaises(ValueError):
                parse_action(json.dumps(invalid), 'advance_time')

    def test_sparse_parser_is_backward_compatible_but_v1_remains_strict(self):
        full = dict({field: [] for field in LIMITS}, explanation='Wait explicitly')
        self.assertEqual(parse_action(json.dumps(full)), v1_parse_action(json.dumps(full)))
        self.assertEqual(action_schema(public_context(self.observation))['required'], [])
        self.assertEqual(set(v1_schema(v1_context(self.observation))['required']), {*LIMITS, 'explanation'})
        with self.assertRaises(ValueError):
            v1_parse_action('{}')
        for tool in ('submit_action', 'advance_time', 'request_resources'):
            actor = OpsModelPolicy(transport=lambda payload: response({}, tool), interval=0)
            self.assertIn('invalid_model_action', actor(self.observation))
        actor = OpsModelPolicy(transport=lambda payload: response(full), interval=0)
        self.assertEqual(actor(self.observation), {field: [] for field in LIMITS})

    def test_default_forced_tools_keep_v1_strict_and_compact_optional_fields_locally_validated(self):
        for policy in (OpsModelPolicy, CompactOpsModelPolicy):
            with self.subTest(policy=policy.__name__):
                payloads = []

                def transport(payload):
                    payloads.append(payload)
                    return response({'unknown': []})

                actor = policy(transport=transport, interval=0)
                self.assertIn('invalid_model_action', actor(self.observation, check_action=self.env.check_orders))
                self.assertEqual(actor.records[0]['response_diagnostics']['stage'], 'action_arguments')
                self.assertEqual(payloads[0]['tool_choice'], 'required')
                self.assertNotIn('parallel_tool_calls', payloads[0])
                self.assertNotIn('reasoning', payloads[0])
                for tool in payloads[0]['tools']:
                    function = tool['function']
                    schema = function['parameters']
                    optional = set(schema['properties']) - set(schema['required'])
                    if policy is CompactOpsModelPolicy and optional:
                        self.assertNotIn('strict', function)
                    else:
                        self.assertTrue(function['strict'])
                        self.assertFalse(optional)

    def test_public_projection_filters_event_fields_and_future_receipts(self):
        observation = copy.deepcopy(self.observation)
        event = dict(id='event-1', kind='resource_update', source='crew', source_role='responder',
                     channel='radio', location=[1, 2], observed_at=0, received_at=0,
                     text='Arrival reported; claim only', resource_id='crew-1', district_id=None,
                     related_event_id=None, incident_id='support:crew-1', status='open', priority='urgent')
        clock = dict(minute=0, horizon_minutes=120, decision_minutes=2,
                     unit='simulation_minutes', basis='virtual model minutes')
        observation.update(clock=dict(clock, private_label={'future': 'PRIVATE-SENTINEL'}),
                           incident_event_version='test-v1', event_cursor=1,
                           incident_events=[dict(event, private_label='PRIVATE-SENTINEL'),
                                            dict(event, id='future-receipt', received_at=1),
                                            dict(event, id='future-observation', observed_at=1)],
                           future_events='PRIVATE-SENTINEL')
        for field in ('observed_at', 'received_at'):
            observation['incident_events'].extend(dict(event, **{field: invalid})
                                                  for invalid in (None, True, '0', float('nan'), float('inf')))
        observation['incident_events'].append({'id': 'missing-receipt'})
        observation['incident_events'].append(dict(id='missing-observation', received_at=0))
        arrival = dict(arrival_at=8, arrival_in_minutes=8, travel_time_basis='synthetic fixture')
        observation['resources'][0].update(arrival, private_label='PRIVATE-SENTINEL')
        context = public_context(observation)
        self.assertEqual(context['incident_events'], [event])
        self.assertEqual(set(context['incident_events'][0]), set(EVENT_FIELDS))
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(context, allow_nan=False))
        self.assertEqual(context['clock'], clock)
        for field in ('incident_event_version', 'event_cursor'):
            self.assertEqual(context[field], observation[field])
        for field, value in arrival.items():
            self.assertEqual(context['resources'][0][field], value)
        self.assertEqual(context['sensor_memory'], observation['sensor_memory'])
        context['incident_events'][0]['location'][0] = 99
        context['clock']['minute'] = 99
        self.assertEqual(observation['incident_events'][0]['location'], [1, 2])
        self.assertEqual(observation['clock']['minute'], 0)
        self.assertEqual(public_context(self.observation), v1_context(self.observation))

    def test_events_observed_after_receipt_are_excluded_even_when_both_times_are_past(self):
        observation = copy.deepcopy(self.observation)
        observation['minute'] = 4
        valid = dict(id='incident-event-1', incident_id='support:crew-1', observed_at=1,
                     received_at=2, location=[1, 2], status='open', priority='normal')
        invalid = dict(valid, id='incident-event-2', observed_at=3, received_at=2,
                       status='resolved', text='PRIVATE-SENTINEL')
        observation['incident_events'] = [valid, invalid]
        observation['active_incidents'] = [valid, invalid]
        observation['sectors'].append(dict(id='event:' + invalid['id'], center=invalid['location']))
        context = public_context(observation)
        self.assertEqual(context['incident_events'], [valid])
        self.assertEqual(context['active_incidents'], [valid])
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(context))
        self.assertNotIn('event:incident-event-2', {sector['id'] for sector in context['sectors']})
        schema = action_schema(context, 'request_resources')
        self.assertNotIn('event:incident-event-2',
                         schema['properties']['requests']['items']['properties']['sector_id']['enum'])

    def test_active_incidents_use_delivered_records_and_latest_resolution(self):
        observation = copy.deepcopy(self.observation)
        opened = dict(id='incident-event-1', incident_id='support:crew-1', kind='crew_support_request',
                      observed_at=0, received_at=0, location=[1, 2], status='open', priority='urgent',
                      text='Need support')
        resolved = dict(opened, id='incident-event-2', kind='crew_support_resolved', status='resolved',
                        observed_at=0, received_at=1, related_event_id=opened['id'], text='Deficit cleared')
        observation['incident_events'] = [opened, resolved]
        observation['active_incidents'] = [dict(opened, text='PRIVATE-SENTINEL'), resolved,
                                          dict(opened, id='fabricated', incident_id='fabricated')]
        context = public_context(observation)
        self.assertEqual(context['incident_events'], [opened])
        self.assertEqual(context['active_incidents'], [opened])
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(context))
        context['active_incidents'][0]['location'][0] = 99
        self.assertEqual(context['incident_events'][0]['location'], [1, 2])
        self.assertEqual(opened['location'], [1, 2])
        observation['minute'] = 1
        context = public_context(observation)
        self.assertEqual(context['incident_events'], [opened, resolved])
        self.assertEqual(context['active_incidents'], [])
        reopened = dict(opened, id='incident-event-3', observed_at=1, received_at=1,
                        related_event_id=resolved['id'])
        observation['incident_events'].append(reopened)
        observation['active_incidents'].append(reopened)
        self.assertEqual(public_context(observation)['active_incidents'], [reopened])
        del observation['incident_events']
        self.assertEqual(public_context(observation)['active_incidents'], [])

    def test_event_recon_sectors_only_expose_eligible_delivered_locations(self):
        observation = copy.deepcopy(self.observation)
        delivered = dict(id='incident-event-1', incident_id='smoke:west', observed_at=0,
                         received_at=0, location=[1, 2], status='open', priority='normal')
        future_observation = dict(delivered, id='incident-event-2', observed_at=1, location=[2, 2])
        future_receipt = dict(delivered, id='incident-event-3', received_at=1, location=[3, 2])
        fabricated = dict(delivered, id='incident-event-4', location=[4, 2])
        observation['incident_events'] = [delivered, future_observation, future_receipt]
        observation['active_incidents'] = [delivered, future_observation, future_receipt, fabricated]
        observation['sectors'].extend(dict(id='event:' + event['id'], center=event['location'])
                                      for event in (delivered, future_observation, future_receipt, fabricated))
        context = public_context(observation)
        self.assertEqual(context['active_incidents'], [delivered])
        self.assertEqual([sector['id'] for sector in context['sectors'] if sector['id'].startswith('event:')],
                         ['event:incident-event-1'])
        self.assertEqual([sample['sector_id'] for sample in context['terrain_at_sectors']
                          if sample['sector_id'].startswith('event:')], ['event:incident-event-1'])
        schema = action_schema(context, 'request_resources')
        sectors = schema['properties']['requests']['items']['properties']['sector_id']['enum']
        self.assertIn('event:incident-event-1', sectors)
        for event in (future_observation, future_receipt, fabricated):
            self.assertNotIn('event:' + event['id'], sectors)
        observation['sectors'][-4]['center'] = [5, 2]
        context = public_context(observation)
        self.assertFalse(any(sector['id'].startswith('event:') for sector in context['sectors']))

    def test_operational_events_cannot_supply_triage_sensor_evidence(self):
        self.observation, _ = self.env.reset('prepared-dev-001')
        report = self.observation['reports'][0]
        self.observation['incident_events'] = [dict(id='event-1', kind='fire_report',
            source='crew', source_role='responder', channel='radio', location=report['location'],
            observed_at=0, received_at=0, text='Fire is confirmed')]
        action = self.actor({'triage': [dict(report_ids=[report['id']], assessment='fire_observed')]},
                            'assess_reports')(self.observation)
        self.assertFalse(self.env.check_orders(action)['valid'])
        _, _, _, _, info = self.env.step(action)
        self.assertIsNotNone(info['error'])


if __name__ == '__main__':
    unittest.main()
