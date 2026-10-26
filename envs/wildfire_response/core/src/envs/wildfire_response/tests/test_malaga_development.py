import copy
import json
from pathlib import Path
import tempfile
import unittest

from wildfire_core.malaga import (
    CASE_IDS, MalagaDevelopmentEnv, ProtectionBaseline, load_pack,
    task_manifest, validate_pack, verify_malaga_trajectory,
)
from envs.wildfire_response.regional.malaga.pack import VERSION, pack_digest
from envs.wildfire_response.regional.ops.generator import digest


def fixture_pack():
    pack = dict(version=VERSION, role='simulation_configuration', actor_input_eligible=False,
                source_bundle_sha256='0' * 64,
                region_bbox_lon_lat=[-5.263180459999944, 36.437454941000055,
                                    -5.065065475999972, 36.61232644100005],
                detection_local='2021-09-08T21:35:00', assumed_ground_transit_minutes=8)
    pack['sha256'] = pack_digest(pack)
    return pack


def complete(pack, identifier=CASE_IDS[0]):
    env = MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True)
    observation, _ = env.reset(identifier)
    policy = ProtectionBaseline(prefer_shelter=False)
    while True:
        observation, _, terminated, truncated, info = env.step(policy(observation))
        if info['error'] is not None:
            raise AssertionError(info['error'])
        if terminated or truncated:
            return env


class MalagaDevelopmentTests(unittest.TestCase):
    def test_explicit_synthetic_conditions_opt_in_is_required(self):
        for value in (False, None, 1, 'true'):
            with self.assertRaises(ValueError):
                MalagaDevelopmentEnv(fixture_pack(), allow_synthetic_conditions=value)
        with self.assertRaises(ValueError):
            MalagaDevelopmentEnv(fixture_pack(), allow_synthetic_conditions=True, max_decisions=120)

    def test_pack_rejects_retrospective_fields_corruption_and_wrong_geography(self):
        pack = fixture_pack()
        self.assertEqual(validate_pack(pack), pack)
        for changes in ({'historical_reference': {'extinguished': '2021-10-24'}},
                        {'actor_input_eligible': True}, {'sha256': '1' * 64},
                        {'region_bbox_lon_lat': [36.4, -5.3, 36.7, -5.0]},
                        {'assumed_ground_transit_minutes': True}):
            with self.assertRaises(ValueError):
                validate_pack({**pack, **changes})

    def test_pack_loader_rejects_duplicate_fields_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pack.json'
            path.write_text(json.dumps(fixture_pack()))
            self.assertEqual(load_pack(path), fixture_pack())
            path.write_text('{"version":1,"version":2}')
            with self.assertRaises(ValueError):
                load_pack(path)
            path.write_bytes(b' ' * 65537)
            with self.assertRaises(ValueError):
                load_pack(path)

    def test_source_clock_and_study_envelope_are_used_without_real_conditions_claims(self):
        env = MalagaDevelopmentEnv(fixture_pack(), allow_synthetic_conditions=True)
        observation, _ = env.reset()
        self.assertEqual((observation['geography']['width'], observation['geography']['height']), (36, 39))
        self.assertEqual(observation['geography']['northwest_lon_lat'],
                         [fixture_pack()['region_bbox_lon_lat'][0], fixture_pack()['region_bbox_lon_lat'][3]])
        self.assertFalse(observation['daylight'])
        self.assertFalse(observation['aircraft_operable'])
        self.assertFalse(observation['capabilities']['historical_weather_replay'])
        self.assertFalse(observation['capabilities']['measured_terrain_in_physics'])
        self.assertTrue(all(base['name'].startswith('Exercise') for base in observation['bases']))
        self.assertTrue(all(base['coordinates'] is None for base in observation['bases']))
        self.assertEqual([group['population'] for group in observation['districts']], [200, 150])

    def test_historical_labels_future_reports_and_private_fire_state_are_absent(self):
        env = MalagaDevelopmentEnv(fixture_pack(), allow_synthetic_conditions=True)
        observation, _ = env.reset(CASE_IDS[1])
        self.assertEqual([report['id'] for report in observation['reports']], ['claim-0'])
        serialized = json.dumps(observation)
        for private in ('2021-10-24', '8651.26', '8401.02', '71925', 'historical_reference',
                        'source_feature_index', 'observedEventA', 'fire_checkpoint'):
            self.assertNotIn(private, serialized)
        self.assertNotIn('report:claim-1', [sector['id'] for sector in observation['sectors']])

    def test_invalid_combined_action_is_atomic_and_three_invalid_actions_truncate(self):
        env = MalagaDevelopmentEnv(fixture_pack(), allow_synthetic_conditions=True)
        observation, _ = env.reset()
        before = copy.deepcopy(observation['resources'])
        for index in range(3):
            observation, _, terminated, truncated, info = env.step(dict(
                requests=[dict(resource_id='engine-valverde', sector_id='report:claim-0', mission='recon')],
                protection_orders=[dict(recipient='private-historical-town', action='evacuate')]))
            self.assertIsNotNone(info['error'])
            self.assertEqual(observation['minute'], 2 * (index + 1))
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(observation['resources'], before)
        self.assertEqual(env._core.pending_requests, set())
        self.assertTrue(env.verify_episode()['passed'])

    def test_reset_and_interleaved_instances_are_isolated(self):
        pack = fixture_pack()
        before = copy.deepcopy(pack)
        first = MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True)
        second = MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True)
        initial, _ = first.reset()
        same, _ = second.reset()
        self.assertEqual(initial, same)
        first.step({})
        self.assertEqual(second.get_situation(), same)
        first.reset(CASE_IDS[2])
        restored, _ = first.reset()
        self.assertEqual(restored, initial)
        self.assertEqual(pack, before)
        with self.assertRaises(ValueError):
            first.reset(seed=123)

    def test_all_variants_finish_and_replay_with_the_public_policy(self):
        pack = fixture_pack()
        self.assertEqual(task_manifest(pack)['source_groups'], 1)
        for identifier in CASE_IDS:
            env = complete(pack, identifier)
            verification = env.verify_episode()
            self.assertTrue(verification['passed'])
            self.assertEqual(verification['objective_score'], env.reward)
            self.assertEqual(env.trajectory()['runtime_pack_sha256'], digest(env._core.pack))
            self.assertLessEqual(env.get_situation()['minute'], 120)

    def test_exact_repeated_rollouts_and_tamper_rejection(self):
        pack = fixture_pack()
        first, second = complete(pack), complete(pack)
        self.assertEqual(first.trajectory(), second.trajectory())
        changed = copy.deepcopy(first.trajectory())
        changed['transitions'][0]['observation']['minute'] += 1
        with self.assertRaises(ValueError):
            verify_malaga_trajectory(changed, pack)
        different = {**pack, 'assumed_ground_transit_minutes': 9}
        different['sha256'] = pack_digest(different)
        with self.assertRaises(ValueError):
            verify_malaga_trajectory(first.trajectory(), different)


if __name__ == '__main__':
    unittest.main()
