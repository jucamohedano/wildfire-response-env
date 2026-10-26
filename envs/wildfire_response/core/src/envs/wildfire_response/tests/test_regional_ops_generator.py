import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wildfire_core import OpsControlCenterEnv, verify_ops_trajectory
from wildfire_core.ops import ControlBaseline
from envs.wildfire_response.regional.ops.baselines import notice_needed
from envs.wildfire_response.regional.ops.dataset import audit_catalog, build, evaluate
from envs.wildfire_response.regional.ops.generator import (
    SPLITS, case_ids, configuration_groups, get_generated_task, validate_scenario, verify_catalog_lock,
)
from envs.wildfire_response.regional.ops.run import run_episode
from envs.wildfire_response.regional.ops.scenario_core import ScenarioResponseEnv
from envs.wildfire_response.regional.ops.tasks import get_ops_task


def transport_core():
    scenario = get_generated_task('control-train-002-v0')['scenario']
    scenario['transport'] = dict(message_extra_delay_minutes=2, request_extra_delay_minutes=4,
        fail_first_evacuation=True, resource_unavailable_until={'engine-valverde': 6})
    env = ScenarioResponseEnv(ticks_per_action=1)
    env.reset(scenario=scenario)
    return env


class GeneratedCatalogTests(unittest.TestCase):
    def test_configuration_groups_seeds_and_all_families_are_disjoint_by_split(self):
        audit = audit_catalog()
        self.assertTrue(audit['passed'])
        self.assertEqual(audit['groups'], 120)
        self.assertEqual(audit['cases'], 360)
        self.assertEqual(audit['operational_configurations'], 360)
        groups = configuration_groups()
        signatures = set()
        seeds = set()
        for split, count in SPLITS.items():
            selected = [group for group in groups if group['split'] == split]
            self.assertEqual(len(selected), count)
            self.assertEqual(len({group['factors']['family'] for group in selected}), 6)
            for group in selected:
                self.assertNotIn(group['configuration_sha256'], signatures)
                signatures.add(group['configuration_sha256'])
            for identifier in case_ids(split):
                task = get_generated_task(identifier)
                self.assertEqual(task['split'], split)
                self.assertNotIn(task['seed'], seeds)
                seeds.add(task['seed'])
        self.assertEqual(len(signatures), 120)
        self.assertEqual(len(seeds), 360)
        self.assertEqual(len(verify_catalog_lock()), 64)

    def test_generation_is_deterministic_isolated_and_does_not_override_pinned_seed(self):
        first = get_generated_task('control-train-000-v0')
        changed = copy.deepcopy(first)
        changed['scenario']['reports'][0]['text'] = 'caller mutation'
        self.assertEqual(first, get_generated_task(first['id']))
        self.assertEqual(first, get_ops_task(first['id']))
        env = OpsControlCenterEnv()
        with self.assertRaisesRegex(ValueError, 'pin their seed'):
            env.reset(first['id'], first['seed'] + 1)
        self.assertIsNone(env._core.sim)
        with self.assertRaisesRegex(ValueError, 'does not match its configuration split'):
            get_generated_task('control-test-000-v0')

    def test_structural_validator_rejects_illegal_locations_times_and_outages(self):
        for mutation in ('location', 'time', 'resource', 'wind'):
            scenario = get_generated_task('control-train-000-v0')['scenario']
            if mutation == 'location':
                scenario['reports'][0]['location'] = [-1, 3]
            elif mutation == 'time':
                scenario['reports'][0]['received_at'] = -1
            elif mutation == 'resource':
                scenario['transport']['resource_unavailable_until'] = {'invented-unit': 20}
            else:
                scenario['wind'] = [3.1, 0]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_scenario(scenario)

    def test_dataset_export_separates_public_metadata_and_private_configurations(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'dataset'
            manifest = build(output)
            self.assertEqual(manifest['cases'], 360)
            self.assertFalse(manifest['test_outcomes_evaluated'])
            for split, count in (('train', 240), ('validation', 60), ('test', 60)):
                rows = [json.loads(line) for line in (output / f'{split}.jsonl').read_text().splitlines()]
                self.assertEqual(len(rows), count)
                for row in rows:
                    self.assertTrue(row['synthetic'])
                    self.assertFalse({'scenario', 'events', 'reports', 'transport'}.intersection(row))
                self.assertTrue((output / 'private' / f'{split}.jsonl').is_file())
            with self.assertRaisesRegex(ValueError, 'fresh dataset directory'):
                build(output)

    def test_test_outcomes_are_not_evaluated_without_explicit_consumption(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                'envs.wildfire_response.regional.ops.dataset.run_episode',
                side_effect=AssertionError('Test policy evaluation must not run')):
            with self.assertRaisesRegex(ValueError, 'Test outcomes are sealed'):
                evaluate(Path(directory) / 'test', 'test')
            self.assertFalse((Path(directory) / 'test').exists())

    def test_cpu_evaluation_resumes_verified_records_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'eval'
            first = evaluate(output, 'train', ['control'], max_groups=1, variants=1)
            self.assertEqual(first['completed'], 1)
            self.assertTrue(first['all_verified'])
            with patch('envs.wildfire_response.regional.ops.dataset.run_episode',
                       side_effect=AssertionError('Cached episode must not rerun')):
                resumed = evaluate(output, 'train', ['control'], max_groups=1, variants=1, resume=True)
            self.assertEqual(resumed['reused'], 1)
            record_path = output / 'episodes' / 'control-train-000-v0' / 'control.json'
            record = json.loads(record_path.read_text())
            record['summary']['objective_score'] = 999
            record_path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'corrupt or inconsistent'):
                evaluate(output, 'train', ['control'], max_groups=1, variants=1, resume=True)


