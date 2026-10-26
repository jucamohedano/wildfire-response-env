import copy
import unittest
from collections import Counter

from envs.wildfire_response.regional.benchmark_v1.catalog import (
    FAMILIES, build_catalog, get_task, public_manifest, task_ids, validate_catalog,
)
from envs.wildfire_response.regional.benchmark_v1.calibration import apply_profile, apply_runtime_profile
from envs.wildfire_response.regional.topographic.data import crop_inputs
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixture_inputs()
        cls.domain = fixture_domain(cls.prepared)
        cls.catalog = build_catalog(cls.prepared, cls.domain)

    def test_split_group_seed_integrity_and_family_coverage(self):
        catalog = self.catalog
        self.assertEqual(Counter(group['split'] for group in catalog['groups']), dict(train=80, validation=20, test=20))
        seeds, configurations = set(), set()
        for group in catalog['groups']:
            self.assertNotIn(group['configuration_sha256'], configurations)
            configurations.add(group['configuration_sha256'])
            self.assertEqual(len(group['cases']), 2)
            for case in group['cases']:
                self.assertNotIn(case['seed'], seeds)
                seeds.add(case['seed'])
        for split in ('train', 'validation', 'test'):
            self.assertEqual({group['family'] for group in catalog['groups'] if group['split'] == split}, set(FAMILIES))
        self.assertEqual(len(task_ids(catalog, 'development')), 12)
        self.assertTrue(configurations.isdisjoint(case['configuration_sha256'] for case in catalog['development']))

    def test_manifest_excludes_factors_and_catalog_changes_rejected(self):
        manifest = public_manifest(self.catalog)
        self.assertEqual(len(manifest['tasks']), 252)
        self.assertTrue(all(set(task) == {'id', 'split'} for task in manifest['tasks']))
        broken = copy.deepcopy(self.catalog)
        broken['groups'][0]['cases'][0]['seed'] += 1
        with self.assertRaises(ValueError):
            validate_catalog(broken, self.prepared, self.domain)

    def test_profile_is_explicit_copy_and_variants_share_physical_configuration(self):
        inputs = crop_inputs(self.prepared, self.domain)
        group = self.catalog['groups'][0]
        first = get_task(self.catalog, inputs, group['cases'][0]['id'])
        second = get_task(self.catalog, inputs, group['cases'][1]['id'])
        self.assertEqual(first['scenario']['events'], second['scenario']['events'])
        original = first['scenario']['grounded']['ground_transit_minutes']
        changed = apply_profile(first['scenario'], 'slower_weaker')
        self.assertGreaterEqual(changed['grounded']['ground_transit_minutes'], original)
        self.assertEqual(first['scenario']['grounded']['ground_transit_minutes'], original)
        from types import SimpleNamespace
        core = SimpleNamespace(pack={'assumptions': {'suppression_probability': {'engine': .9}}})
        apply_runtime_profile(core, 'faster_stronger')
        self.assertEqual(core.pack['assumptions']['suppression_probability']['engine'], 1.)


if __name__ == '__main__':
    unittest.main()
