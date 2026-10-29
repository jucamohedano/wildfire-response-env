import copy
import json
import tempfile
from pathlib import Path
import unittest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.scenarios import CASE_IDS
from envs.wildfire_response.incident_day.outcomes import recompute
from envs.wildfire_response.incident_day.clock import solar_daylight
from datetime import datetime


def create(**kwargs):
    inputs, network = fixture_inputs()
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'], **kwargs)


class IncidentDayCoreTests(unittest.TestCase):
    def test_handover_is_aged_large_fire_with_deployed_resource(self):
        env = create()
        observation, _ = env.reset()
        self.assertEqual(observation['clock']['horizon_minutes'], 1440)
        self.assertGreater(env._prehistory['footprint_hectares'], 500)
        self.assertGreater(max(cell['age'] for row in env.fire.cells for cell in row), 0)
        self.assertTrue(any(unit['state'] == 'on_scene' for unit in observation['resources']))
        self.assertNotIn('initial_fire', observation)
        self.assertNotIn('_events', observation)

    def test_reads_and_private_mutations_do_not_change_public_view(self):
        env = create()
        observation, _ = env.reset()
        before = env.checkpoint()
        env.get_reports()
        self.assertTrue(env.check_action({'advance_minutes': 20})['valid'])
        self.assertEqual(env.checkpoint(), before)
        env.fire.cells[1][1]['heat'] = 1.
        env._events.append(dict(minute=1400, kind='wind', wind=[-2., 0.]))
        self.assertEqual(env.get_situation(), observation)
        with self.assertRaises(ValueError):
            env.get_reports(['future-report'])

    def test_checkpoint_resume_exactness_and_tamper_rejection(self):
        env = create()
        env.reset()
        env.request_status('center-0', 'resources')
        while env.minute < 80:
            env.advance_time(20)
        checkpoint = json.loads(json.dumps(env.checkpoint()))
        other = create()
        self.assertEqual(other.restore(checkpoint), env.get_situation())
        for unused in range(5):
            self.assertEqual(other.advance_time(20), env.advance_time(20))
        self.assertEqual(other.checkpoint(), env.checkpoint())
        corrupted = copy.deepcopy(checkpoint)
        corrupted['state']['minute'] += 1
        with self.assertRaises(ValueError):
            create().restore(corrupted)

    def test_invalid_orders_consume_time_preserve_missions_and_abandon(self):
        env = create()
        env.reset()
        for unused in range(3):
            observation, reward, terminated, truncated, info = env.step({'bad': 1})
        self.assertEqual(observation['minute'], 3)
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(reward, 0)
        self.assertTrue(env.verify_episode()['passed'])

    def test_sparse_day_completes_and_reward_replays(self):
        env = create()
        env.reset()
        while True:
            observation, reward, terminated, truncated, info = env.advance_time(60)
            if terminated or truncated:
                break
        self.assertTrue(terminated, info)
        self.assertEqual(observation['minute'], 1440)
        self.assertLess(observation['decision'], 200)
        self.assertEqual(reward, env.outcomes()['graded_score'])
        self.assertTrue(env.verify_episode()['passed'])

    def test_inherited_burned_interior_and_harm_do_not_count_again(self):
        population = {'group': dict(count=100, exposed=20, status='arrived')}
        first = dict(minute=0, footprint=[0, 1], new_footprint=[], burnable=[0, 1, 2, 3],
                     cell_area_hectares=25, population=population)
        records = [first] + [dict(minute=minute, new_footprint=[], population=copy.deepcopy(population))
                             for minute in range(1, 1441)]
        outcome = recompute(records)
        self.assertEqual(outcome['additional_burned_hectares'], 0)
        self.assertEqual(outcome['new_exposed_population'], 0)
        self.assertEqual(outcome['graded_score'], 1.)

    def test_solar_window_not_fixed_seven_to_twenty_clock(self):
        self.assertFalse(solar_daylight(datetime.fromisoformat('2021-09-09T04:00:00+00:00'), -5.15, 36.55))
        self.assertTrue(solar_daylight(datetime.fromisoformat('2021-09-09T12:00:00+00:00'), -5.15, 36.55))
        self.assertFalse(solar_daylight(datetime.fromisoformat('2021-09-09T23:00:00+00:00'), -5.15, 36.55))


if __name__ == '__main__':
    unittest.main()
