"""Dependency-free fuel evidence tests; no source raster or projection needed."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import digest
from envs.wildfire_response.incident_day.fuel_audit import build_ledger
from eval.audit_incident_day_fuels import classify_siose_response, verify_siose_probe


def pin(doc):
    doc['sha256'] = digest({key: value for key, value in doc.items() if key != 'sha256'})
    return doc['sha256']


def fixture():
    grid = {'height': 1, 'width': 3, 'transform': [500, 0, 100, 0, -500, 200],
            'crs': 'EPSG:25830', 'cell_km': .5,
            'centre_lon_lat': [[[-5, 36], [-4.9, 36], [-4.8, 36]]]}
    prepared = {'grid': grid, 'fuel_classes': [[2, 0, 0]],
                'fuel_valid_mask': [[1, 0, 0]], 'study_mask': [[1, 1, 1]]}
    prepared_pin = pin(prepared)
    domain = {'grid': copy.deepcopy(grid), 'crop': {'top': 0, 'left': 0, 'height': 1, 'width': 3},
              'prepared_inputs_sha256': prepared_pin}
    domain_pin = pin(domain)
    observations = {(0, 1): {'native_xy': [1., 2.], 'native_pixel': [5, 6], 'native_status': 'masked', 'native_value': None},
                    (0, 2): {'native_xy': [3., 4.], 'native_pixel': None, 'native_status': 'outside', 'native_value': None}}
    return prepared, domain, prepared_pin, domain_pin, observations


class FuelAuditTest(unittest.TestCase):
    def build(self, inputs):
        prepared, domain, prepared_pin, domain_pin, observations = inputs
        return build_ledger(prepared, domain, observations, prepared_pin=prepared_pin, domain_pin=domain_pin)

    def test_masked_and_outside_are_unresolved(self):
        ledger = self.build(fixture())
        self.assertEqual(ledger['counts'], {'unknown': 2, 'masked': 1, 'outside': 1, 'valid': 0})
        self.assertEqual(ledger['approval_status'], 'blocked')
        self.assertEqual(ledger['cells'][0]['grid_xy'], [850, -50])
        self.assertEqual(ledger['cells'][0]['longitude'], -4.9)
        for cell in ledger['cells']:
            self.assertFalse(cell['source_known'])
            self.assertIsNone(cell['source_class'])
            self.assertEqual(cell['evidence_status'], 'unresolved')

    def test_valid_native_conflict_is_not_a_repair(self):
        inputs = fixture()
        inputs[4][(0, 1)].update(native_status='valid', native_value=4)
        cell = self.build(inputs)['cells'][0]
        self.assertEqual(cell['evidence_status'], 'source_conflict_requires_review')
        self.assertIsNone(cell['source_class'])
        self.assertFalse(cell['source_known'])

    def test_masked_value_is_rejected(self):
        inputs = fixture()
        inputs[4][(0, 1)]['native_value'] = 0
        with self.assertRaisesRegex(ValueError, 'Masked observation'):
            self.build(inputs)

    def test_tampering_and_incomplete_observations_fail(self):
        inputs = fixture()
        inputs[0]['fuel_classes'][0][1] = 2
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.build(inputs)
        inputs = fixture()
        del inputs[4][(0, 1)]
        with self.assertRaisesRegex(ValueError, 'observation'):
            self.build(inputs)

    def test_grid_misalignment_rejected_even_with_valid_hash(self):
        inputs = fixture()
        inputs[1]['grid']['transform'][2] += 500
        pin(inputs[1])
        with self.assertRaisesRegex(ValueError, 'alignment'):
            build_ledger(inputs[0], inputs[1], inputs[4], prepared_pin=inputs[2], domain_pin=inputs[1]['sha256'])

    def test_siose_point_and_polygon_boundary_remain_distinct(self):
        response = ('{"type":"FeatureCollection","numberMatched":1,"features":[{"properties":{"fid":"1"},'
                    '"geometry":{"type":"Polygon","coordinates":[[[0,0],[2,0],[2,2],[0,2],[0,0]]]}}]}')
        self.assertEqual(classify_siose_response(response, (1, 1)), ('point_inside', {'fid': '1'}))
        self.assertEqual(classify_siose_response(response, (0, 1)), ('ambiguous_boundary_or_overlap', None))
        self.assertEqual(classify_siose_response(response, (3, 1)), ('no_containing_feature', None))

    def test_siose_multipolygon_holes_and_boundaries(self):
        outer = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
        hole = [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]
        second = [[6, 0], [8, 0], [8, 2], [6, 2], [6, 0]]
        response = json.dumps({'type': 'FeatureCollection', 'numberMatched': 1, 'features': [
            {'properties': {'fid': 'multipolygon'}, 'geometry': {'type': 'MultiPolygon',
             'coordinates': [[outer, hole], [second]]}}]})
        self.assertEqual(classify_siose_response(response, (3, 3)), ('point_inside', {'fid': 'multipolygon'}))
        self.assertEqual(classify_siose_response(response, (7, 1)), ('point_inside', {'fid': 'multipolygon'}))
        self.assertEqual(classify_siose_response(response, (1.5, 1.5)), ('no_containing_feature', None))
        self.assertEqual(classify_siose_response(response, (1, 1.5)), ('ambiguous_boundary_or_overlap', None))

    def test_siose_rejects_pagination_and_truncated_connector_text(self):
        document = {'type': 'FeatureCollection', 'numberMatched': 2, 'features': [
            {'properties': {}, 'geometry': {'type': 'Polygon',
             'coordinates': [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}}]}
        self.assertEqual(classify_siose_response(json.dumps(document), (1, 1)), ('incomplete', None))
        document['numberMatched'] = 1
        response = json.dumps(document) + '\n\n---\n[Content info: Showing characters 0-10 of 20 total]'
        self.assertEqual(classify_siose_response(response, (1, 1)), ('incomplete', None))
        del document['numberMatched']
        document['features'] = document['features'] * 5
        self.assertEqual(classify_siose_response(json.dumps(document), (1, 1)), ('incomplete', None))

    def test_retained_official_probe_matches_ledger(self):
        root = Path(__file__).resolve().parents[1] / 'data/incident_day_v2/fuel_review'
        counts = verify_siose_probe(root / 'siose2020_wfs_probe.json', root / 'unknown_cells.json')
        self.assertEqual(counts, {'point_inside': 53, 'ambiguous_boundary_or_overlap': 0,
                                  'no_feature_returned': 20, 'no_containing_feature': 7, 'incomplete': 0})

    def test_retained_response_tampering_is_rejected(self):
        root = Path(__file__).resolve().parents[1] / 'data/incident_day_v2/fuel_review'
        document = json.loads((root / 'siose2020_wfs_probe.json').read_text())
        document['cells'][0]['response'] += 'altered'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'probe.json'
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'hash or byte count mismatch'):
                verify_siose_probe(path, root / 'unknown_cells.json')


if __name__ == '__main__':
    unittest.main()
