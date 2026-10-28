import unittest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


class CurrentConditionTests(unittest.TestCase):
    def test_resident_current_condition_does_not_repeat_cumulative_exposure(self):
        inputs, network = fixture_inputs()
        environment = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                     expected_network_sha256=network['sha256'])
        environment.reset()
        group = environment.ops.groups['population-0']
        group['exposed'] = True
        environment._signals[group['id']] = 'exposed'
        while environment.minute < 30:
            environment.advance_time(1)
        reports = [item for item in environment.bus.reports
                   if item['source'] == group['id'] and item['kind'] == 'resident_update']
        self.assertTrue(reports)
        self.assertEqual(reports[0]['payload']['condition'], 'clear')
        self.assertTrue(reports[0]['payload']['group']['exposed'])
        self.assertFalse(reports[0]['urgent'])
        self.assertTrue(group['exposed'])


if __name__ == '__main__':
    unittest.main()
