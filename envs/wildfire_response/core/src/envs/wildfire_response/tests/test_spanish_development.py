import copy
from datetime import datetime, timedelta
import json
import unittest

from wildfire_core.spanish import SpanishDevelopmentEnv, ClarifyingControlBaseline, verify_spanish_trajectory
from envs.wildfire_response.regional.grounded.anchors import VERSION, anchor_pack_sha256
from envs.wildfire_response.regional.grounded.tasks import case_ids, get_task
from envs.wildfire_response.regional.ops.objectives import verify_ops_trajectory
from envs.wildfire_response.regional.ops.generator import verify_catalog_lock


def fixture_pack():
    """Synthetic schema fixtures, never historical incident demonstrations."""
    anchors = []
    for index, (province, delay, stratum) in enumerate((('4', 8, 'small'), ('28', 12, 'small'),
                                                       ('21', 20, 'large'), ('29', 30, 'large'))):
        detected = datetime(2021, 7, 1, 12)
        anchors.append(dict(id=f'anchor-{index:03d}', source_incident_id=f'2021{int(province):02d}0001',
            province_code=province, stratum=stratum, detection_local=detected.isoformat(),
            ground_response_minutes=float(delay), quality_flags=[
                'detection_local_timezone_unresolved', 'weather_hour_semantics_unresolved',
                'weather_not_actor_input'], historical_reference=dict(
                ground_arrival=(detected + timedelta(minutes=delay)).isoformat(),
                air_arrival=None, controlled=(detected + timedelta(days=1)).isoformat(),
                extinguished=(detected + timedelta(days=2)).isoformat(),
                burned_wooded_ha='501' if stratum == 'large' else '0.1', burned_unwooded_ha='0')))
    pack = dict(version=VERSION,
        source_archive_sha256='316e34acf653079efd6ea895f0991681468af6c9bc84897030a9bbde220d60e0',
        source_year=2021, role='retrospective_evaluator_evidence', actor_input_eligible=False, anchors=anchors)
    pack['sha256'] = anchor_pack_sha256(pack)
    return pack


def arrived_at(delay=0):
    environment = SpanishDevelopmentEnv(fixture_pack())
    observation, _ = environment.reset()
    for _ in range(delay):
        observation, *_ = environment.advance()
    observation, *_ = environment.request_resources([
        dict(resource_id='engine-valverde', sector_id='report:claim-0', mission='recon')])
    offer = observation['offers'][0]
    readback = observation['readbacks'][0]
    environment.submit_action(dict(readback_confirmations=[readback['id']], authorizations=[offer['id']]))
    for _ in range(30):
        observation, *_ = environment.advance()
        arrivals = [record['minute'] for record in observation['acknowledgements']
                    if record['kind'] == 'resource_arrived' and record['resource_id'] == 'engine-valverde']
        if arrivals:
            return arrivals[0]
    raise AssertionError('No simulated arrival')


