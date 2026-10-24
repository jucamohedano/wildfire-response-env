import copy
import unittest
from unittest.mock import patch

from wildfire_core.protection import ProtectionDevelopmentEnv, ProtectionBaseline, verify_protection_trajectory
from wildfire_core.spanish import SpanishDevelopmentEnv, verify_spanish_trajectory
from envs.wildfire_response.regional.fire import RegionalFire
from envs.wildfire_response.regional.protection.tasks import CONFIGURATION, case_ids
from envs.wildfire_response.regional.protection.world import ProtectionWorld
from envs.wildfire_response.tests.test_spanish_development import fixture_pack


def order(action='shelter', *, supersedes=None, channel='broadcast', resource_id=None,
          recipient='settlement-west'):
    return dict(recipient=recipient, action=action, channel=channel,
                resource_id=resource_id, supersedes=supersedes)


def environment(identifier='protect-dev-000-v0'):
    instance = ProtectionDevelopmentEnv(fixture_pack(), max_decisions=120, ticks_per_action=1)
    instance.reset(identifier)
    return instance


def advance_to(instance, minute):
    while instance.get_situation()['minute'] < minute:
        instance.advance()
    return instance.get_situation()


class StaticFireFixture:
    def __init__(self):
        self.tick = 0
        self.cells = [[{'burning': False} for _ in range(7)] for _ in range(7)]
        self.cells[3][1]['burning'] = True
        self.groups = dict(town=dict(count=10, burnt=0, status='unwarned', x=3., y=3., home=[3, 3]))

    def burning(self, cell):
        return cell['burning']

    def step(self):
        self.tick += 1
        for group in self.groups.values():
            if self.cells[round(group['y'])][round(group['x'])]['burning']:
                group.update(burnt=group['count'], status='burnt')

    def log(self, *arguments):
        pass


class ProtectionWorldTests(unittest.TestCase):
    def test_initial_smoke_telemetry_is_computed_and_timestamped_not_default_clear(self):
        world = ProtectionWorld(StaticFireFixture(), CONFIGURATION)
        self.assertTrue(world.population_state()['town']['smoke_present'])
        self.assertEqual(world.population_state()['town']['smoke_observed_at'], 0)
        self.assertEqual(world.population_state()['town']['smoke_dose_units'], 0)
        world.step()
        self.assertEqual(world.population_state()['town']['smoke_observed_at'], 1)
    def test_shelter_reduces_dose_but_does_not_make_people_immune(self):
        outside = ProtectionWorld(StaticFireFixture(), CONFIGURATION)
        sheltered = ProtectionWorld(StaticFireFixture(), CONFIGURATION)
        self.assertTrue(sheltered.execute_protection('town', 'shelter')[0])
        outside.step(12)
        sheltered.step(12)
        self.assertEqual(outside.groups['town']['burnt'], 10)
        self.assertEqual(sheltered.groups['town']['burnt'], 0)
        self.assertEqual(sheltered.population_state()['town']['smoke_dose_units'], 24)
        sheltered.step(48)
        self.assertEqual(sheltered.groups['town']['burnt'], 10)
        self.assertFalse(sheltered.population_state()['town']['shelter_active'])

    def test_direct_fire_exposes_a_sheltering_group(self):
        world = ProtectionWorld(StaticFireFixture(), CONFIGURATION)
        world.execute_protection('town', 'shelter')
        world.cells[3][3]['burning'] = True
        world.step()
        self.assertEqual(world.groups['town']['burnt'], 10)
        self.assertFalse(world.population_safe_now())

    def test_no_teleport_and_release_does_not_cancel_an_evacuation_in_motion(self):
        world = ProtectionWorld(StaticFireFixture(), CONFIGURATION)
        world.groups['town'].update(status='evacuating', x=4.)
        self.assertFalse(world.execute_protection('town', 'shelter')[0])
        self.assertFalse(world.execute_protection('town', 'release')[0])
        self.assertEqual(world.groups['town']['x'], 4.)
        self.assertEqual(world.groups['town']['status'], 'evacuating')