class TransportScenarioTests(unittest.TestCase):
    def test_failure_does_not_move_people_and_retry_delivers_only_after_its_delay(self):
        env = transport_core()
        warning = dict(messages=[dict(recipient='settlement-west', purpose='evacuate')])
        env.step(warning)
        self.assertEqual(env.observe()['message_deliveries'][0]['status'], 'pending')
        while env.sim.tick < 4:
            env.step({})
        self.assertEqual(env.observe()['message_deliveries'][0]['status'], 'failed')
        self.assertEqual(env.sim.groups['settlement-west']['status'], 'unwarned')
        env.step(warning)
        while env.sim.tick < 8:
            self.assertEqual(env.sim.groups['settlement-west']['status'], 'unwarned')
            env.step({})
        self.assertEqual(env.observe()['message_deliveries'][1]['status'], 'delivered')
        self.assertEqual(env.sim.groups['settlement-west']['status'], 'evacuating')
        self.assertEqual(env.counters['redundant_notices'], 0)

    def test_unavailable_units_release_at_their_scheduled_time(self):
        env = transport_core()
        self.assertEqual(env.resources['engine-valverde']['state'], 'unavailable')
        for _ in range(5):
            env.step({})
        self.assertEqual(env.resources['engine-valverde']['state'], 'unavailable')
        env.step({})
        self.assertEqual(env.resources['engine-valverde']['state'], 'available')
        self.assertTrue(any(ack['kind'] == 'availability_update' for ack in env.acks))

    def test_delayed_requests_are_publicly_pending_and_cannot_be_sent_twice(self):
        env = transport_core()
        request = dict(requests=[dict(resource_id='engine-cabezudos', sector_id='report:report-0', mission='recon')])
        env.step(request)
        self.assertIn('engine-cabezudos', env.observe()['pending_resource_requests'])
        self.assertFalse(env.check_action(request)['valid'])
        while env.sim.tick < 5:
            self.assertEqual(env.observe()['offers'], [])
            env.step({})
        self.assertEqual(env.observe()['offers'][0]['expires_at'], 13)
        self.assertEqual(env.observe()['pending_resource_requests'], [])

    def test_future_failures_and_resource_release_times_are_not_in_observations(self):
        env = transport_core()
        env.step(dict(messages=[dict(recipient='settlement-west', purpose='evacuate')]))
        public = json.dumps(env.observe())
        for field in ('will_fail', 'fail_first_evacuation', 'resource_unavailable_until', 'transport'):
            self.assertNotIn(field, public)
        env.reset(scenario=get_generated_task('control-train-000-v0')['scenario'])
        self.assertEqual(env.observe()['message_deliveries'], [])

    def test_acknowledged_failure_retry_is_not_a_redundant_notice_and_replays(self):
        env = OpsControlCenterEnv()
        env.reset('control-train-002-v0')
        warning = dict(messages=[dict(recipient='settlement-west', purpose='evacuate')])
        observation, *_ = env.submit_action(warning)
        while observation['message_deliveries'][0]['status'] == 'pending':
            observation, *_ = env.advance()
        self.assertTrue(notice_needed(observation, 'settlement-west', 'evacuate'))
        observation, *_ = env.submit_action(warning)
        self.assertFalse(notice_needed(observation, 'settlement-west', 'evacuate'))
        policy = ControlBaseline()
        while True:
            observation, _, terminated, truncated, _ = env.submit_action(policy(observation))
            if terminated or truncated:
                break
        result = env.verify_episode()
        self.assertEqual(result['evaluator_evidence']['redundant_notices'], 0)
        self.assertTrue(result['objective_result']['by_kind']['communication_discipline'])
        self.assertTrue(verify_ops_trajectory(env.trajectory())['verified'])

    def test_generated_episode_seeded_replay_and_scenario_hash_binding(self):
        first, summary, _ = run_episode('control-train-000-v0', 'control')
        second, _, _ = run_episode('control-train-000-v0', 'control')
        self.assertEqual(first, second)
        self.assertEqual(summary['split'], 'train')
        self.assertEqual(summary['invalid_actions'], 0)
        first['scenario_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'scenario_sha256'):
            verify_ops_trajectory(first)


if __name__ == '__main__':
    unittest.main()
