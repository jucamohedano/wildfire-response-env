import unittest

from envs.wildfire_response.regional.ops.objectives import validate_objectives
from envs.wildfire_response.regional.ops.tasks import (
    OPS_TASK_VERSION,
    get_ops_task,
    list_ops_tasks,
)


class OpsTaskManifestTests(unittest.TestCase):
    def test_development_task_ids_and_scenario_mappings(self):
        tasks = list_ops_tasks()

        self.assertEqual(OPS_TASK_VERSION, 'wildfire-ops-task-pack-v0.1.0')
        self.assertEqual([task['id'] for task in tasks], [f'ops-{i:03d}' for i in range(6)])
        for index, task in enumerate(tasks):
            regional_id = f'regional-{index:03d}'
            self.assertEqual(task['scenario_id'], regional_id)
            self.assertEqual(task['group_id'], regional_id)
            self.assertEqual(task['split'], 'development')
            self.assertTrue(task['objectives'])

    def test_only_competing_incidents_has_two_report_objectives(self):
        for index, task in enumerate(list_ops_tasks()):
            expected_reports = ['report-0', 'report-1'] if index == 3 else ['report-0']
            self.assertEqual(
                [objective['sector_id'] for objective in task['objectives']],
                [f'report:{report_id}' for report_id in expected_reports],
            )

    def test_objectives_have_strict_public_task_schema(self):
        expected_fields = {
            'id',
            'kind',
            'sector_id',
            'acceptable_resource_kinds',
            'acceptable_missions',
            'deadline_minute',
        }
        for task in list_ops_tasks():
            self.assertEqual(validate_objectives(task['objectives']), task['objectives'])
            for objective in task['objectives']:
                with self.subTest(task=task['id'], objective=objective['id']):
                    self.assertEqual(set(objective), expected_fields)
                    self.assertEqual(objective['kind'], 'resource_arrival')
                    self.assertIn(objective['sector_id'], {'report:report-0', 'report:report-1'})
                    self.assertEqual(objective['acceptable_resource_kinds'], ['engine'])
                    self.assertEqual(objective['acceptable_missions'], ['recon'])
                    self.assertGreaterEqual(objective['deadline_minute'], 0)
                    self.assertLessEqual(objective['deadline_minute'], 60)
                    self.assertEqual(objective['deadline_minute'] % 2, 0)

    def test_tasks_are_deep_copies(self):
        task = get_ops_task('ops-000')
        task['objectives'][0]['acceptable_resource_kinds'].append('aircraft')
        task['objectives'].append(task['objectives'][0])
        listed = list_ops_tasks()
        listed[0]['objectives'][0]['sector_id'] = 'hidden:fire-location'

        self.assertEqual(get_ops_task('ops-000')['objectives'], [{
            'id': 'arrive-report-0',
            'kind': 'resource_arrival',
            'sector_id': 'report:report-0',
            'acceptable_resource_kinds': ['engine'],
            'acceptable_missions': ['recon'],
            'deadline_minute': 40,
        }])

    def test_no_split_claims_or_hidden_truth_fields(self):
        forbidden_fields = {
            'train',
            'validation',
            'test',
            'truth',
            'fire_location',
            'private_annotations',
            'associated_ignition',
            'is_fire',
        }
        for task in list_ops_tasks():
            self.assertEqual(task['split'], 'development')
            self.assertFalse(forbidden_fields.intersection(task))
            for objective in task['objectives']:
                self.assertFalse(forbidden_fields.intersection(objective))

    def test_unknown_task_ids_are_rejected(self):
        for task_id in ('regional-000', 'ops-006', None, 0):
            with self.subTest(task_id=task_id):
                with self.assertRaises(ValueError):
                    get_ops_task(task_id)


if __name__ == '__main__':
    unittest.main()
