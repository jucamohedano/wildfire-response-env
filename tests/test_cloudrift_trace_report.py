import copy
import json
from pathlib import Path
import tempfile
import unittest

from eval.render_cloudrift_ops_traces import (
    START, build_case, public_observation, render_section, update_report,
)


ROOT = Path(__file__).resolve().parents[1]


class CloudRiftTraceReportTests(unittest.TestCase):
    def test_saved_excerpts_account_for_every_attempt_without_replaying_prefix_twice(self):
        export = json.loads((ROOT / 'results/cloudrift-ops-20261026-trajectories.json').read_text())
        false_alarm, urgent = export['cases']
        self.assertEqual([case['decisions'] for case in export['cases']], [6, 6])
        self.assertEqual([len(case['events']) for case in export['cases']], [8, 8])
        events = [event for case in export['cases'] for event in case['events']]
        self.assertEqual(sum(event['kind'] == 'invalid' for event in events), 7)
        self.assertEqual(sum(event['kind'] == 'timeout' for event in events), 2)
        self.assertEqual(sum(event['kind'] == 'check' for event in events), 2)
        self.assertEqual(false_alarm['score'], 0)
        self.assertTrue(false_alarm['events'][-1]['truncated'])
        self.assertIsNone(urgent['score'])
        self.assertFalse(urgent['scored'])
        self.assertNotIn('observation_after', urgent['events'][-1])
        self.assertEqual(urgent['events'][-2]['kind'], 'check')

    def test_public_selection_omits_unknown_fields_and_html_escapes_recorded_text(self):
        observation = {'minute': 0, 'private_fire': [1, 2], 'future_events': [], 'landscape': {}}
        self.assertEqual(public_observation(observation), {'minute': 0})
        export = json.loads((ROOT / 'results/cloudrift-ops-20261026-trajectories.json').read_text())
        export['cases'][0]['events'][0]['proposal']['explanation'] = '<script>alert("unsafe")</script>'
        rendered = render_section(export, 'trace.json')
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('Manual continuation', rendered)
        self.assertIn('Not scored', rendered)
        self.assertIn('Raw malformed arguments', rendered)

    def test_report_update_is_idempotent_and_preserves_original_sections(self):
        original = '<main><h2>Outcomes</h2><h2>Audit and budget</h2></main>'
        section = START + '\nnew traces\n<!-- saved-trajectories:end -->'
        updated = update_report(original, section)
        self.assertEqual(update_report(updated, section), updated)
        self.assertIn('<h2>Outcomes</h2>', updated)
        self.assertIn('<h2>Audit and budget</h2>', updated)
        self.assertEqual(updated.count(START), 1)
        with self.assertRaises(ValueError):
            update_report('<main></main>', section)

    def test_builder_rejects_log_action_mismatch_and_does_not_read_private_artifacts(self):
        outcome = dict(task_id='topo-dev-000', family='urgent', scored=False,
                       score=None, status='transport_timeout')
        observation = dict(minute=0, decision=0, districts=[], resources=[], reports=[])
        step = dict(action={'requests': []}, action_valid=True, objective_reward=0.,
                    terminated=False, truncated=False,
                    observation=dict(observation, minute=2, decision=1))
        trace = dict(initial_observation=observation, transitions=[step], seed=7, reward_version='v2')
        request = dict(request=1, decision=0, minute=0, seconds=1, tool='submit_action',
                       proposal={'requests': [], 'explanation': 'Wait'}, protocol_error=None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / 'cloudrift-ops-eval-20261026-resumed' / 'topo-dev-000'
            case.mkdir(parents=True)
            (case / 'partial-trajectory.json').write_text(json.dumps(trace))
            (case / 'requests.json').write_text(json.dumps([request]))
            (case / 'private').mkdir()
            (case / 'private' / 'verification.json').write_text('must not be read')
            result = build_case(root, outcome)
            self.assertEqual(result['decisions'], 1)
            self.assertEqual(len(result['sources']), 2)
            changed = copy.deepcopy(request)
            changed['proposal']['requests'] = ['different']
            (case / 'requests.json').write_text(json.dumps([changed]))
            with self.assertRaisesRegex(ValueError, 'canonical transition'):
                build_case(root, outcome)


if __name__ == '__main__':
    unittest.main()
