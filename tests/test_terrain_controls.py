import copy
import unittest

from eval.validate_terrain_controls import compare, sample, survey_point


def collection():
    return dict(type='FeatureCollection', features=[dict(type='Feature', id=107199,
        geometry=dict(type='Point', coordinates=[-5.208312774, 36.484362155]),
        properties=dict(nombre='Reales', alt_orto=1451.467, alt_elip=1497.812))])


class TerrainControlTests(unittest.TestCase):
    def test_mark_height_type_and_uncertainty_are_preserved(self):
        point = survey_point(collection(), 107199, 'roi')
        self.assertEqual(point['height_reference_point'], 'base_of_pillar')
        self.assertIsNone(point['mark_to_bare_ground_offset_metres'])
        self.assertFalse(point['exact_vertical_realization_verified'])
        result = compare(point, [[1450.0]], [5, 0, 100, 0, -5, 200], 102, 198)
        self.assertAlmostEqual(result['raster_minus_survey_mark_metres'], -1.467)
        self.assertIsNone(result['accuracy_pass'])

    def test_point_selection_rejects_missing_duplicate_or_invalid_measurements(self):
        original = collection()
        for mutate in (lambda data: data['features'].clear(),
                       lambda data: data['features'].append(copy.deepcopy(data['features'][0])),
                       lambda data: data['features'][0]['properties'].update(alt_orto=None),
                       lambda data: data['features'][0]['properties'].update(alt_elip=float('nan')),
                       lambda data: data['features'][0]['geometry'].update(coordinates=[-50, 36.5])):
            changed = copy.deepcopy(original)
            mutate(changed)
            with self.assertRaises(ValueError):
                survey_point(changed, 107199, 'roi')

    def test_north_up_sampling_has_half_open_bounds_and_reports_cell_offset(self):
        transform = [25, 0, 100, 0, -25, 200]
        values = [[1., 2.], [3., 4.]]
        self.assertEqual(sample(values, transform, 100, 200)['height_metres'], 1)
        self.assertEqual(sample(values, transform, 125, 175)['height_metres'], 4)
        self.assertIsNone(sample(values, transform, 150, 175))
        self.assertIsNone(sample(values, transform, 125, 150))
        self.assertEqual(sample(values, transform, 112.5, 187.5)['offset_from_mark_metres'], 0)

    def test_bad_terrain_is_never_compared_or_repaired(self):
        for height in (None, 0, -9999, True, float('inf')):
            with self.assertRaises(ValueError):
                sample([[height]], [5, 0, 0, 0, -5, 5], 1, 1)
        with self.assertRaises(ValueError):
            sample([[1]], [5, 1, 0, 0, -5, 5], 1, 1)


if __name__ == '__main__':
    unittest.main()
