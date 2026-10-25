from functools import cache
import json
import unittest

from wildfire_core import OpsControlCenterEnv
from envs.wildfire_response.regional.ops.objectives import (
    REWARD_VERSION,
    objective_step,
    validate_objectives,
    verify_ops_trajectory,
)
from envs.wildfire_response.regional.ops.tasks import OPS_TASK_VERSION, get_ops_task


def objective(identifier='recon-west', **overrides):
    record = {
        'id': identifier,
        'kind': 'resource_arrival',
        'sector_id': 'sector-west',
        'acceptable_resource_kinds': ['engine'],
        'acceptable_missions': ['recon'],
        'deadline_minute': 10,
    }
    record.update(overrides)
    return record


def observation(minute, resources=()):
    return {'minute': minute, 'resources': list(resources)}


def on_scene(kind='engine', mission='recon', sector_id='sector-west'):
    return {'state': 'on_scene', 'kind': kind, 'mission': mission, 'sector_id': sector_id}


@cache
def _completed_ops_002_json():
    env = OpsControlCenterEnv(ticks_per_action=2)
    env.reset('ops-002', 20002)
    result = env.request_resources([
        {'resource_id': 'engine-valverde', 'sector_id': 'report:report-0', 'mission': 'recon'}
    ])
    offer_id = result[0]['offers'][0]['id']
    env.submit_action(dict(authorizations=[offer_id],
        readback_confirmations=[result[0]['readbacks'][0]['id']]))
    while env._core.resources['engine-valverde']['state'] != 'on_scene':
        env.advance()
    env.release_resources(['engine-valverde'])
    while not env._core.terminated and not env._core.truncated:
        env.advance()
    return json.dumps(env.trajectory())


def completed_ops_002_trajectory():
    return json.loads(_completed_ops_002_json())


