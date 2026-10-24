import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wildfire_core.communication_data import (
    communication_catalog, reconstruct_report, validate_catalog, verify_snapshots,
)
from envs.wildfire_response.regional.ops.generator import get_generated_task, validate_scenario, verify_catalog_lock
from envs.wildfire_response.regional.ops.scenario_core import ScenarioResponseEnv


class CommunicationDataTests(unittest.TestCase):
    def test_bounded_catalog_has_no_authentic_transcripts_or_actor_evidence(self):
        catalog = communication_catalog()
        self.assertEqual(len(catalog['sources']), 4)
        self.assertEqual(len(catalog['records']), 8)
        self.assertEqual(len({record['incident_id'] for record in catalog['records']}), 3)
        self.assertFalse(catalog['historical_replay_ready'])
        self.assertTrue(all(record['actor_input_eligible'] is False for record in catalog['records']))

    def test_catalog_isolation(self):
        changed = communication_catalog()
        changed['records'].clear()
        self.assertEqual(len(communication_catalog()['records']), 8)

    def test_no_actor_promotion_or_fabricated_receipts(self):
        for field, value in [('actor_input_eligible', True), ('individual_received_at', '2026-07-09T17:00:00+02:00')]:
            catalog = communication_catalog()
            catalog['records'][0][field] = value
            with self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_unknown_and_approximate_times_stay_uncertain(self):
        for index in (0, 2):
            catalog = communication_catalog()
            catalog['records'][index]['event_time']['precision'] = 'exact'
            with self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_missing_source_and_duplicate_ids_rejected(self):
        for mutation in ('source', 'duplicate'):
            catalog = communication_catalog()
            if mutation == 'source':
                catalog['records'][0]['source_id'] = 'missing'
            else:
                catalog['records'].append(copy.deepcopy(catalog['records'][0]))
            with self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_future_and_naive_event_times_rejected(self):
        for instant in ('2027-01-01T13:00:00+01:00', '2026-06-08T13:00:00'):
            catalog = communication_catalog()
            catalog['records'][0]['event_time']['value'] = instant
            with self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_aggregate_counts_do_not_expand_into_authentic_calls(self):
        result = reconstruct_report('castillejos-call-load', report_id='exercise-1', observed_at=1, received_at=4)
        self.assertEqual(result['report']['channel'], 'synthetic_phone')
        self.assertIsNone(result['report']['location'])
        self.assertEqual(result['evaluator_provenance']['authenticity'], 'source_inspired_synthetic_not_transcript')
        self.assertEqual(result['evaluator_provenance']['incident_group'], 'castillejos-2026-06-08')
        public = json.dumps(result['report'])
        for private in ('300', '2026', 'castillejos', 'evidence_id', 'source_id', 'causation'):
            self.assertNotIn(private, public)

    def test_unsupported_notice_cannot_become_a_citizen_report(self):
        for identifier in ('missing', 'pinos-shelter-alert', 'gallardos-agency-notification'):
            with self.assertRaises(ValueError):
                reconstruct_report(identifier, report_id='exercise-1', observed_at=0, received_at=1)

    def test_mapped_report_requires_explicit_assumption(self):
        with self.assertRaises(ValueError):
            reconstruct_report('gallardos-first-claims', report_id='exercise-1', observed_at=0,
                               received_at=2, location=[10, 10])
        location = [10, 10]
        result = reconstruct_report('gallardos-first-claims', report_id='exercise-1', observed_at=0,
                                    received_at=2, location=location,
                                    mapping_note='Hypothetical cell; not the historical road')
        location[0] = 99
        self.assertEqual(result['report']['location'], [10, 10])
        self.assertNotIn('mapping_note', result['report'])

    def test_bad_exercise_times_and_ids_rejected(self):
        for observed, received in ((2, 1), (-1, 2), (False, 2), (0, 101)):
            with self.assertRaises(ValueError):
                reconstruct_report('pinos-first-claims', report_id='exercise-1',
                                   observed_at=observed, received_at=received)
        with self.assertRaises(ValueError):
            reconstruct_report('pinos-first-claims', report_id='caller@example.com', observed_at=0, received_at=1)

    def test_engine_delivers_at_receipt_without_provenance(self):
        scenario = get_generated_task('control-train-000-v0')['scenario']
        result = reconstruct_report('castillejos-call-load', report_id='exercise-1', observed_at=1,
                                    received_at=4, location=scenario['reports'][0]['location'],
                                    mapping_note='Hypothetical cell, no geographic equivalence')
        scenario['reports'] = [result['report']]
        self.assertTrue(validate_scenario(scenario))
        environment = ScenarioResponseEnv(ticks_per_action=2)
        observation, _ = environment.reset(scenario['id'], scenario=scenario)
        self.assertEqual(observation['reports'], [])
        observation, *_ = environment.step({})
        self.assertEqual(observation['reports'], [])
        observation, *_ = environment.step({})
        self.assertEqual(observation['reports'][0]['available_at'], 4)
        self.assertEqual(observation['reports'][0]['observed_at'], 1)
        self.assertNotIn('evaluator_provenance', json.dumps(observation))
        self.assertNotIn('castillejos', json.dumps(observation))
        self.assertTrue(verify_catalog_lock())

    def test_snapshot_hash_verification_and_missing_file_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = communication_catalog()
            for source in catalog['sources']:
                content = source['id'].encode()
                source['snapshot_bytes'] = len(content)
                source['snapshot_sha256'] = hashlib.sha256(content).hexdigest()
                path = Path(directory, source['snapshot_path'])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            with patch('envs.wildfire_response.data.communications.communication_catalog', return_value=catalog):
                self.assertEqual(len(verify_snapshots(directory)), 4)
                path.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    verify_snapshots(directory)
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    verify_snapshots(directory)


if __name__ == '__main__':
    unittest.main()
