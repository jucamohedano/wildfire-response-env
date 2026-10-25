import json
import unittest

from eval.run_topographic_stress import public_catalog, select_cases
from wildfire_core.stress import build_catalog
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain


class StressSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepared = fixture_inputs()
        cls.catalog_json = json.dumps(build_catalog(prepared, fixture_domain(prepared)))

    def setUp(self):
        self.catalog = json.loads(self.catalog_json)

    def test_smoke_is_registered_training_only_with_variant_zero(self):
        selected = select_cases(self.catalog, 'train', full=False, consume_held_out=False)
        self.assertEqual(len(selected), 12)
        self.assertTrue(all(group['split'] == 'train' and case['variant'] == 0 for group, case in selected))
        self.assertEqual(len({group['factors']['family'] for group, _ in selected}), 6)
        self.assertTrue(any(group['factors']['ground_hold_minutes'] == 40 for group, _ in selected))

    def test_held_out_requires_explicit_outcome_consumption(self):
        for split in ('validation', 'test'):
            with self.assertRaises(ValueError):
                select_cases(self.catalog, split, full=True, consume_held_out=False)
            with self.assertRaises(ValueError):
                select_cases(self.catalog, split, full=False, consume_held_out=True)
            selected = select_cases(self.catalog, split, full=True, consume_held_out=True)
            self.assertEqual(len(selected), 40)
            self.assertTrue(all(group['split'] == split for group, _ in selected))

    def test_public_catalog_has_hashes_and_membership_not_scenario_factors(self):
        public = public_catalog(self.catalog)
        self.assertEqual(public['catalog_sha256'], self.catalog['sha256'])
        for group in public['groups']:
            self.assertEqual(set(group), {'id', 'split', 'configuration_sha256', 'cases'})
        self.assertNotIn('false_alarm', json.dumps(public))


if __name__ == '__main__':
    unittest.main()
