import copy
import json
import unittest

from wildfire_core.stress import StressEnv, build_catalog, get_task, load_catalog, verify_stress_trajectory
from envs.wildfire_response.regional.stress import FAMILIES, SPLITS, configuration_digest
from envs.wildfire_response.regional.topographic.data import crop_inputs
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain


class StressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        cls.fixture_json = json.dumps([prepared, domain, build_catalog(prepared, domain)])

    def setUp(self):
        self.prepared, self.domain, self.catalog = json.loads(self.fixture_json)
        self.inputs = crop_inputs(self.prepared, self.domain)

    def environment(self):
        return StressEnv(self.prepared, self.domain, self.catalog,
            expected_prepared_sha256=self.prepared['sha256'], expected_topographic_sha256=self.domain['sha256'],
            expected_catalog_sha256=self.catalog['sha256'], slope_enabled=True, allow_synthetic_conditions=True)

    def test_configuration_groups_and_seeds_are_disjoint_and_all_families_present(self):
        groups = self.catalog['groups']
        self.assertEqual(len(groups), 120)
        self.assertEqual(len({group['configuration_sha256'] for group in groups}), 120)
        seeds = {}
        for split, expected in SPLITS.items():
            selected = [group for group in groups if group['split'] == split]
            self.assertEqual(len(selected), expected)
            self.assertEqual({group['factors']['family'] for group in selected}, set(FAMILIES))
            seeds[split] = {case['seed'] for group in selected for case in group['cases']}
            self.assertEqual(len(seeds[split]), expected * 2)
        self.assertFalse(seeds['train'] & (seeds['validation'] | seeds['test']))
        self.assertFalse(seeds['validation'] & seeds['test'])

    def test_all_variants_have_same_configuration_and_supported_causal_inputs(self):
        for group in self.catalog['groups']:
            for case in group['cases']:
                task = get_task(self.inputs, self.catalog, case['id'])
                self.assertEqual(configuration_digest(task), group['configuration_sha256'])
                self.assertEqual(task['split'], group['split'])
                scenario = task['scenario']
                self.assertLessEqual(len(scenario['reports']), 100)
                for report in scenario['reports']:
                    self.assertTrue(0 <= report['observed_at'] <= report['received_at'] <= 100)
                for event in scenario['events']:
                    self.assertTrue(0 <= event['tick'] <= 100)
                    if event['kind'] == 'ignition':
                        column, row = event['location']
                        self.assertTrue(4 <= column < self.inputs['grid']['width'] - 4)
                        self.assertTrue(4 <= row < self.inputs['grid']['height'] - 4)
                        self.assertEqual(self.inputs['fuel_valid_mask'][row][column], 1)

    def test_caller_pin_and_canonical_configuration_cannot_be_rewritten(self):
        with self.assertRaises(ValueError):
            load_catalog(self.catalog, self.prepared, self.domain, '0' * 64)
        changed = copy.deepcopy(self.catalog)
        changed['groups'][0]['split'] = 'test'
        with self.assertRaises(ValueError):
            load_catalog(changed, self.prepared, self.domain, changed['sha256'])
        first = get_task(self.inputs, self.catalog, self.catalog['groups'][0]['cases'][0]['id'])
        first['scenario']['events'].clear()
        self.assertTrue(get_task(self.inputs, self.catalog, first['id'])['scenario']['events'])

    def test_report_labels_future_configuration_and_catalog_are_not_actor_observations(self):
        environment = self.environment()
        identifier = self.catalog['groups'][19]['cases'][0]['id']
        observation, _ = environment.reset(identifier)
        self.assertEqual(observation['reports'], [])
        serialized = json.dumps(observation)
        for private in ('configuration_sha256', 'ground_hold_minutes', 'factors', 'future_events', 'false_alarm'):
            self.assertNotIn(private, serialized)
        self.assertFalse(observation['aircraft_operable'])
        self.assertFalse(observation['weather_context']['physics_coupled'])

    def test_unavailable_units_and_readiness_changes_apply_in_topographic_mode(self):
        environment = self.environment()
        observation, _ = environment.reset(self.catalog['groups'][6]['cases'][0]['id'])
        units = {unit['id']: unit for unit in observation['resources']}
        self.assertEqual(units['engine-valverde']['state'], 'unavailable')
        self.assertEqual(units['aircraft-las-arenas']['state'], 'unavailable')
        while observation['minute'] < 12:
            observation, *_ = environment.step({})
        units = {unit['id']: unit for unit in observation['resources']}
        self.assertEqual(units['engine-valverde']['state'], 'available')
        self.assertEqual(units['aircraft-las-arenas']['state'], 'unavailable')

    def test_wind_shift_occurs_only_at_scheduled_tick(self):
        environment = self.environment()
        observation, _ = environment.reset(self.catalog['groups'][4]['cases'][0]['id'])
        while observation['minute'] < 12:
            observation, *_ = environment.step({})
        self.assertEqual(observation['wind'], [1, 0])
        observation, *_ = environment.step({})
        self.assertEqual(observation['wind'], [-.5, 1.7])

    def test_false_alarm_complete_export_replays_and_rejects_tampering(self):
        environment = self.environment()
        identifier = self.catalog['groups'][1]['cases'][0]['id']
        initial, _ = environment.reset(identifier)
        while True:
            _, _, terminated, truncated, info = environment.step({})
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        trace = json.loads(json.dumps(environment.trajectory()))
        self.assertTrue(environment.verify_episode()['passed'])
        self.assertEqual(verify_stress_trajectory(trace, self.prepared, self.domain, self.catalog)['total_score'], environment.reward)
        trace['transitions'][-1]['objective_reward'] += 1
        with self.assertRaises(ValueError):
            verify_stress_trajectory(trace, self.prepared, self.domain, self.catalog)
        reset, _ = environment.reset(identifier)
        self.assertEqual(reset, initial)
        with self.assertRaises(ValueError):
            environment.reset(identifier, seed=0)

    def test_invalid_actions_truncate_without_poisoning_a_reset(self):
        environment = self.environment()
        initial, _ = environment.reset()
        for index in range(3):
            observation, _, _, truncated, info = environment.step({'invalid': True})
            self.assertIsNotNone(info['error'])
            self.assertEqual(observation['minute'], (index + 1) * 2)
        self.assertTrue(truncated)
        self.assertEqual(environment.reset()[0], initial)


if __name__ == '__main__':
    unittest.main()
