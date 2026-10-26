import json
import unittest

from eval.render_topographic_replay import TEMPLATE, frame, record, safe_json
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import create, fixture_domain


class TopographicReplayTests(unittest.TestCase):
    def test_embedded_json_cannot_end_script_or_insert_markup(self):
        value = {'report': '</script><script>alert("test")</script>&>'}
        encoded = safe_json(value)
        self.assertNotIn('<', encoded)
        self.assertNotIn('&', encoded)
        self.assertEqual(json.loads(encoded), value)

    def test_template_is_self_contained_and_has_exact_data_slot(self):
        template = TEMPLATE.read_text()
        self.assertEqual(template.count('__REPLAY_DATA__'), 1)
        self.assertNotIn('https://', template)
        self.assertNotIn('fetch(', template)
        self.assertIn('Simulator truth', template)
        self.assertIn('Control-centre evidence', template)

    def test_frames_are_copies_and_truth_is_separate_from_evidence(self):
        environment = create()
        observation, _ = environment.reset()
        captured = frame(environment, observation, {}, 0)
        self.assertTrue(captured['world_fire'])
        self.assertNotIn('world_fire', observation)
        self.assertEqual(captured['aircraft_operable'], observation['aircraft_operable'])
        captured['districts'][0]['population'] = -1
        self.assertGreater(observation['districts'][0]['population'], 0)

    def test_simple_response_recording_finishes_and_is_verified(self):
        prepared = fixture_inputs()
        run = record(prepared, fixture_domain(prepared), 'response')
        self.assertTrue(run['verified'])
        self.assertTrue(run['frames'][-1]['terminated'])
        self.assertEqual(run['frames'][0]['minute'], 0)
        self.assertEqual(run['reward'], 1)


if __name__ == '__main__':
    unittest.main()