class OpsObjectiveTests(unittest.TestCase):
    def test_cached_trajectory_returns_independent_mutable_copies(self):
        first = completed_ops_002_trajectory()
        expected = completed_ops_002_trajectory()
        first['transitions'][0]['observation']['resources'][0]['state'] = 'tampered'
        first['objectives'].clear()
        self.assertEqual(completed_ops_002_trajectory(), expected)

    def test_success_and_reward_version(self):
        newly_completed, reward = objective_step(
            observation(10, [on_scene()]), [objective()], [])

        self.assertEqual(REWARD_VERSION, 'wildfire-ops-objective-fraction-v3')
        self.assertEqual(newly_completed, ['recon-west'])
        self.assertEqual(reward, 1.0)

    def test_deadline_miss(self):
        newly_completed, reward = objective_step(
            observation(11, [on_scene()]), [objective()], [])

        self.assertEqual(newly_completed, [])
        self.assertEqual(reward, 0.0)

    def test_one_of_many_objectives_sets_fraction(self):
        objectives = [objective(), objective('recon-east', sector_id='sector-east')]

        newly_completed, reward = objective_step(
            observation(5, [on_scene()]), objectives, [])

        self.assertEqual(newly_completed, ['recon-west'])
        self.assertEqual(reward, 0.5)

    def test_multiple_acceptable_resource_kinds_and_missions(self):
        task = objective(acceptable_resource_kinds=['engine', 'aircraft'],
                         acceptable_missions=['recon', 'suppress'])

        newly_completed, reward = objective_step(
            observation(4, [on_scene(kind='aircraft', mission='suppress')]), [task], [])

        self.assertEqual(newly_completed, ['recon-west'])
        self.assertEqual(reward, 1.0)

    def test_repeated_observations_do_not_duplicate_credit(self):
        tasks = [objective()]
        completed, first_reward = objective_step(observation(4, [on_scene()]), tasks, [])
        repeated, second_reward = objective_step(observation(5, [on_scene()]), tasks, completed)

        self.assertEqual(first_reward, 1.0)
        self.assertEqual(repeated, [])
        self.assertEqual(second_reward, 0.0)

    def test_schema_rejects_unknown_fields_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, 'unknown field'):
            validate_objectives([objective(unexpected='value')])
        with self.assertRaisesRegex(ValueError, 'duplicate objective id'):
            validate_objectives([objective(), objective()])

    def test_schema_rejects_duplicate_predicates_with_different_ids(self):
        with self.assertRaisesRegex(ValueError, 'duplicates an existing objective predicate'):
            validate_objectives([objective(), objective('same-goal')])

    def test_schema_rejects_overlapping_arrival_predicates(self):
        overlapping = objective('broader-goal', acceptable_resource_kinds=['engine', 'aircraft'])
        with self.assertRaisesRegex(ValueError, 'could duplicate credit'):
            validate_objectives([objective(), overlapping])

    def test_deadline_rejects_bool_zero_and_negative_values(self):
        for deadline in (True, 0, -1):
            with self.subTest(deadline=deadline):
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    validate_objectives([objective(deadline_minute=deadline)])

    def test_trajectory_rejects_non_monotonic_time(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['transitions'][1]['observation']['minute'] = -1

        with self.assertRaisesRegex(ValueError, 'does not replay'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_fabricated_public_observation(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['transitions'][0]['observation']['resources'][0]['state'] = 'on_scene'

        with self.assertRaisesRegex(ValueError, 'observation does not replay'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_tampered_reward_and_reports_summary(self):
        trajectory = completed_ops_002_trajectory()
        result = verify_ops_trajectory(trajectory)
        self.assertEqual(result['total_score'], 1.0)
        self.assertEqual(result['completed_ids'], ['arrive-report-0'])
        self.assertTrue(result['verified'])
        self.assertTrue(result['core_replayed'])

        scored = next(item for item in trajectory['transitions'] if item['objective_reward'] > 0)
        scored['objective_reward'] = 0.0
        with self.assertRaisesRegex(ValueError, 'objective_reward mismatch'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_repeat_credit_even_when_scene_persists(self):
        trajectory = completed_ops_002_trajectory()
        scored = next(index for index, item in enumerate(trajectory['transitions'])
                      if item['objective_reward'] > 0)
        trajectory['transitions'][scored+1]['objective_reward'] = 1.0

        with self.assertRaisesRegex(ValueError, 'objective_reward mismatch'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_requires_manifest_fields(self):
        for field in ('interface_version', 'task_id', 'task_set', 'task_version', 'task_manifest_sha256',
                      'communication_protocol_version',
                      'evidence_version',
                      'scenario_engine_version', 'scenario_sha256',
                      'reward_version', 'core_version', 'core_reward_version',
                      'core_pack_sha256', 'scenario_id', 'seed', 'max_decisions',
                      'ticks_per_action', 'objectives', 'initial_observation'):
            trajectory = completed_ops_002_trajectory()
            del trajectory[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_unknown_task_id(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['task_id'] = 'ops-999'

        with self.assertRaisesRegex(ValueError, 'unknown ops task'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_wrong_scenario_id(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['scenario_id'] = 'regional-001'

        with self.assertRaisesRegex(ValueError, 'scenario_id mismatch'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_changed_objective_list(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['objectives'][0]['deadline_minute'] += 1

        with self.assertRaisesRegex(ValueError, 'do not match the canonical task manifest'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_wrong_task_version(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['task_version'] = 'wildfire-ops-task-pack-v0.0.0'

        with self.assertRaisesRegex(ValueError, 'task_version mismatch'):
            verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_wrong_provenance_hashes(self):
        for field in ('task_manifest_sha256', 'core_pack_sha256'):
            trajectory = completed_ops_002_trajectory()
            trajectory[field] = '0' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                verify_ops_trajectory(trajectory)

    def test_trajectory_rejects_horizon_that_cannot_meet_deadline(self):
        trajectory = completed_ops_002_trajectory()
        trajectory['max_decisions'] = 1
        with self.assertRaisesRegex(ValueError, 'deadlines exceed'):
            verify_ops_trajectory(trajectory)

    def test_empty_objective_lists_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-empty list'):
            validate_objectives([])
        with self.assertRaisesRegex(ValueError, 'non-empty list'):
            objective_step(observation(0), [], [])
        with self.assertRaisesRegex(ValueError, 'non-empty list'):
            trajectory = completed_ops_002_trajectory()
            trajectory['objectives'] = []
            verify_ops_trajectory(trajectory)


if __name__ == '__main__':
    unittest.main()
