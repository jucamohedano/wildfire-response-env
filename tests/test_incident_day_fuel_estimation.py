"""Tests for exercise estimates and evidence boundaries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import digest
from envs.wildfire_response.incident_day.fuel_estimation import _category, _contains, _vote, build_estimates, validate_estimates


def pin(document):
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})
    return document


def response(features):
    text = json.dumps({'type': 'FeatureCollection', 'numberMatched': len(features), 'features': features})
    return {'response': text, 'response_extracted_utf8_bytes': len(text.encode()), 'response_extracted_sha256': hashlib.sha256(text.encode()).hexdigest()}


def polygon(code, left, right, hole=None):
    outer = [[left, -1], [right, -1], [right, 1], [left, 1], [left, -1]]
    return {'type': 'Feature', 'properties': {'cod_os': code}, 'geometry': {'type': 'Polygon', 'coordinates': [outer] + ([hole] if hole else [])}}


def fixture(features=None):
    grid = {'height': 1, 'width': 5, 'transform': [500, 0, 0, 0, -500, 0], 'crs': 'EPSG:25830', 'cell_km': 0.5,
            'centre_lon_lat': [[[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]]}
    prepared = pin({'grid': grid, 'fuel_classes': [[8, 0, 4, 0, 2]], 'fuel_valid_mask': [[1, 0, 1, 0, 1]], 'study_mask': [[1, 1, 1, 1, 1]]})
    domain = pin({'grid': copy.deepcopy(grid), 'crop': {'top': 0, 'left': 0, 'height': 1, 'width': 5}, 'prepared_inputs_sha256': prepared['sha256']})
    cells = []
    for column in (1, 3):
        cell = {'row': 0, 'column': column, 'longitude': column, 'latitude': 0}
        cell.update(response(features or []))
        cells.append(cell)
    return prepared, domain, {'cells': cells}


class FuelEstimationTest(unittest.TestCase):
    def test_urban_and_uncovered_are_distinct_and_keep_known_values(self):
        prepared, domain, probe = fixture([polygon(2002, 0.5, 1.5)])
        result = build_estimates(prepared, domain, probe)
        first, second = result['cells']
        self.assertEqual((first['land_kind'], first['runtime_terrain'], first['fuel_class'], first['fuel_multiplier']), ('urban_mixed', 'built', 1, 0.35))
        self.assertEqual(first['support_neighbours'], [])
        self.assertEqual(second['land_kind'], 'unresolved')
        self.assertIn('land_and_water_unresolved', second['inference_flags'])
        self.assertEqual(result['source_known_mask'], [[1, 0, 1, 0, 1]])
        self.assertEqual([result['fuel_classes'][0][index] for index in (0, 2, 4)], [8, 4, 2])
        self.assertIs(validate_estimates(result, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid']), result)

    def test_union_and_riparian_are_not_water(self):
        prepared, domain, probe = fixture()
        probe['cells'][0].update(response([polygon(317, 2.5, 3.5)]))
        result = build_estimates(prepared, domain, probe)
        self.assertEqual(result['cells'][1]['land_kind'], 'vegetation')
        self.assertEqual(result['cells'][1]['land_category'], 'riparian')
        self.assertEqual(_category({'cod_os': 317})[0], 'vegetation')

    def test_hole_and_barrier_prevent_cross_boundary_borrow(self):
        hole = [[1, -0.5], [2, -0.5], [2, 0.5], [1, 0.5], [1, -0.5]]
        shape = polygon(2002, 0, 3, hole)
        self.assertEqual(_contains(shape, [1.5, 0]), 'outside')
        self.assertEqual(_contains(shape, [1, 0]), 'boundary')
        prepared, domain, probe = fixture()
        neighbours, alternatives = _vote(0, 1, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid'], [2], [polygon(2002, 1.5, 2.5)])
        self.assertEqual((neighbours, alternatives), ([], []))

    def test_tie_is_categorical_and_deterministic(self):
        prepared, domain, probe = fixture()
        neighbours, alternatives = _vote(0, 1, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid'], [4, 8], [], limit=2)
        self.assertEqual([item['class'] for item in alternatives], [4, 8])
        self.assertNotIn(6, [item['class'] for item in alternatives])

    def test_blocked_woodland_uses_compatible_fallback_and_rejects_tampering(self):
        prepared, domain, probe = fixture()
        prepared['fuel_classes'][0][4] = 4
        pin(prepared)
        domain['prepared_inputs_sha256'] = prepared['sha256']
        pin(domain)
        probe['cells'][0].update(response([polygon(2002, 0.5, 1.5)]))
        probe['cells'][1].update(response([polygon(510, 2.5, 3.5)]))
        result = build_estimates(prepared, domain, probe)
        woodland = result['cells'][1]
        self.assertEqual((woodland['land_category'], woodland['fuel_class'], woodland['runtime_terrain']), ('woodland', 8, 'woodland'))
        self.assertEqual(woodland['support_neighbours'], [])
        self.assertIn('fallback_no_compatible_support', woodland['inference_flags'])
        changed = copy.deepcopy(result)
        changed['cells'][1]['fuel_class'] = 4
        changed['cells'][1]['runtime_terrain'] = 'scrub'
        changed['fuel_classes'][0][3] = 4
        pin(changed)
        with self.assertRaisesRegex(ValueError, 'Incompatible estimated class'):
            validate_estimates(changed, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid'])

    def test_cultivation_without_compatible_support_uses_field_fallback(self):
        prepared, domain, probe = fixture()
        prepared['fuel_classes'][0][4] = 4
        pin(prepared)
        domain['prepared_inputs_sha256'] = prepared['sha256']
        pin(domain)
        probe['cells'][1].update(response([polygon(431, 2.5, 3.5)]))
        result = build_estimates(prepared, domain, probe)
        cultivation = result['cells'][1]
        self.assertEqual((cultivation['land_category'], cultivation['fuel_class'], cultivation['runtime_terrain']), ('cultivation', 1, 'field'))
        self.assertEqual(cultivation['support_neighbours'], [])

    def test_hash_schema_and_source_tampering_are_rejected(self):
        prepared, domain, probe = fixture()
        result = build_estimates(prepared, domain, probe)
        changed = copy.deepcopy(result)
        changed['cells'][0]['fuel_class'] = 7
        with self.assertRaises(ValueError):
            validate_estimates(changed, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid'])
        changed = copy.deepcopy(result)
        changed['cells'][0]['unexpected'] = 1
        pin(changed)
        with self.assertRaises(ValueError):
            validate_estimates(changed, prepared['fuel_classes'], prepared['fuel_valid_mask'], domain['grid'])
        changed = copy.deepcopy(prepared['fuel_classes'])
        changed[0][0] = 9
        with self.assertRaises(ValueError):
            validate_estimates(result, changed, prepared['fuel_valid_mask'], domain['grid'])
        probe['cells'][0]['response'] += ' '
        with self.assertRaises(ValueError):
            build_estimates(prepared, domain, probe)


if __name__ == '__main__':
    unittest.main()