class ProtectionProtocolTests(unittest.TestCase):
    def test_delivery_acknowledgement_and_execution_are_distinct(self):
        instance = environment()
        observation, reward, *_ = instance.issue_protection_orders([order()])
        self.assertEqual(reward, 0)
        self.assertEqual(observation['protection_orders'][0]['status'], 'pending')
        observation = advance_to(instance, 2)
        self.assertEqual(observation['protection_orders'][0]['status'], 'delivered')
        self.assertEqual(observation['districts'][0]['status'], 'unwarned')
        observation = advance_to(instance, 3)
        self.assertEqual(observation['protection_orders'][0]['status'], 'acknowledged')
        self.assertEqual(observation['districts'][0]['status'], 'unwarned')
        observation = advance_to(instance, 4)
        self.assertEqual(observation['protection_orders'][0]['status'], 'executed')
        self.assertEqual(observation['districts'][0]['status'], 'sheltering')

    def test_late_old_instruction_cannot_override_a_newer_received_instruction(self):
        instance = environment('protect-dev-000-v1')
        instance.issue_protection_orders([order()])
        observation, *_ = instance.issue_protection_orders([order('evacuate', supersedes='protection-1')])
        self.assertEqual(observation['protection_orders'][0]['status'], 'pending')
        observation = advance_to(instance, 5)
        self.assertEqual(observation['districts'][0]['status'], 'evacuating')
        observation = advance_to(instance, 8)
        self.assertEqual(observation['protection_orders'][0]['status'], 'stale_ignored')
        self.assertIsNone(observation['protection_orders'][0]['executed_at'])
        self.assertEqual(observation['last_executed_protection_orders']['settlement-west'], 'protection-2')

    def test_replacement_does_not_instantly_change_population_before_receipt_and_execution(self):
        instance = environment()
        instance.issue_protection_orders([order()])
        advance_to(instance, 4)
        instance.issue_protection_orders([order('evacuate', supersedes='protection-1')])
        self.assertEqual(instance.get_situation()['districts'][0]['status'], 'sheltering')
        observation = advance_to(instance, 6)
        self.assertEqual(observation['districts'][0]['status'], 'sheltering')
        self.assertEqual(observation['protection_orders'][0]['superseded_at_recipient'], 6)
        observation = advance_to(instance, 8)
        self.assertEqual(observation['districts'][0]['status'], 'evacuating')

    def test_failed_broadcast_has_no_population_effect_and_can_be_retried(self):
        instance = environment('protect-dev-000-v2')
        instance.issue_protection_orders([order()])
        observation = advance_to(instance, 2)
        self.assertEqual(observation['protection_orders'][0]['status'], 'failed')
        self.assertEqual(observation['districts'][0]['status'], 'unwarned')
        instance.issue_protection_orders([order(supersedes='protection-1')])
        observation = advance_to(instance, 6)
        self.assertEqual(observation['protection_orders'][1]['status'], 'executed')
        self.assertEqual(instance._core.protection.redundant_orders, 0)

    def test_repeated_same_instruction_is_recorded_as_redundant_not_rewarded(self):
        instance = environment()
        instance.issue_protection_orders([order()])
        _, reward, *_ = instance.issue_protection_orders([order(supersedes='protection-1')])
        self.assertEqual(reward, 0)
        self.assertEqual(instance._core.protection.redundant_orders, 1)
        self.assertEqual(instance.get_situation()['protection_orders'][-1]['status'], 'duplicate_ignored')
        self.assertEqual(instance.get_situation()['latest_protection_orders']['settlement-west'], 'protection-1')

    def test_duplicate_cannot_bypass_a_slow_transport_queue(self):
        first, repeated = environment('protect-dev-000-v1'), environment('protect-dev-000-v1')
        first.issue_protection_orders([order()])
        repeated.issue_protection_orders([order()])
        first.advance()
        repeated.issue_protection_orders([order(supersedes='protection-1')])
        for minute in range(3, 11):
            original_state = advance_to(first, minute)
            repeat_state = advance_to(repeated, minute)
            self.assertEqual(original_state['districts'], repeat_state['districts'])
        self.assertEqual(original_state['protection_orders'][0]['executed_at'],
                         repeat_state['protection_orders'][0]['executed_at'])
        self.assertIsNone(repeat_state['protection_orders'][1]['executed_at'])

    def test_unable_to_comply_is_not_recorded_as_executed(self):
        instance = environment()
        instance.issue_protection_orders([order('evacuate')])
        advance_to(instance, 5)
        instance.issue_protection_orders([order('shelter', supersedes='protection-1')])
        observation = advance_to(instance, 9)
        record = observation['protection_orders'][-1]
        self.assertEqual(record['status'], 'unable_to_comply')
        self.assertIsNotNone(record['attempted_at'])
        self.assertIsNone(record['executed_at'])
        self.assertEqual(observation['last_executed_protection_orders']['settlement-west'], 'protection-1')

    def test_invalid_replacement_is_atomic_with_other_orders_and_advances_time(self):
        instance = environment()
        observation, _, _, _, info = instance.step(dict(
            protection_orders=[order(supersedes='unknown')],
            requests=[dict(resource_id='engine-valverde', sector_id='report:claim-0', mission='recon')]))
        self.assertIsNotNone(info['error'])
        self.assertEqual(observation['minute'], 1)
        self.assertEqual(observation['protection_orders'], [])
        self.assertEqual(observation['pending_resource_requests'], [])

    def test_legacy_evacuation_is_rejected_and_information_does_not_move_people(self):
        instance = environment()
        observation, _, _, _, info = instance.step(
            {'messages': [dict(recipient='settlement-west', purpose='evacuate')]})
        self.assertIsNotNone(info['error'])
        self.assertEqual(observation['protection_orders'], [])
        instance.step({'messages': [dict(recipient='settlement-west', purpose='inform')]})
        observation = advance_to(instance, 4)
        self.assertEqual(observation['districts'][0]['status'], 'unwarned')

    def test_warning_team_requires_actual_arrival_and_remains_occupied(self):
        instance = environment()
        bad = instance.check_orders({'protection_orders': [order(channel='in_person', resource_id='crew-valverde')]})
        self.assertFalse(bad['valid'])
        observation, *_ = instance.request_resources([
            dict(resource_id='crew-valverde', sector_id='protect:settlement-west', mission='suppress')])
        instance.step(dict(readback_confirmations=[observation['readbacks'][0]['id']],
                           authorizations=[observation['offers'][0]['id']]))
        while next(unit for unit in instance.get_situation()['resources'] if unit['id']=='crew-valverde')['state'] != 'on_scene':
            instance.advance()
        issued_at = instance.get_situation()['minute']
        home = instance._core.sim.groups['settlement-west']['home']
        instance._core.sim.add_fire(home[0]-1, home[1])
        with patch.object(RegionalFire, 'suppress') as suppression:
            instance.issue_protection_orders([order(channel='in_person', resource_id='crew-valverde')])
            self.assertFalse(instance.check_orders({'releases': ['crew-valverde']})['valid'])
            advance_to(instance, issued_at + 3)
            self.assertEqual(suppression.call_count, 0)
            advance_to(instance, issued_at + 4)
            self.assertGreater(suppression.call_count, 0)

    def test_safety_failure_cannot_be_offset_by_other_objectives(self):
        instance = environment()
        instance._core.sim.groups['settlement-west']['burnt'] = 1
        with patch('envs.wildfire_response.regional.protection.environment.objective_step',
                   return_value=(['evidence_triage','population_safety','incident_resolved','communication_discipline'],1.)):
            completed, reward = instance._objective_step(instance.get_situation(), True)
        self.assertEqual(reward, 0)
        self.assertNotIn('population_safety', completed)
        self.assertEqual(len(completed), 3)

    def test_in_person_order_replays_in_a_complete_canonical_episode(self):
        instance = environment()
        observation, *_ = instance.request_resources([
            dict(resource_id='crew-valverde', sector_id='protect:settlement-west', mission='recon')])
        instance.step(dict(readback_confirmations=[observation['readbacks'][0]['id']],
                           authorizations=[observation['offers'][0]['id']]))
        while next(unit for unit in instance.get_situation()['resources'] if unit['id']=='crew-valverde')['state'] != 'on_scene':
            instance.advance()
        observation, *_ = instance.issue_protection_orders([order(channel='in_person', resource_id='crew-valverde')])
        baseline = ProtectionBaseline()
        while True:
            observation, _, terminated, truncated, info = instance.step(baseline(observation))
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        self.assertTrue(any(record['channel']=='in_person' and record['executed_at'] is not None
                            for record in observation['protection_orders']))
        self.assertTrue(instance.verify_episode()['passed'])

    def test_replay_and_v1_compatibility_and_tamper_detection(self):
        pack = fixture_pack()
        instance = ProtectionDevelopmentEnv(pack)
        observation, _ = instance.reset()
        baseline = ProtectionBaseline()
        while True:
            observation, _, terminated, truncated, info = instance.step(baseline(observation))
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        self.assertTrue(instance.verify_episode()['passed'])
        changed = instance.trajectory()
        changed['transitions'][0]['observation']['minute'] += 1
        with self.assertRaises(ValueError):
            verify_protection_trajectory(changed, pack)
        with self.assertRaises(ValueError):
            verify_spanish_trajectory(instance.trajectory(), pack)
        old = SpanishDevelopmentEnv(pack)
        old.reset()
        for _ in range(3):
            old.step({'unknown': True})
        self.assertTrue(verify_spanish_trajectory(old.trajectory(), pack)['verified'])
        self.assertEqual(len(case_ids(pack)), 12)


if __name__ == '__main__':
    unittest.main()
