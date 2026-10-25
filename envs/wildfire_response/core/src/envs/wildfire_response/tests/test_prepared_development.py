import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from wildfire_core.prepared import CASE_IDS, PreparedDataEnv, ProtectionBaseline, validate_inputs, verify_prepared_trajectory
from envs.wildfire_response.regional.malaga.pack import pack_digest
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING, VERSION, load_inputs


def fixture_inputs():
    episode = dict(version='wildfire-malaga-episode-pack-v1', role='simulation_configuration', actor_input_eligible=False,
        source_bundle_sha256='0' * 64, region_bbox_lon_lat=[-5.263180459999944, 36.437454941000055,
        -5.065065475999972, 36.61232644100005], detection_local='2021-09-08T21:35:00', assumed_ground_transit_minutes=8)
    episode['sha256'] = pack_digest(episode)
    moment = datetime(2021, 9, 8, 18, 30, tzinfo=timezone.utc)
    records = []
    for index in range(10):
        records.append(dict(observed_at_utc=(moment + timedelta(minutes=30 * index)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            temperature_c=25.0, relative_humidity_percent=50.0, wind_speed_m_s=3.0,
            wind_direction_degrees=230.0, precipitation_mm=0.0))
    value = dict(version=VERSION, role='simulation_configuration',
        grid=dict(width=37, height=41, cell_km=.5, crs='EPSG:25830', transform=[500, 0, 297000, 0, -500, 4054500],
            northwest_lon_lat=[-5.27, 36.62],
            centre_lon_lat=[[[-5.27 + column * .005, 36.62 - row * .0045] for column in range(37)] for row in range(41)]),
        fuel_classes=[[4] * 37 for row in range(41)], fuel_valid_mask=[[1] * 37 for row in range(41)],
        study_mask=[[1] * 37 for row in range(41)], weather=dict(station='MA04 Estepona', source_clock='UTC', records=records),
        episode_pack=episode, episode_start_utc='2021-09-08T19:35:00Z', slope_enabled=False,
        historical_replay=False, provenance=[], assumptions=['Fixture, not measured data'])
    value['sha256'] = digest(value)
    return value


def rehash(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


def create(value=None, **kwargs):
    value = fixture_inputs() if value is None else value
    return PreparedDataEnv(value, expected_sha256=value['sha256'], allow_synthetic_conditions=True, **kwargs)


def complete(environment, identifier=CASE_IDS[0], policy=None):
    observation, _ = environment.reset(identifier)
    policy = ProtectionBaseline(prefer_shelter=False) if policy is None else policy
    while True:
        observation, _, terminated, truncated, info = environment.step(policy(observation))
        if info['error']:
            raise AssertionError(info['error'])
        if terminated or truncated:
            return environment


class PreparedDevelopmentTests(unittest.TestCase):
    def test_requires_pin_and_opt_in(self):
        value = fixture_inputs()
        with self.assertRaises(ValueError):
            PreparedDataEnv(value, expected_sha256=value['sha256'])
        with self.assertRaises(ValueError):
            PreparedDataEnv(value, expected_sha256='0' * 64, allow_synthetic_conditions=True)

    def test_validation_rejects_private_fields_masks_clocks_and_slope(self):
        changes = [lambda value: value.update(final_perimeter=[]),
                   lambda value: value.update(slope_enabled=True),
                   lambda value: value.update(episode_start_utc='2021-09-08T21:35:00Z'),
                   lambda value: value['fuel_valid_mask'][0].__setitem__(0, 0),
                   lambda value: value['grid'].update(width=36),
                   lambda value: value['weather']['records'][0].update(wind_speed_m_s=float('nan')),
                   lambda value: value['weather']['records'].reverse()]
        for mutate in changes:
            value = fixture_inputs()
            mutate(value)
            with self.assertRaises(ValueError):
                rehash(value)
                validate_inputs(value, value['sha256'])

    def test_loader_rejects_duplicate_json_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            value = fixture_inputs()
            path.write_text(json.dumps(value))
            self.assertEqual(load_inputs(path, expected_sha256=value['sha256']), value)
            link = Path(directory) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                load_inputs(link, expected_sha256=value['sha256'])
            for raw in ('{"clock":1,"clock":2}', ' ' * 2_000_001):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    load_inputs(path, expected_sha256=value['sha256'])

    def test_grid_fuel_mask_coordinates_and_disabled_slope(self):
        value = fixture_inputs()
        value['fuel_classes'][0][:3] = [1, 8, 0]
        value['fuel_valid_mask'][0][2] = 0
        value['study_mask'][0][3] = 0
        rehash(value)
        environment = create(value)
        observation, _ = environment.reset()
        self.assertEqual((environment._core.sim.width, environment._core.sim.height), (37, 41))
        self.assertEqual(observation['geography']['crs'], 'EPSG:25830')
        self.assertEqual(environment.cell_lon_lat(14, 20), value['grid']['centre_lon_lat'][20][14])
        for point in [(-1, 0), (37, 0), (0, 41), (True, 1)]:
            with self.assertRaises(ValueError):
                environment.cell_lon_lat(*point)
        cells = environment._core.sim.cells[0]
        self.assertEqual([cell['terrain'] for cell in cells[:4]], ['field', 'woodland', 'scrub', 'bare'])
        self.assertEqual(cells[0]['initial_fuel'], FUEL_MAPPING[1]['initial_fuel'])
        self.assertEqual(cells[2]['initial_fuel'], 1.0)
        self.assertEqual(observation['landscape']['fuel_valid_mask'][0][2], 0)
        self.assertFalse(observation['capabilities']['slope_effects'])
        for resource in observation['sectors']:
            self.assertTrue(0 <= resource['center'][0] < 37 and 0 <= resource['center'][1] < 41)

    def test_weather_is_causal_delayed_context_and_never_engine_wind(self):
        value = fixture_inputs()
        environment = create(value)
        observation, _ = environment.reset()
        records = observation['weather_context']['records']
        self.assertEqual(records[-1]['observed_at_utc'], '2021-09-08T19:30:00Z')
        self.assertEqual(records[-1]['received_at_utc'], '2021-09-08T19:35:00Z')
        self.assertEqual(observation['wind'], [1, 0])
        future = copy.deepcopy(value)
        future['weather']['records'][5]['wind_speed_m_s'] = 120.0
        rehash(future)
        other, _ = create(future).reset()
        self.assertEqual(observation, other)
        for index in range(15):
            observation, *_ = environment.step({})
        self.assertEqual(observation['weather_context']['records'][-1]['observed_at_utc'], '2021-09-08T20:00:00Z')
        self.assertFalse(observation['weather_context']['physics_coupled'])

    def test_invalid_atomicity_limits_and_isolation(self):
        environment = create()
        observation, _ = environment.reset()
        before = copy.deepcopy(observation['resources'])
        for index in range(3):
            observation, _, _, truncated, info = environment.step({'private_truth': True})
            self.assertIsNotNone(info['error'])
            self.assertEqual(observation['minute'], 2 * (index + 1))
            self.assertEqual(observation['resources'], before)
        self.assertTrue(truncated)
        first, _ = environment.reset()
        second, _ = create().reset()
        self.assertEqual(first, second)
        first['landscape']['fuel_classes'][0][0] = 999
        self.assertNotEqual(environment.get_situation()['landscape']['fuel_classes'][0][0], 999)

    def test_false_alarm_has_no_hidden_ignition_or_future_report(self):
        environment = create()
        observation, _ = environment.reset(CASE_IDS[1])
        self.assertFalse(any(environment._core.sim.burning(cell) for row in environment._core.sim.cells for cell in row))
        self.assertEqual([report['id'] for report in observation['reports']], ['claim-0'])
        self.assertNotIn('events', observation)
        self.assertNotIn('family', observation)
        self.assertNotIn('provenance', observation)

    def test_complete_episode_exact_reward_replay_and_tamper_rejection(self):
        environment = complete(create(), CASE_IDS[1])
        result = environment.verify_episode()
        self.assertTrue(result['passed'])
        trace = environment.trajectory()
        trace = json.loads(json.dumps(trace))
        self.assertEqual(sum(item['objective_reward'] for item in trace['transitions']), environment.reward)
        self.assertEqual(verify_prepared_trajectory(trace, fixture_inputs())['total_score'], environment.reward)
        repeated = complete(create(), CASE_IDS[1]).trajectory()
        self.assertEqual(trace, repeated)
        trace['transitions'][0]['objective_reward'] = 999
        with self.assertRaises(ValueError):
            verify_prepared_trajectory(trace, fixture_inputs())

    def test_timely_protection_outperforms_waiting(self):
        waiting = complete(create(), policy=lambda observation: {})
        active = complete(create())
        self.assertGreater(active.reward, waiting.reward)
        self.assertEqual(sum(group['exposed'] for group in active.get_situation()['districts']), 0)
        self.assertGreater(sum(group['exposed'] for group in waiting.get_situation()['districts']), 0)

    def test_false_alarm_restraint_outperforms_blanket_evacuation(self):
        baseline = ProtectionBaseline(prefer_shelter=False)
        def blanket(observation):
            action = baseline(observation)
            if observation['minute'] == 0:
                action['protection_orders'] = [dict(recipient=group['id'], action='evacuate',
                    channel='broadcast', resource_id=None, supersedes=None) for group in observation['districts']]
            return action
        restraint = complete(create(), CASE_IDS[1])
        indiscriminate = complete(create(), CASE_IDS[1], policy=blanket)
        self.assertGreater(restraint.reward, indiscriminate.reward)
        self.assertGreater(indiscriminate._core.protection.unsupported_orders, 0)

    def test_duplicate_protection_notices_cannot_improve_reward(self):
        baseline = ProtectionBaseline(prefer_shelter=False)
        def repeated(observation):
            action = baseline(observation)
            if observation['minute'] == 2:
                prior = next(record for record in observation['protection_orders']
                             if record['recipient'] == 'settlement-west')
                action['protection_orders'] = [dict(recipient=prior['recipient'], action=prior['action'],
                    channel='broadcast', resource_id=None, supersedes=prior['id'])]
            return action
        normal = complete(create())
        duplicate = complete(create(), policy=repeated)
        self.assertLessEqual(duplicate.reward, normal.reward)
        self.assertGreater(duplicate._core.protection.redundant_orders, 0)


if __name__ == '__main__':
    unittest.main()
