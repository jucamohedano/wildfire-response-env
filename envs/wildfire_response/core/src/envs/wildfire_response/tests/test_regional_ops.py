import json
import unittest

from wildfire_core import (OPS_REWARD_VERSION, OpsControlCenterEnv,
                           WildfireResponseEnv, verify_ops_trajectory)
from envs.wildfire_response.regional.environment import VERSION as CORE_VERSION
from envs.wildfire_response.regional.ops.tasks import get_ops_task


def resource_request(resource_id='engine-valverde'):
    return [dict(resource_id=resource_id, sector_id='report:report-0', mission='recon')]


class OpsControlCenterTests(unittest.TestCase):
    def test_public_core_exports_preserve_both_interfaces(self):
        self.assertTrue(callable(OpsControlCenterEnv))
        self.assertTrue(callable(WildfireResponseEnv))
        self.assertEqual(OPS_REWARD_VERSION, 'wildfire-ops-objective-fraction-v1')
        self.assertTrue(callable(verify_ops_trajectory))

    def setUp(self):
        self.env = OpsControlCenterEnv(ticks_per_action=1)
        self.initial, self.info = self.env.reset('ops-000', 20000)

    def test_public_reads_are_copied_and_do_not_advance(self):
        initial_tick = self.env._core.sim.tick
        initial_decision = self.env._core.decisions
        situation = self.env.get_situation()
        reports = self.env.get_reports()
        reports[0]['text'] = 'caller mutation'
        situation['resources'][0]['state'] = 'caller mutation'

        self.assertEqual(self.env._core.sim.tick, initial_tick)
        self.assertEqual(self.env._core.decisions, initial_decision)
        self.assertNotEqual(self.env.get_reports()[0]['text'], 'caller mutation')
        self.assertNotEqual(self.env.get_situation()['resources'][0]['state'], 'caller mutation')

    def test_verify_episode_requires_reset_and_terminal_episode(self):
        env = OpsControlCenterEnv()
        with self.assertRaisesRegex(RuntimeError, 'reset required'):
            env.verify_episode()
        env.reset('ops-000', 20)
        with self.assertRaisesRegex(RuntimeError, 'terminate or truncate'):
            env.verify_episode()

    def test_reset_rejects_objective_deadline_beyond_horizon(self):
        short = OpsControlCenterEnv(max_decisions=10, ticks_per_action=2)
        with self.assertRaisesRegex(ValueError, 'fit within the episode horizon'):
            short.reset('ops-000', 21)

    def test_trace_captures_reward_and_data_provenance(self):
        trace = self.env.trajectory()
        self.assertEqual(trace['reward_version'], 'wildfire-ops-objective-fraction-v1')
        self.assertEqual(trace['core_version'], CORE_VERSION)
        self.assertEqual(len(trace['core_pack_sha256']), 64)
        self.assertEqual(len(trace['task_manifest_sha256']), 64)

    def test_presentation_uses_ops_role_and_hides_private_evaluator(self):
        situation = self.env.get_situation()
        serialized = json.dumps(situation)

        self.assertEqual(situation['authority_role'], 'ops')
        self.assertEqual(situation['rules']['authority_role'], 'ops')
        self.assertIn('simulated wildfire ops control center', situation['rules']['authority'])
        self.assertIn('ops authorization', situation['rules']['dispatch'])
        self.assertNotIn('director', serialized)
        self.assertNotIn('private_annotations', serialized)
        self.assertNotIn('fire_checkpoint', serialized)
        self.assertFalse(hasattr(self.env, 'evaluator_state'))

    def test_check_orders_does_not_advance_time(self):
        result = self.env.check_orders(dict(requests=resource_request()))

        self.assertTrue(result['valid'])
        self.assertEqual(self.env._core.sim.tick, 0)
        self.assertEqual(self.env._core.decisions, 0)

    def test_request_and_authorize_each_commit_exactly_one_core_step(self):
        observation, reward, terminated, truncated, info = self.env.request_resources(
            resource_request())
        self.assertEqual(self.env._core.decisions, 1)
        self.assertEqual(self.env._core.sim.tick, 1)
        self.assertIsNone(info['error'])
        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, 0.0)
        offer_id = observation['offers'][0]['id']

        result = self.env.authorize_resources([offer_id])
        self.assertEqual(len(result), 5)
        self.assertEqual(self.env._core.decisions, 2)
        self.assertEqual(self.env._core.sim.tick, 2)
        self.assertIsNone(result[4]['error'])
        self.assertEqual(self.env._core.resources['engine-valverde']['state'], 'mobilizing')
        self.assertTrue(all(ack['source'] != 'director' and ack['recipient'] != 'director'
                            for ack in result[0]['acknowledgements']))

    def test_release_resources_commits_once_and_returns_assigned_unit(self):
        offer_observation, *_ = self.env.request_resources(resource_request())
        offer_id = offer_observation['offers'][0]['id']
        self.env.authorize_resources([offer_id])

        result = self.env.release_resources(['engine-valverde'])

        self.assertEqual(len(result), 5)
        self.assertEqual(self.env._core.decisions, 3)
        self.assertEqual(self.env._core.sim.tick, 3)
        self.assertIsNone(result[4]['error'])
        self.assertEqual(self.env._core.resources['engine-valverde']['state'], 'returning')

    def test_invalid_committed_action_is_atomic_but_advances_once(self):
        result = self.env.authorize_resources(['unknown-offer'])

        self.assertEqual(len(result), 5)
        self.assertEqual(self.env._core.decisions, 1)
        self.assertEqual(self.env._core.sim.tick, 1)
        self.assertIsNotNone(result[4]['error'])
        self.assertTrue(all(resource['state'] in ('available', 'unavailable')
                            for resource in result[0]['resources']))

    def test_invalid_free_text_is_not_saved_in_public_trajectory(self):
        private_text = 'caller-supplied diagnostic text'
        result = self.env.submit_action({'unexpected_note': private_text})

        self.assertIsNotNone(result[4]['error'])
        trajectory = self.env.trajectory()
        self.assertEqual(trajectory['transitions'][0]['action'], {'_invalid_ops_action': True})
        self.assertFalse(trajectory['transitions'][0]['action_valid'])
        self.assertNotIn(private_text, json.dumps(trajectory))

    def test_free_text_explanation_is_rejected_before_time_advances(self):
        with self.assertRaisesRegex(ValueError, 'do not accept free-text'):
            self.env.submit_action({'requests': [], 'explanation': 'sensitive narrative'})
        self.assertEqual(self.env._core.decisions, 0)

    def test_send_messages_uses_core_delay_and_ops_acknowledgement(self):
        result = self.env.send_messages([
            dict(recipient='settlement-west', purpose='inform')])

        self.assertEqual(self.env._core.decisions, 1)
        self.assertEqual(result[0]['pending_deliveries'], [dict(kind='message', due_at=2)])
        self.assertEqual(self.env._core.sim.groups['settlement-west']['status'], 'unwarned')
        self.env.advance()
        situation = self.env.get_situation()
        delivery = next(ack for ack in situation['acknowledgements']
                        if ack['kind'] == 'message_delivered')
        self.assertEqual(delivery['recipient'], 'ops')
        self.assertEqual(delivery['effect'], 'district_informed')
        self.assertEqual(self.env._core.decisions, 2)

    def test_submit_action_is_single_combined_commit(self):
        result = self.env.submit_action(dict(requests=resource_request()))

        self.assertEqual(len(result), 5)
        self.assertEqual(self.env._core.decisions, 1)
        self.assertEqual(self.env._core.sim.tick, 1)
        self.assertIsNone(result[4]['error'])

    def test_objective_score_is_earned_only_when_a_resource_arrives(self):
        self.env.reset('ops-002', 20002)
        result = self.env.request_resources(resource_request())
        self.assertEqual(result[1], 0.0)
        offer_id = result[0]['offers'][0]['id']
        result = self.env.authorize_resources([offer_id])
        self.assertEqual(result[1], 0.0)

        while self.env._core.resources['engine-valverde']['state'] != 'on_scene':
            result = self.env.advance()
            expected_reward = 1.0 if self.env._core.resources['engine-valverde']['state'] == 'on_scene' else 0.0
            self.assertEqual(result[1], expected_reward)
        self.assertEqual(result[0]['task_objectives'], get_ops_task('ops-002')['objectives'])
        self.assertEqual(result[4]['objectives_completed'], ['arrive-report-0'])
        self.assertEqual(result[1], 1.0)

        self.env.release_resources(['engine-valverde'])
        while not self.env._core.terminated and not self.env._core.truncated:
            self.env.advance()
        self.assertEqual(self.env.reward, 1.0)
        verification = self.env.verify_episode()
        self.assertTrue(verification['passed'])
        self.assertEqual(verification['objective_score'], 1.0)
        self.assertTrue(verification['core_accounting']['passed'])

    def test_objective_deadlines_align_with_action_boundaries(self):
        env = OpsControlCenterEnv(ticks_per_action=3)
        with self.assertRaisesRegex(ValueError, 'decision interval'):
            env.reset('ops-000', 20000)
        self.assertIsNone(env._core.sim)

    def test_task_brief_includes_public_objectives(self):
        self.assertEqual(self.initial['task_objectives'], get_ops_task('ops-000')['objectives'])
        self.assertEqual(self.initial['task_id'], 'ops-000')
        self.assertEqual(self.initial['task_version'], 'wildfire-ops-task-pack-v0.1.0')
        self.assertEqual(self.initial['ops_interface_version'], 'wildfire-ops-control-center-v1')
        self.assertEqual(self.info['reward_version'], OPS_REWARD_VERSION)

    def test_reset_rejects_unknown_task_before_mutating_episode(self):
        initial_minute = self.env.get_situation()['minute']
        with self.assertRaisesRegex(ValueError, 'Unknown ops task'):
            self.env.reset('ops-999', 20000)
        self.assertEqual(self.env.get_situation()['minute'], initial_minute)


if __name__ == '__main__':
    unittest.main()
