import copy
import json
import math
import unittest

from wildfire_core.topographic import BOUNDARY_CASE_ID, CASE_IDS, TopographicEnv, verify_topographic_trajectory
from envs.wildfire_response.regional.malaga.pack import engine_pack
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.prepared.environment import PreparedFire
from envs.wildfire_response.regional.topographic.data import POLICY, VERSION, crop_inputs, load_domain
from envs.wildfire_response.regional.topographic.fire import TopographicFire, edge_grade, edge_multiplier
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs


def fixture_domain(prepared=None):
    prepared = fixture_inputs() if prepared is None else prepared
    height, width = 36, 37
    grid = copy.deepcopy(prepared['grid'])
    grid['height'] = height
    grid['centre_lon_lat'] = grid['centre_lon_lat'][:height]
    domain = dict(version=VERSION, role='simulation_configuration', prepared_inputs_sha256=prepared['sha256'],
        crop=dict(left=0, top=0, width=width, height=height), grid=grid,
        elevations_metres=[[100 + column * 10 + row * 3 for column in range(width)] for row in range(height)],
        height_support_mask=[[1] * width for row in range(height)], policy=copy.deepcopy(POLICY), provenance=[])
    domain['sha256'] = digest(domain)
    return domain


def create(*, enabled=True, prepared=None, domain=None):
    prepared = fixture_inputs() if prepared is None else prepared
    domain = fixture_domain(prepared) if domain is None else domain
    return TopographicEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], slope_enabled=enabled, allow_synthetic_conditions=True)


