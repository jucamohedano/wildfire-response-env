import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from wildfire_core.incident import ReactiveOpsEnv, verify_reactive_trajectory
from envs.wildfire_response.regional.incident.events import IncidentEvents, RULES, covered_cells
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain


def create():
    prepared = fixture_inputs()
    domain = fixture_domain(prepared)
    return ReactiveOpsEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], slope_enabled=True, allow_synthetic_conditions=True)


class IncidentTests(unittest.TestCase):
    def test_compact_runner_records_new_contract_and_only_selected_case(self):
        from eval.run_ops_model import run
        from wildfire_core.compact_ops_model import CompactOpsModelPolicy
        def transport(payload):
            return dict(model='scripted-not-inference', usage={}, choices=[dict(finish_reason='tool_calls',
                message=dict(tool_calls=[dict(id='wait', type='function',
                    function=dict(name='advance_time', arguments='{}'))]))])
        with tempfile.TemporaryDirectory() as directory:
            result = run(Path(directory)/'trial', create, transport, model='test:free',
                         policy_type=CompactOpsModelPolicy, case_ids=('incident-dev-001',),
                         max_requests=16, interval=0)
            self.assertEqual(result['contract_version'], 'wildfire-ops-model-tools-v2')
            self.assertEqual(result['cases_planned'], ['incident-dev-001'])
            self.assertTrue(result['episodes'][0]['scored'])
            self.assertEqual(result['episodes'][0]['invalid_actions'], 0)

    def test_delivery_clock_privacy_and_read_only_inbox(self):
        env = create()
        initial, _ = env.reset()
        self.assertEqual(initial['clock']['horizon_minutes'], 120)
        self.assertEqual(initial['incident_events'], [])
        before_delivery = initial
        self.assertEqual(before_delivery['incident_events'], [])
        self.assertFalse(any(item['kind'] == 'incident_update' for item in before_delivery['pending_deliveries']))
        self.assertFalse(any(sector['id'].startswith('event:') for sector in before_delivery['sectors']))
        delivered, *_ = env.step({})
        event = delivered['incident_events'][0]
        self.assertEqual((event['observed_at'], event['received_at']), (0, 2))
        self.assertIn('event:' + event['id'], {sector['id'] for sector in delivered['sectors']})
        self.assertEqual(env.get_situation(), env.get_situation())
        env.check_orders({})
        self.assertEqual(env.get_situation()['minute'], 2)
        self.assertEqual(env.get_situation()['incident_events'], delivered['incident_events'])
        for event in delivered['incident_events']:
            self.assertLessEqual(event['observed_at'], event['received_at'])
            self.assertLessEqual(event['received_at'], delivered['minute'])

    def test_false_alarm_does_not_create_smoke_calls_and_reset_clears_events(self):
        env = create()
        env.reset()
        env.step({})
        env.step({})
        self.assertTrue(env.get_situation()['incident_events'])
        env.reset('incident-dev-001')
        for decision in range(10):
            observation, *_ = env.step({})
            self.assertEqual(observation['incident_events'], [])
        fresh, _ = create().reset('incident-dev-001')
        reset, _ = env.reset('incident-dev-001')
        self.assertEqual(fresh, reset)

    def test_executed_protection_changes_followups_without_rewarding_messages(self):
        waiting, protected = create(), create()
        waiting.reset()
        observation, _ = protected.reset()
        district = observation['districts'][0]['id']
        order = dict(protection_orders=[dict(recipient=district, action='shelter', channel='broadcast',
                                           resource_id=None, supersedes=None)])
        for index in range(5):
            waiting_obs, *_ = waiting.step({})
            protected_obs, reward, _, _, info = protected.step(order if index == 0 else {})
            self.assertIsNone(info['error'])
            self.assertEqual(reward, 0)
        self.assertTrue(any(event['kind'] == 'resident_protection_update' for event in protected_obs['incident_events']))
        self.assertFalse(any(event['incident_id'] == 'smoke:' + district for event in protected_obs['active_incidents']))
        self.assertTrue(any(event['incident_id'] == 'smoke:' + district for event in waiting_obs['active_incidents']))
        self.assertLess(sum(event['kind'] == 'resident_smoke_call' for event in protected_obs['incident_events']),
                        sum(event['kind'] == 'resident_smoke_call' for event in waiting_obs['incident_events']))

    def test_arrival_eta_matches_actual_resource_arrival_and_no_instant_dispatch(self):
        env = create()
        initial, _ = env.reset('incident-dev-001')
        resource = initial['resources'][0]['id']
        observation, *_ = env.step({'requests': [dict(resource_id=resource, sector_id='report:claim-0', mission='recon')]})
        self.assertEqual(next(unit for unit in observation['resources'] if unit['id'] == resource)['state'], 'reserved')
        observation, _, _, _, info = env.step(dict(authorizations=[observation['offers'][0]['id']],
            readback_confirmations=[observation['readbacks'][0]['id']]))
        self.assertIsNone(info['error'])
        unit = next(unit for unit in observation['resources'] if unit['id'] == resource)
        expected = unit['arrival_at']
        self.assertGreater(expected, observation['minute'])
        while observation['minute'] < expected:
            observation, *_ = env.step({})
            unit = next(unit for unit in observation['resources'] if unit['id'] == resource)
            if observation['minute'] < expected:
                self.assertNotEqual(unit['state'], 'on_scene')
                self.assertEqual(unit['arrival_at'], expected)
        arrived = next(event for event in observation['acknowledgements']
                       if event['kind'] == 'resource_arrived' and event['resource_id'] == resource)
        self.assertEqual(arrived['minute'], expected)
        self.assertEqual(unit['arrival_in_minutes'], 0)

    def test_shortage_requires_on_scene_capacity_and_resolution_is_delayed(self):
        scene = dict(id='observer', kind='crew', state='on_scene', mission='recon', target=[5, 5], water_litres=0)
        backup = dict(id='backup', kind='engine', state='en_route', mission='suppress', target=[5, 5], water_litres=100)
        core = SimpleNamespace(sim=SimpleNamespace(tick=1, groups={}, population_state=lambda: {}),
            resources={'observer': scene, 'backup': backup}, queue=[], task={'protection': {}},
            memory={(5, 5): dict(x=5, y=5, burning=True, observed_at=1)})
        events = IncidentEvents(12, 120)
        events.detect(core)
        self.assertEqual(events.public_state()['incident_events'], [])
        core.sim.tick = 2
        events.deliver(core)
        request = next(event for event in events.delivered if event['kind'] == 'crew_support_request')
        self.assertIn('capacity is 0', request['text'])
        backup['state'] = 'on_scene'
        core.memory[(5, 5)]['observed_at'] = 2
        events.detect(core)
        self.assertTrue(any(event['incident_id'] == 'support:observer' for event in events.public_state()['active_incidents']))
        core.sim.tick = 3
        events.deliver(core)
        self.assertFalse(any(event['incident_id'] == 'support:observer' for event in events.public_state()['active_incidents']))

    def test_latched_conditions_cooldown_budget_and_horizon_bound_messages(self):
        core = SimpleNamespace(sim=SimpleNamespace(tick=1), queue=[])
        events = IncidentEvents(7, 120)
        arguments = dict(kind='test', source='resident', role='resident', channel='synthetic_phone',
                         location=[1, 1], text='Smoke reported', repeat=True)
        for minute in range(1, 9):
            core.sim.tick = minute
            events._emit(core, 'smoke:west', 'smoke', **arguments)
        self.assertEqual(len(core.queue), 1)
        core.sim.tick = 9
        events._emit(core, 'smoke:west', 'smoke', **arguments)
        self.assertEqual(len(core.queue), 2)
        for index in range(100):
            events._emit(core, 'distinct:' + str(index), index, status='resolved', **arguments)
        self.assertLess(events.created, RULES['max_events'])
        events._emit(core, 'smoke:west', 'worse', priority='urgent', **arguments)
        events._emit(core, 'smoke:west', 'clear', status='resolved', **arguments)
        self.assertEqual(events.created, RULES['max_events'])
        self.assertEqual(core.queue[-1]['data']['status'], 'resolved')
        events = IncidentEvents(7, 120)
        core.queue.clear()
        core.sim.tick = 119
        events._emit(core, 'late', 'smoke', **arguments)
        self.assertEqual(core.queue, [])

    def test_warning_duty_and_nonoverlapping_ranges_do_not_count_as_capacity(self):
        unit = dict(id='crew', kind='crew', state='on_scene', mission='suppress', target=[5, 5], water_litres=0)
        core = SimpleNamespace(sim=SimpleNamespace(tick=3), resources={'crew': unit},
                               protection=SimpleNamespace(warning_busy={'crew': 5}))
        cells = [dict(x=5, y=5)]
        self.assertEqual(covered_cells(core, cells), 0)
        core.sim.tick = 5
        self.assertEqual(covered_cells(core, cells), 1)
        core.resources['engine'] = dict(id='engine', kind='engine', state='on_scene', mission='suppress',
                                        target=[8, 5], water_litres=100)
        self.assertEqual(covered_cells(core, [dict(x=2, y=5), dict(x=3, y=5)]), 1)
        unit['mission'] = 'recon'
        self.assertEqual(covered_cells(core, [dict(x=2, y=5), dict(x=3, y=5)]), 0)

    def test_departure_withdraws_request_without_claiming_fire_resolution(self):
        unit = dict(id='crew', kind='crew', state='on_scene', mission='recon', target=[5, 5], water_litres=0)
        core = SimpleNamespace(sim=SimpleNamespace(tick=1, groups={}, population_state=lambda: {}),
            resources={'crew': unit}, queue=[], task={'protection': {}},
            memory={(5, 5): dict(x=5, y=5, burning=True, observed_at=1)})
        events = IncidentEvents(12, 120)
        events.detect(core)
        core.sim.tick = 2
        events.deliver(core)
        self.assertTrue(events.public_state()['active_incidents'])
        unit['state'] = 'returning'
        events.detect(core)
        core.sim.tick = 3
        events.deliver(core)
        self.assertEqual(events.public_state()['active_incidents'], [])
        self.assertEqual(events.delivered[-1]['kind'], 'crew_support_withdrawn')
        self.assertIn('reassess', events.delivered[-1]['text'])

    def test_complete_episode_replay_and_tamper_rejection(self):
        first, second = create(), create()
        first.reset()
        second.reset()
        while True:
            actual = first.step({})
            expected = second.step({})
            self.assertEqual(actual, expected)
            if actual[2] or actual[3]:
                break
        trace = first.trajectory()
        self.assertEqual(trace, second.trajectory())
        verified = verify_reactive_trajectory(trace, first._inputs, first._domain)
        self.assertTrue(verified['verified'])
        changed = copy.deepcopy(trace)
        event = next(step['observation']['incident_events'][0] for step in changed['transitions']
                     if step['observation']['incident_events'])
        event['received_at'] = 0
        with self.assertRaises(ValueError):
            verify_reactive_trajectory(changed, first._inputs, first._domain)
        self.assertNotIn('incident_update', json.dumps(trace['initial_observation']['pending_deliveries']))


if __name__ == '__main__':
    unittest.main()
