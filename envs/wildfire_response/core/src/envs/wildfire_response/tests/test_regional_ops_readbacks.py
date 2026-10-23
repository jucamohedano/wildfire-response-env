import copy
import json
import tempfile
import unittest
from pathlib import Path

from wildfire_core import OpsControlCenterEnv, verify_ops_trajectory
from envs.wildfire_response.regional.ops.baselines import DispatchBaseline
from envs.wildfire_response.regional.ops.run import run_episode, run_suite
from envs.wildfire_response.eval.run import load


def request_offer(env):
    return env.request_resources([dict(resource_id='engine-valverde',
        sector_id='report:report-0', mission='recon')])[0]


def correction_for(observation):
    offer = observation['offers'][0]
    return dict(readback_id=observation['readbacks'][-1]['id'],
                **{field: offer[field] for field in ('resource_id', 'sector_id', 'mission')})


def authorize_action(observation):
    return dict(authorizations=[observation['offers'][0]['id']],
                readback_confirmations=[observation['readbacks'][-1]['id']])


class OpsReadbackTests(unittest.TestCase):
    def setUp(self):
        self.env = OpsControlCenterEnv(ticks_per_action=2)
        self.env.reset('ops-000', 20000)

    def test_offer_readback_is_public_and_does_not_dispatch(self):
        observation = request_offer(self.env)
        readback = observation['readbacks'][0]
        self.assertEqual(readback['source'], 'valverde')
        self.assertEqual(readback['recipient'], 'ops')
        self.assertEqual(readback['status'], 'awaiting_confirmation')
        self.assertLessEqual(readback['received_at'], observation['minute'])
        self.assertEqual(observation['resources'][0]['state'], 'reserved')
        self.assertEqual(self.env.reward, 0)

    def test_coarse_decision_interval_is_rejected_before_starting_an_episode(self):
        env = OpsControlCenterEnv(ticks_per_action=5)
        with self.assertRaisesRegex(ValueError, 'correct a readback before offer expiry'):
            env.reset('ops-001')
        self.assertIsNone(env._core.sim)

    def test_unconfirmed_authorization_advances_without_dispatching(self):
        observation = request_offer(self.env)
        result = self.env.authorize_resources([observation['offers'][0]['id']])
        self.assertIn('confirmed readback', result[4]['error'])
        self.assertEqual(result[0]['minute'], observation['minute'] + 2)
        self.assertEqual(result[0]['resources'][0]['state'], 'reserved')
        self.assertEqual(result[0]['readbacks'][0]['status'], 'awaiting_confirmation')

    def test_confirm_and_authorize_can_share_one_committed_action(self):
        observation = request_offer(self.env)
        result = self.env.submit_action(authorize_action(observation))
        self.assertIsNone(result[4]['error'])
        self.assertEqual(result[0]['readbacks'][0]['status'], 'confirmed')
        self.assertEqual(result[0]['resources'][0]['state'], 'mobilizing')
        self.assertEqual(result[0]['decision'], 2)
        self.assertEqual(result[1], 0)

    def test_separate_confirmation_does_not_dispatch(self):
        observation = request_offer(self.env)
        result = self.env.confirm_readbacks([observation['readbacks'][0]['id']])
        self.assertIsNone(result[4]['error'])
        self.assertEqual(result[0]['resources'][0]['state'], 'reserved')
        result = self.env.authorize_resources([observation['offers'][0]['id']])
        self.assertIsNone(result[4]['error'])
        self.assertEqual(result[0]['resources'][0]['state'], 'mobilizing')

    def test_bad_combined_action_does_not_partially_confirm(self):
        observation = request_offer(self.env)
        action = authorize_action(observation)
        action['messages'] = [dict(recipient='unknown', purpose='evacuate')]
        result = self.env.submit_action(action)
        self.assertIsNotNone(result[4]['error'])
        self.assertEqual(result[0]['readbacks'][0]['status'], 'awaiting_confirmation')
        self.assertEqual(result[0]['resources'][0]['state'], 'reserved')

    def test_mismatched_readback_requires_delayed_correction_then_confirmation(self):
        self.env.reset('ops-001', 20001)
        observation = request_offer(self.env)
        self.assertEqual(observation['offers'][0]['mission'], 'recon')
        self.assertEqual(observation['readbacks'][0]['mission'], 'suppress')
        result = self.env.submit_action(authorize_action(observation))
        self.assertIn('send a correction', result[4]['error'])
        sent_at = result[0]['minute']
        result = self.env.correct_readbacks([correction_for(result[0])])
        self.assertIsNone(result[4]['error'])
        self.assertEqual(result[0]['readbacks'][0]['status'], 'superseded')
        self.assertEqual(result[0]['readbacks'][1]['received_at'], sent_at + 1)
        self.assertEqual(result[0]['readbacks'][1]['mission'], 'recon')
        self.assertEqual(result[0]['resources'][0]['state'], 'reserved')
        self.assertEqual(result[1], 0)
        result = self.env.submit_action(authorize_action(result[0]))
        self.assertIsNone(result[4]['error'])
        self.assertEqual(result[0]['resources'][0]['mission'], 'recon')
        self.assertEqual(result[0]['resources'][0]['state'], 'mobilizing')
        self.assertEqual(result[1], 0)

    def test_correction_cannot_change_the_requested_allocation(self):
        self.env.reset('ops-001', 20001)
        observation = request_offer(self.env)
        correction = correction_for(observation)
        correction['sector_id'] = 'protect:settlement-west'
        result = self.env.correct_readbacks([correction])
        self.assertIn('restate the original', result[4]['error'])
        self.assertEqual(len(result[0]['readbacks']), 1)
        self.assertEqual(result[0]['readbacks'][0]['status'], 'awaiting_confirmation')

    def test_checks_are_read_only_and_include_the_readback_gate(self):
        observation = request_offer(self.env)
        self.assertFalse(self.env.check_orders(
            dict(authorizations=[observation['offers'][0]['id']]))['valid'])
        self.assertTrue(self.env.check_orders(authorize_action(observation))['valid'])
        self.assertEqual(self.env.get_situation(), observation)

    def test_expired_offer_and_duplicate_confirmation_cannot_be_authorized(self):
        observation = request_offer(self.env)
        duplicate = authorize_action(observation)
        duplicate['readback_confirmations'] *= 2
        result = self.env.submit_action(duplicate)
        self.assertIsNotNone(result[4]['error'])
        while self.env.get_situation()['minute'] < observation['offers'][0]['expires_at']:
            self.env.advance()
        self.assertEqual(self.env.get_situation()['readbacks'][0]['status'], 'closed')
        self.assertFalse(self.env.check_orders(authorize_action(observation))['valid'])

    def test_reset_isolates_readbacks_and_fault_schedule(self):
        self.env.reset('ops-001', 20001)
        first = request_offer(self.env)
        self.env.correct_readbacks([correction_for(first)])
        self.env.reset('ops-001', 20001)
        self.assertEqual(self.env.get_situation()['readbacks'], [])
        self.assertEqual(request_offer(self.env), first)
        isolated = OpsControlCenterEnv()
        isolated.reset('ops-001', 20001)
        self.assertEqual(isolated.get_situation()['readbacks'], [])

    def test_invalid_gate_attempt_and_correction_episode_replay(self):
        self.env.reset('ops-001', 20001)
        observation = request_offer(self.env)
        observation, *_ = self.env.submit_action(authorize_action(observation))
        policy = DispatchBaseline()
        while True:
            result = self.env.submit_action(policy(observation))
            observation = result[0]
            if result[2] or result[3]:
                break
        trace = json.loads(json.dumps(self.env.trajectory()))
        self.assertTrue(self.env.verify_episode()['passed'])
        self.assertEqual(verify_ops_trajectory(trace)['total_score'], 1)
        mutated = copy.deepcopy(trace)
        received = next(step for step in mutated['transitions']
                        if len(step['observation']['readbacks']) > 1)
        received['observation']['readbacks'][1]['received_at'] -= 1
        with self.assertRaisesRegex(ValueError, 'does not replay'):
            verify_ops_trajectory(mutated)
        trace['communication_protocol_version'] = 'unrecognized'
        with self.assertRaisesRegex(ValueError, 'communication_protocol_version'):
            verify_ops_trajectory(trace)


