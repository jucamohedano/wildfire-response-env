import json
import unittest

from envs.wildfire_response.data.miteco import extract


class MitecoTests(unittest.TestCase):
    def test_extracts_json_without_executing_page_script(self):
        payload = dict(meta=dict(curYear='2026', fechaGen='2026-10-16'), cur=dict(nG=1),
                       gif=[dict(fecha='09/07/2026', muni='Test', prov='Almería', ccaa='ANDALUCÍA',
                                 forr=20, geoVia='provincia y mismo día (perímetro compartido)')])
        metadata, rows = extract('var D=' + json.dumps(payload) + '; doNotExecute();')
        self.assertEqual(metadata['curYear'], '2026')
        self.assertFalse(rows[0]['actor_input_eligible'])
        self.assertIn('compartido', rows[0]['provider_fields']['geoVia'])
        with self.assertRaises(ValueError):
            extract('var D=' + json.dumps(payload), cutoff='2026-10-15')
        payload['gif'][0]['fecha'] = '09/07/2025'
        with self.assertRaises(ValueError):
            extract('var D=' + json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