class TopographicTests(unittest.TestCase):
    def test_edge_gradients_are_shared_antisymmetric_and_distance_aware(self):
        heights = [[100, 150], [100, 150]]
        forward = edge_grade(heights, (0, 0), (1, 0))
        backward = edge_grade(heights, (1, 0), (0, 0))
        self.assertEqual(forward, .1)
        self.assertEqual(backward, -.1)
        self.assertAlmostEqual(edge_grade(heights, (0, 0), (1, 1)), .1 / math.sqrt(2))
        uphill = edge_multiplier(heights, (0, 0), (1, 0), enabled=True)
        downhill = edge_multiplier(heights, (1, 0), (0, 0), enabled=True)
        self.assertGreater(uphill, 1)
        self.assertLess(downhill, 1)
        self.assertAlmostEqual(uphill * downhill, 1)
        self.assertEqual(edge_multiplier(heights, (0, 0), (0, 1), enabled=True), 1)
        self.assertEqual(edge_multiplier(heights, (0, 0), (1, 0), enabled=False), 1)

    def test_unknown_edge_heights_never_become_neutral_fallback(self):
        for invalid in (None, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                edge_multiplier([[100, invalid]], (0, 0), (1, 0), enabled=False)
        for point in ((-1, 0), (2, 0)):
            with self.assertRaises(ValueError):
                edge_grade([[100, 100]], (0, 0), point)

    def test_pin_support_and_height_holes_fail_closed(self):
        prepared = fixture_inputs()
        changes = [lambda domain: domain['height_support_mask'][5].__setitem__(5, 0),
                   lambda domain: domain['elevations_metres'][5].__setitem__(5, None),
                   lambda domain: domain['elevations_metres'][5].__setitem__(5, 0),
                   lambda domain: domain['grid'].update(transform=[500, 0, 297500, 0, -500, 4054500]),
                   lambda domain: domain.update(prepared_inputs_sha256='0' * 64)]
        for mutate in changes:
            domain = fixture_domain(prepared)
            mutate(domain)
            domain['sha256'] = digest({key: value for key, value in domain.items() if key != 'sha256'})
            with self.assertRaises(ValueError):
                load_domain(domain, prepared, domain['sha256'])
        with self.assertRaises(ValueError):
            load_domain(fixture_domain(prepared), prepared, '0' * 64)
        with self.assertRaises(ValueError):
            create(enabled=1)

    def test_cropped_frame_and_all_actor_locations_remain_coherent(self):
        environment = create()
        observation, _ = environment.reset()
        self.assertEqual((observation['geography']['width'], observation['geography']['height']), (37, 36))
        self.assertEqual(environment.cell_lon_lat(2, 3), fixture_inputs()['grid']['centre_lon_lat'][3][2])
        self.assertTrue(observation['capabilities']['slope_effects'])
        self.assertTrue(observation['capabilities']['measured_terrain_in_physics'])
        disabled, _ = create(enabled=False).reset()
        self.assertFalse(disabled['capabilities']['measured_terrain_in_physics'])
        for group in observation['districts']:
            for location in (group['home'], group['refuge']):
                self.assertTrue(0 < location[0] < 36 and 0 < location[1] < 35)
        self.assertNotIn('events', observation)
        self.assertEqual(len(observation['landscape']['elevations_metres']), 36)

    def test_disabled_kernel_and_flat_enabled_kernel_match_inherited_fire(self):
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        active = crop_inputs(prepared, domain)
        pack = engine_pack(prepared['episode_pack'])
        original = PreparedFire(pack, 123, active)
        disabled = TopographicFire(pack, 123, active, domain['elevations_metres'], slope_enabled=False)
        flat = [[100] * 37 for row in range(36)]
        enabled = TopographicFire(pack, 123, active, flat, slope_enabled=True)
        for simulation in (original, disabled, enabled):
            simulation.add_fire(12, 18)
        for minute in range(40):
            for simulation in (original, disabled, enabled):
                simulation.step()
            for other in (disabled, enabled):
                self.assertEqual(original.cells, other.cells)
                self.assertEqual(original.groups, other.groups)
                self.assertEqual(original.rng.getstate(), other.rng.getstate())

    def test_boundary_probe_is_censored_zero_reward_and_replayable(self):
        for enabled in (False, True):
            environment = create(enabled=enabled)
            observation, _ = environment.reset(BOUNDARY_CASE_ID)
            observation, reward, terminated, truncated, info = environment.step({})
            self.assertFalse(terminated)
            self.assertTrue(truncated)
            self.assertEqual(reward, 0)
            self.assertTrue(info['boundary_censored'])
            self.assertFalse(info['suitable_for_training'])
            self.assertEqual(info['boundary_contact_at'], 2)
            result = environment.verify_episode()
            self.assertTrue(result['passed'])
            self.assertFalse(result['suitable_for_training'])

    def test_reset_isolation_and_three_invalid_actions(self):
        environment = create()
        initial, _ = environment.reset()
        for index in range(3):
            observation, _, _, truncated, info = environment.step({'invalid': True})
            self.assertIsNotNone(info['error'])
        self.assertTrue(truncated)
        self.assertFalse(info['boundary_censored'])
        reset, _ = environment.reset()
        self.assertEqual(initial, reset)
        reset['landscape']['elevations_metres'][1][1] = -9999
        self.assertGreater(environment.get_situation()['landscape']['elevations_metres'][1][1], 0)

    def test_complete_json_trace_pins_mode_and_replays_reward(self):
        from wildfire_core.topographic import ProtectionBaseline
        prepared, domain = fixture_inputs(), fixture_domain()
        environment = create(prepared=prepared, domain=domain)
        observation, _ = environment.reset(CASE_IDS[1])
        policy = ProtectionBaseline(prefer_shelter=False)
        while True:
            observation, _, terminated, truncated, info = environment.step(policy(observation))
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        trace = json.loads(json.dumps(environment.trajectory()))
        self.assertTrue(environment.verify_episode()['passed'])
        self.assertEqual(verify_topographic_trajectory(trace, prepared, domain)['total_score'], environment.reward)
        trace['transitions'][0]['objective_reward'] = 999
        with self.assertRaises(ValueError):
            verify_topographic_trajectory(trace, prepared, domain)


if __name__ == '__main__':
    unittest.main()