class OpsBaselineRolloutTests(unittest.TestCase):
    def test_dispatch_completes_faulted_task_and_seeded_rollouts_replay(self):
        first, summary, _ = run_episode('ops-001', 'dispatch')
        second, _, _ = run_episode('ops-001', 'dispatch')
        self.assertEqual(first, second)
        self.assertEqual(summary['objective_score'], 1)
        self.assertEqual(summary['corrections_sent'], 1)
        self.assertEqual(summary['invalid_actions'], 0)
        self.assertTrue(summary['verified'])
        self.assertGreater(summary['process_peak_rss_bytes'], 0)

    def test_random_valid_and_passive_use_the_same_verified_api(self):
        for policy in ('passive', 'random_valid'):
            trace, summary, verification = run_episode('ops-002', policy)
            self.assertEqual(summary['invalid_actions'], 0)
            self.assertTrue(verification['passed'])
            self.assertTrue(verify_ops_trajectory(trace)['verified'])
            if policy == 'passive':
                self.assertEqual(summary['objective_score'], 0)

    def test_cpu_runner_exports_replayable_public_trace_and_separate_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'suite'
            result = run_suite(output, ['ops-002'], ['dispatch'])
            self.assertEqual(result['episodes'], 1)
            trace = load(output / 'ops-002' / 'dispatch' / 'trajectory.json.gz')
            self.assertEqual(verify_ops_trajectory(trace)['total_score'], 1)
            self.assertNotIn('mission_swap_once', json.dumps(trace))
            self.assertTrue((output / 'evaluator' / 'task-manifest.json').is_file())
            with self.assertRaisesRegex(ValueError, 'fresh ops rollout directory'):
                run_suite(output, ['ops-002'], ['dispatch'])


if __name__ == '__main__':
    unittest.main()