class SpanishDevelopmentTests(unittest.TestCase):
    def test_cases_are_development_only_seeded_and_do_not_schedule_historical_outcomes(self):
        pack = fixture_pack()
        self.assertEqual(len(case_ids(pack)), 12)
        for identifier in case_ids(pack):
            task = get_task(pack, identifier)
            self.assertEqual(task['split'], 'development')
            self.assertTrue(task['scenario']['synthetic'])
            self.assertLessEqual({event['kind'] for event in task['scenario']['events']},
                                 {'ignition', 'wind', 'daylight'})
        environment = SpanishDevelopmentEnv(pack)
        with self.assertRaises(ValueError):
            environment.reset('es-dev-000-v0', seed=1)
        self.assertTrue(verify_catalog_lock())

    def test_observations_exclude_source_identity_outcomes_and_future_weather(self):
        pack = fixture_pack()
        environment = SpanishDevelopmentEnv(pack)
        observation, _ = environment.reset('es-dev-000-v1')
        for anchor in pack['anchors']:
            self.assertNotIn(anchor['source_incident_id'], json.dumps(observation))
        for field in ('historical_reference', 'quality_flags', 'ground_response_minutes'):
            self.assertNotIn(field, json.dumps(observation))
        self.assertEqual(observation['wind'], [1, 0])
        for _ in range(6):
            observation, *_ = environment.advance()
        self.assertEqual(observation['wind'], [1, 0])
        observation, *_ = environment.advance()
        self.assertEqual(observation['wind'], [-1, 1])

    def test_location_followup_is_delayed_and_not_sensor_confirmation(self):
        environment = SpanishDevelopmentEnv(fixture_pack())
        observation, _ = environment.reset()
        environment.advance()
        observation, *_ = environment.advance()
        self.assertIsNone(environment.get_reports(['claim-1'])[0]['location'])
        self.assertFalse(any(report['id'].startswith('followup') for report in observation['reports']))
        self.assertTrue(environment.check_orders({'clarifications': ['claim-1']})['valid'])
        self.assertEqual(environment.get_situation()['minute'], 4)
        self.assertEqual(environment.get_situation()['clarification_requests'], [])
        observation, *_ = environment.clarify_reports(['claim-1'])
        answer = environment.get_reports(['followup-claim-1'])[0]
        self.assertEqual((answer['observed_at'], answer['received_at']), (2, 6))
        self.assertIsNotNone(answer['location'])
        self.assertFalse(any(record['report_id'] == answer['id'] for record in observation['report_assessments']))
        self.assertEqual(observation['clarification_requests'][0]['status'], 'answered')
        observation, _, _, _, info = environment.clarify_reports(['claim-1'])
        self.assertIsNotNone(info['error'])
        self.assertEqual(len(observation['clarification_requests']), 1)

    def test_invalid_combined_clarification_is_atomic_and_consumes_time(self):
        environment = SpanishDevelopmentEnv(fixture_pack())
        environment.reset()
        observation, _, _, _, info = environment.submit_action(dict(
            requests=[dict(resource_id='engine-valverde', sector_id='report:claim-0', mission='recon')],
            clarifications=['claim-1']))
        self.assertIsNotNone(info['error'])
        self.assertEqual(observation['minute'], 2)
        self.assertEqual(observation['pending_resource_requests'], [])
        self.assertEqual(observation['clarification_requests'], [])

    def test_dispatch_delay_changes_arrival_instead_of_forcing_historical_time(self):
        immediate = arrived_at()
        delayed = arrived_at(3)
        self.assertEqual(delayed - immediate, 6)
        self.assertNotEqual(immediate, fixture_pack()['anchors'][0]['ground_response_minutes'])

    def test_historical_final_outcomes_do_not_drive_simulated_future(self):
        original = fixture_pack()
        changed = copy.deepcopy(original)
        changed['anchors'][0]['historical_reference'].update(
            controlled='2021-07-10T12:00:00', extinguished='2021-07-12T12:00:00', burned_wooded_ha='0.9')
        changed['sha256'] = anchor_pack_sha256(changed)
        first, second = SpanishDevelopmentEnv(original), SpanishDevelopmentEnv(changed)
        self.assertEqual(first.reset()[0], second.reset()[0])
        for _ in range(10):
            self.assertEqual(first.step({}), second.step({}))

    def test_shelter_is_rejected_instead_of_silently_evacuating(self):
        environment = SpanishDevelopmentEnv(fixture_pack())
        environment.reset()
        observation, _, _, _, info = environment.step(
            {'messages': [{'recipient': 'settlement-west', 'purpose': 'shelter'}]})
        self.assertIsNotNone(info['error'])
        self.assertEqual(observation['message_deliveries'], [])
        self.assertTrue(all(district['status'] == 'unwarned' for district in observation['districts']))
        self.assertFalse(observation['capabilities']['shelter_in_place'])

    def test_assumed_daylight_gate_tracks_clock_crossing_without_using_future_outcomes(self):
        pack = fixture_pack()
        anchor = pack['anchors'][0]
        anchor['detection_local'] = '2021-07-01T19:50:00'
        anchor['historical_reference']['ground_arrival'] = '2021-07-01T19:58:00'
        pack['sha256'] = anchor_pack_sha256(pack)
        environment = SpanishDevelopmentEnv(pack)
        observation, _ = environment.reset()
        self.assertTrue(observation['daylight'])
        for _ in range(5):
            observation, *_ = environment.advance()
        self.assertFalse(observation['daylight'])

    def test_replay_binds_private_pack_and_rejects_tampering_and_old_verifier(self):
        pack = fixture_pack()
        environment = SpanishDevelopmentEnv(pack)
        environment.reset()
        for _ in range(3):
            environment.submit_action({'unknown': True})
        trace = environment.trajectory()
        self.assertEqual(environment.verify_episode()['objective_score'], 0)
        self.assertTrue(verify_spanish_trajectory(trace, pack)['verified'])
        changed = copy.deepcopy(trace)
        changed['transitions'][0]['observation']['minute'] += 1
        with self.assertRaises(ValueError):
            verify_spanish_trajectory(changed, pack)
        changed = copy.deepcopy(pack)
        changed['anchors'][0]['historical_reference']['extinguished'] = '2021-07-04T12:00:00'
        changed['sha256'] = anchor_pack_sha256(changed)
        with self.assertRaises(ValueError):
            verify_spanish_trajectory(trace, changed)
        with self.assertRaises(ValueError):
            verify_ops_trajectory(trace)

    def test_public_baseline_finishes_and_resets_are_isolated(self):
        environment = SpanishDevelopmentEnv(fixture_pack())
        initial, _ = environment.reset()
        baseline = ClarifyingControlBaseline()
        observation = initial
        while True:
            observation, _, terminated, truncated, info = environment.submit_action(baseline(observation))
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        self.assertTrue(environment.verify_episode()['passed'])
        self.assertEqual(observation['clarification_requests'][0]['status'], 'answered')
        reset, _ = environment.reset()
        self.assertEqual(initial, reset)


if __name__ == '__main__':
    unittest.main()
