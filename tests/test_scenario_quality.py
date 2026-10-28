import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wildfire_core.scenario_quality import audit_catalog, make_context, summarize, validate_task
from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog, get_task
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs


class ScenarioQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixture_inputs()
        cls.domain = fixture_domain(cls.prepared)
        cls.context = make_context(cls.prepared, cls.domain)
        cls.catalog = build_catalog(cls.prepared, cls.domain)

    def task(self, family='uncertain_smoke'):
        group = next(group for group in self.catalog['groups'] if group['family'] == family)
        return get_task(self.catalog, self.context['inputs'], group['cases'][0]['id'])

    def errors(self, task, family=None, context=None):
        return [item['code'] for item in validate_task(task, context or self.context, family=family)
                if item['severity'] == 'error']

    def small_catalog(self):
        catalog = copy.deepcopy(self.catalog)
        catalog['groups'] = catalog['groups'][:1]
        catalog['development'] = []
        catalog['split_groups'] = {'train': 1}
        return self.repin(catalog)

    def repin(self, catalog):
        catalog['sha256'] = digest({key: value for key, value in catalog.items() if key != 'sha256'})
        return catalog

    def test_false_claims_and_delayed_reports_are_allowed(self):
        task = self.task('false_alarm')
        self.assertEqual(self.errors(task, 'false_alarm'), [])
        self.assertEqual(self.errors(task), [])
        report = task['scenario']['reports'][0]
        report.update(location=self.context['population']['settlement-east']['home'][:], received_at=15)
        self.assertEqual(self.errors(task, 'false_alarm'), [])
        self.assertTrue(any(item['code'] == 'night_aircraft' for item in validate_task(task, self.context)))

    def test_clock_types_future_receipt_and_duplicate_identity_fail(self):
        with self.assertRaises(ValueError):
            validate_task(self.task(), self.context, horizon=10 ** 10)
        mutations = [
            lambda task: task['scenario']['reports'][0].update(observed_at=5, received_at=4),
            lambda task: task['scenario']['reports'][0].update(received_at=120),
            lambda task: task['scenario']['events'][0].update(tick=True),
            lambda task: task['scenario']['reports'].append(copy.deepcopy(task['scenario']['reports'][0])),
            lambda task: task['scenario'].update(seed=True),
            lambda task: task['scenario'].update(synthetic=False),
            lambda task: task.update(communication={'mission_swap_once': 'yes'}),
            lambda task: task['scenario'].update(events=7),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                task = self.task()
                mutate(task)
                self.assertTrue(self.errors(task))

    def test_ignition_rejects_guard_unknown_fuel_and_missing_height(self):
        for point in ([1, 8], [-1, 8], [999, 8]):
            task = self.task()
            task['scenario']['events'][0]['location'] = point
            self.assertIn('geography', self.errors(task))
        for field in ('fuel_valid_mask', 'elevations_metres', 'height_support_mask'):
            with self.subTest(field=field):
                context = copy.deepcopy(self.context)
                task = self.task()
                column, row = task['scenario']['events'][0]['location']
                if field == 'fuel_valid_mask':
                    context['inputs'][field][row][column] = 0
                else:
                    context['domain'][field][row][column] = 0
                self.assertIn('geography', self.errors(task, context=context))

    def test_parameter_and_reference_contradictions_fail(self):
        mutations = [
            lambda scenario: scenario.update(wind=[float('nan'), 0]),
            lambda scenario: scenario.update(wind=[3.1, 0]),
            lambda scenario: scenario['grounded'].update(ground_transit_minutes=0),
            lambda scenario: scenario['transport'].update(request_extra_delay_minutes=-1),
            lambda scenario: scenario['transport'].update(resource_unavailable_until={'fake-unit': 10}),
            lambda scenario: scenario['grounded'].update(clarification_locations={'missing-report': [10, 10]}),
            lambda scenario: scenario['protection'].update(sheltered_dose_per_tick=99),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                task = self.task()
                mutate(task['scenario'])
                self.assertTrue(self.errors(task))

    def test_weather_and_daylight_clocks_cannot_silently_diverge(self):
        task = self.task()
        task['scenario']['grounded']['local_clock_minute'] = 8 * 60
        self.assertIn('clock_alignment', self.errors(task))
        task = self.task()
        task['scenario']['events'].append(dict(tick=0, kind='daylight', value=True))
        self.assertIn('clock_alignment', self.errors(task))

    def test_forward_lineage_is_structural_not_a_text_judge(self):
        task = self.task('conflicting_reports')
        forward = next(report for report in task['scenario']['reports'] if report['id'].startswith('forward-'))
        forward['text'] = 'Alternative synthetic wording, not a semantic truth label'
        self.assertNotIn('duplicate_lineage', self.errors(task))
        forward['source'] = 'an-independent-witness'
        self.assertIn('duplicate_lineage', self.errors(task))

    def test_family_promises_match_actual_conditions(self):
        task = self.task('false_alarm')
        task['scenario']['events'].append(dict(tick=4, kind='ignition', location=[8, 8]))
        self.assertIn('family', self.errors(task, 'false_alarm'))
        task = self.task('failed_protection')
        task['scenario']['protection']['broadcast_failures'] = 0
        self.assertIn('family', self.errors(task, 'failed_protection'))
        task = self.task('unavailable_resources')
        task['scenario']['transport']['resource_unavailable_until'] = {}
        self.assertIn('family', self.errors(task, 'unavailable_resources'))
        task = self.task('wind_shift')
        for event in task['scenario']['events']:
            if event['kind'] == 'wind':
                event['wind'] = [task['scenario']['wind'][0] + .00001, task['scenario']['wind'][1]]
        self.assertIn('family', self.errors(task, 'wind_shift'))

    def test_plausible_but_weak_exercises_warn_instead_of_being_deleted(self):
        task = self.task('wind_shift')
        for event in task['scenario']['events']:
            event['tick'] = 12 if event['kind'] == 'ignition' else 4
        findings = validate_task(task, self.context, family='wind_shift')
        self.assertTrue(any(item['code'] == 'preignition_wind_shift' and item['severity'] == 'warning' for item in findings))
        self.assertFalse(any(item['severity'] == 'error' for item in findings))
        task = self.task()
        task['scenario']['events'][0].update(tick=0, location=self.context['population']['settlement-west']['refuge'][:])
        task['scenario']['transport']['resource_unavailable_until'] = {
            unit['id']: 150 for unit in self.context['pack']['scenario_inventory'] if unit['kind'] != 'aircraft'}
        codes = {item['code'] for item in validate_task(task, self.context)}
        self.assertTrue({'population_at_ignition', 'ground_arrival_outside_horizon'} <= codes)

    def test_catalog_errors_are_collected_and_public_summary_excludes_private_details(self):
        catalog = self.small_catalog()
        catalog['groups'][0]['cases'][1]['seed'] = catalog['groups'][0]['cases'][0]['seed']
        self.repin(catalog)
        report = audit_catalog(catalog, lambda identifier: get_task(catalog, self.context['inputs'], identifier), self.context)
        summary = summarize(report)
        self.assertFalse(summary['contract_passed'])
        self.assertFalse(summary['empirically_validated'])
        self.assertEqual(summary['policy_rollouts'], 0)
        self.assertNotIn(catalog['groups'][0]['cases'][0]['id'], json.dumps(summary))
        self.assertTrue(any(item['code'] == 'catalog_integrity' for case in report['cases'] for item in case['findings']))
        report = audit_catalog(self.small_catalog(), lambda identifier: (_ for _ in ()).throw(ValueError('bad config')), self.context)
        self.assertEqual(summarize(report)['findings_by_code']['compile_failure'], 2)
        catalog = self.small_catalog()
        catalog['topographic_inputs_sha256'] = '0' * 64
        catalog['groups'][0]['cases'][1]['variant'] = 0
        self.repin(catalog)
        report = audit_catalog(catalog, lambda identifier: get_task(catalog, self.context['inputs'], identifier), self.context)
        self.assertEqual(len(report['catalog_findings']), 2)

    def test_expansion_layout_split_tampering_is_rejected(self):
        catalog = self.small_catalog()
        catalog['version'] = 'wildfire-synthetic-expansion-catalog-v1'
        catalog['groups'][0]['layout_sha256'] = '0' * 64
        self.repin(catalog)
        report = audit_catalog(catalog, lambda identifier: get_task(catalog, self.context['inputs'], identifier), self.context)
        self.assertFalse(summarize(report)['contract_passed'])

    def test_registered_catalogs_all_variants_and_fresh_output(self):
        from eval.validate_scenarios import audit_all
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'audit'
            result = audit_all(output, self.prepared, self.domain)
            self.assertEqual(result['tasks_checked'], 2412)
            self.assertEqual(result['errors'], 0)
            self.assertFalse(result['held_out_outcomes_inspected'])
            self.assertTrue((output / 'public/report.html').exists())
            private = output / 'private/synthetic_expansion_v1/findings.json'
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            self.assertFalse(any('task_id' in row for row in result['catalogs']))
            with self.assertRaises(ValueError):
                audit_all(output, self.prepared, self.domain)

    def test_compilation_gate_retains_audit_and_withholds_rejected_manifest(self):
        from eval.build_expansion import compile_dataset
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'rejected'
            rejected = dict(cases=[], context_findings=[], catalog_findings=[dict(code='catalog_integrity', severity='error')],
                version='test', rules_sha256='0' * 64, catalog_version='test', catalog_sha256='0' * 64)
            with patch('eval.build_expansion.audit_catalog', return_value=rejected):
                with self.assertRaisesRegex(ValueError, 'quality errors block'):
                    compile_dataset(output, self.prepared, self.domain)
            self.assertTrue((output / 'private/scenario-quality.json').exists())
            self.assertFalse((output / 'public/manifest.json').exists())

    def test_new_checks_preserve_frozen_environment_identity(self):
        from envs.wildfire_response.regional.expansion.environment import implementation_digest
        root = Path(__file__).resolve().parents[1]
        record = json.loads((root / 'data/synthetic_expansion_v1/provenance.json').read_text())
        self.assertEqual(implementation_digest(), record['engine_and_expansion_sha256'])


if __name__ == '__main__':
    unittest.main()
