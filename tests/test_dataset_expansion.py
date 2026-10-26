import copy
import unittest
from collections import Counter
from pathlib import Path
import tempfile
from unittest.mock import patch

from wildfire_core.expansion import ExpandedEnv, build_catalog, coverage, public_manifest
from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog as legacy_catalog
from envs.wildfire_response.regional.expansion.catalog import FAMILIES, get_task, split_for_layout
from envs.wildfire_response.regional.stress import configuration_digest
from envs.wildfire_response.regional.topographic.data import crop_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs


class ExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixture_inputs()
        cls.domain = fixture_domain(cls.prepared)
        cls.catalog = build_catalog(cls.prepared, cls.domain)

    def create(self, **kwargs):
        return ExpandedEnv(self.prepared, self.domain,
            expected_prepared_sha256=self.prepared['sha256'],
            expected_topographic_sha256=self.domain['sha256'], **kwargs)

    def test_counts_unique_configurations_seeds_and_grouped_layouts(self):
        catalog = self.catalog
        self.assertEqual(catalog['split_groups'], dict(train=640, validation=160, test=160))
        self.assertEqual(len(public_manifest(catalog)['tasks']), 1920)
        fingerprints, seeds, layouts = set(), set(), {}
        old = legacy_catalog(self.prepared, self.domain)
        legacy = {group['configuration_sha256'] for group in old['groups']}
        legacy.update(case['configuration_sha256'] for case in old['development'])
        for group in catalog['groups']:
            fingerprint = group['configuration_sha256']
            self.assertNotIn(fingerprint, fingerprints | legacy)
            fingerprints.add(fingerprint)
            self.assertEqual(group['split'], split_for_layout(group['layout_sha256']))
            self.assertEqual(layouts.setdefault(group['layout_sha256'], group['split']), group['split'])
            self.assertEqual(len(group['cases']), 2)
            for case in group['cases']:
                self.assertNotIn(case['seed'], seeds)
                seeds.add(case['seed'])
        for split, count in (('train', 80), ('validation', 20), ('test', 20)):
            self.assertEqual(Counter(group['family'] for group in catalog['groups'] if group['split'] == split),
                             {family: count for family in FAMILIES})

    def test_deterministic_defensive_copy_and_public_privacy(self):
        original = copy.deepcopy(self.catalog)
        returned = build_catalog(self.prepared, self.domain)
        returned['groups'][0]['factors']['report_delay'] = 999
        self.assertEqual(original, build_catalog(self.prepared, self.domain))
        with patch.dict('envs.wildfire_response.regional.expansion.catalog._CACHE', {}, clear=True):
            self.assertEqual(original, build_catalog(self.prepared, self.domain))
        manifest = public_manifest(self.catalog)
        self.assertTrue(all(set(task) == {'id', 'split'} for task in manifest['tasks']))
        self.assertNotIn('groups', manifest)
        for row in coverage(self.catalog)['splits'].values():
            self.assertEqual(len(row['levels']['report_delay']), 5)
            self.assertEqual(len(row['levels']['initial_wind']), 5)
            self.assertEqual(len(row['levels']['duplicate_count']), 4)
        with self.assertRaises(ValueError):
            self.create(catalog=returned)
        with self.assertRaises(ValueError):
            self.create(expected_catalog_sha256='0' * 64)

    def test_variants_share_configurations_and_every_task_compiles(self):
        inputs = crop_inputs(self.prepared, self.domain)
        for group in self.catalog['groups']:
            for case in group['cases']:
                task = get_task(self.catalog, inputs, case['id'])
                self.assertEqual(configuration_digest(task), group['configuration_sha256'])
                self.assertLessEqual(len(task['scenario']['reports']), 100)
                settings = group['factors']
                if group['family'] == 'delayed_reinforcement':
                    self.assertGreaterEqual(settings['ground_hold_minutes'], 12)
                    self.assertGreaterEqual(settings['request_delay'], 2)
                if group['family'] == 'unavailable_resources':
                    self.assertTrue(settings['hold_second_base'])
                    self.assertGreaterEqual(settings['ground_hold_minutes'], 24)
                if group['family'] == 'failed_protection':
                    self.assertEqual(settings['broadcast_failures'], 1)
        with self.assertRaises(ValueError):
            get_task(self.catalog, inputs, 'unknown')

    def test_compile_artifacts_keep_catalog_private_and_never_overwrite(self):
        import json
        from eval.build_expansion import compile_dataset
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'compiled'
            summary = compile_dataset(output, self.prepared, self.domain)
            self.assertEqual(summary['status'], 'compiled')
            self.assertFalse(summary['rank_eligible'])
            self.assertEqual(json.loads((output / 'private/catalog.json').read_text()), self.catalog)
            self.assertEqual(json.loads((output / 'provenance.json').read_text())['new_real_incidents'], 0)
            self.assertNotIn('factors', (output / 'public/manifest.json').read_text())
            with self.assertRaises(ValueError):
                compile_dataset(output, self.prepared, self.domain)
            with self.assertRaises(ValueError):
                compile_dataset(Path(directory) / 'bad', self.prepared, self.domain, seconds=0)
            with patch('eval.build_expansion.time.monotonic', side_effect=[0, 2, 2]):
                stopped = compile_dataset(Path(directory) / 'stopped', self.prepared, self.domain,
                                          smoke=True, seconds=1)
            self.assertEqual(stopped['status'], 'budget_stopped')
            self.assertEqual(stopped['cases'], [])

    def test_complete_episode_replays_and_uses_unchanged_reward(self):
        environment = self.create()
        identifier = next(group['cases'][0]['id'] for group in self.catalog['groups']
                          if group['split'] == 'train' and group['family'] == 'false_alarm')
        initial, _ = environment.reset(identifier)
        self.assertEqual(initial['task_set'], 'wildfire_synthetic_expansion_v1')
        self.assertNotIn('factors', initial)
        for decision in range(60):
            observation, reward, terminated, truncated, _ = environment.step({})
        self.assertEqual(observation['minute'], 120)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, 1.0)
        self.assertTrue(environment.verify_episode()['passed'])
        environment._ops_trajectory['transitions'][0]['action'] = {'bad_field': 1}
        with self.assertRaises(ValueError):
            environment.verify_episode()


if __name__ == '__main__':
    unittest.main()
