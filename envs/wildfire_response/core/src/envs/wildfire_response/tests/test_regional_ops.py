import json
import unittest

from wildfire_core import OpsControlCenterEnv, WildfireResponseEnv


def resource_request(resource_id='engine-valverde'):
    return [dict(resource_id=resource_id, sector_id='report:report-0', mission='recon')]


class OpsControlCenterTests(unittest.TestCase):
    def test_public_core_exports_preserve_both_interfaces(self):
        self.assertTrue(callable(OpsControlCenterEnv))
        self.assertTrue(callable(WildfireResponseEnv))

    def setUp(self):
        self.env = OpsControlCenterEnv(ticks_per_action=1)
        self.initial, self.info = self.env.reset('regional-000', 20000)

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
        self.assertLess(reward, 0)
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
        result = self.env.submit_action(dict(requests=resource_request(), explanation=''))

        self.assertEqual(len(result), 5)
        self.assertEqual(self.env._core.decisions, 1)
        self.assertEqual(self.env._core.sim.tick, 1)
        self.assertIsNone(result[4]['error'])


if __name__ == '__main__':
    unittest.main()
