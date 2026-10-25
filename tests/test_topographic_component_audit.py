import copy
import unittest

from eval.check_topographic_components import inspect_trace


def observation(minute):
    return dict(minute=minute, reports=[], resources=[], protection_orders=[],
        weather_context=dict(now_utc='2021-09-08T19:35:00Z', records=[]),
        acknowledgements=[], readback_events=[], wind=[1, 0])


def trace():
    return dict(ticks_per_action=2, initial_observation=observation(0),
                transitions=[dict(observation=observation(2), terminated=True, truncated=False)])


class ComponentAuditTests(unittest.TestCase):
    def test_audit_does_not_modify_the_trace(self):
        value = trace()
        original = copy.deepcopy(value)
        self.assertEqual(inspect_trace(value), set())
        self.assertEqual(value, original)

    def test_future_evidence_and_unfinished_clock_fail(self):
        for mutate in (
            lambda value: value['transitions'][0]['observation'].update(minute=3),
            lambda value: value['transitions'][0].update(terminated=False),
            lambda value: value['initial_observation'].update(world_fire=[]),
            lambda value: value['initial_observation']['reports'].append(dict(observed_at=0, received_at=2)),
            lambda value: value['initial_observation']['weather_context']['records'].append(
                dict(observed_at_utc='2021-09-08T19:30:00Z', received_at_utc='2021-09-08T19:36:00Z')),
        ):
            value = trace()
            mutate(value)
            with self.assertRaises(ValueError):
                inspect_trace(value)

    def test_protection_effect_cannot_precede_delivered_acknowledged_execution(self):
        valid = dict(issued_at=0, delivered_at=0, acknowledged_at=1, executed_at=2,
                     status='executed', effect='evacuation_started')
        value = trace()
        value['transitions'][0]['observation']['protection_orders'] = [valid]
        self.assertEqual(inspect_trace(value), set())
        for fields in (dict(delivered_at=None), dict(acknowledged_at=3),
                       dict(executed_at=None), dict(status='failed')):
            changed = copy.deepcopy(value)
            changed['transitions'][0]['observation']['protection_orders'][0].update(fields)
            with self.assertRaises(ValueError):
                inspect_trace(changed)


if __name__ == '__main__':
    unittest.main()
