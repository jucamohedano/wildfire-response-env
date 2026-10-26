import copy
from dataclasses import FrozenInstanceError
import math
import unittest

from eval.check_terrain_gradients import MAX_CELLS, horn3x3


def plane(height=7, width=8, column_rise=0, row_rise=0, offset=0):
    return [[offset + column_index * column_rise + row_index * row_rise
             for column_index in range(width)] for row_index in range(height)]


class TerrainGradientTests(unittest.TestCase):
    def gradients(self, elevations, **options):
        parameters = dict(cell_size_x_metres=100, cell_size_y_metres=100,
                          vertical_scale_metres=1)
        parameters.update(options)
        return horn3x3(elevations, **parameters)

    def assert_plane(self, result, column_gradient, row_gradient):
        magnitude = math.hypot(column_gradient, row_gradient)
        height, width = len(result.valid_mask), len(result.valid_mask[0])
        for row_index in range(height):
            for column_index in range(width):
                interior = 0 < row_index < height - 1 and 0 < column_index < width - 1
                self.assertEqual(result.valid_mask[row_index][column_index], interior)
                for values, expected in ((result.gradient_columns, column_gradient),
                                         (result.gradient_rows, row_gradient),
                                         (result.slope_percent, magnitude * 100),
                                         (result.slope_degrees, math.degrees(math.atan(magnitude)))):
                    if interior:
                        self.assertAlmostEqual(values[row_index][column_index], expected, places=12)
                    else:
                        self.assertIsNone(values[row_index][column_index])

    def test_flat_plane_including_sea_level_is_zero(self):
        for offset in (0, 1200, -30):
            with self.subTest(offset=offset):
                self.assert_plane(self.gradients(plane(offset=offset)), 0, 0)

    def test_ten_metres_per_hundred_is_ten_percent_and_atan_point_one(self):
        result = self.gradients(plane(column_rise=10))
        self.assert_plane(result, 0.1, 0)
        self.assertEqual(result.slope_percent[2][2], 10)
        self.assertAlmostEqual(result.slope_degrees[2][2], 5.710593137499643)

    def test_diagonal_plane_and_unequal_cell_sizes(self):
        result = self.gradients(plane(column_rise=30, row_rise=80),
                                cell_size_y_metres=200)
        self.assert_plane(result, 0.3, 0.4)
        self.assertEqual(result.slope_percent[2][2], 50)

    def test_negative_direction_and_array_row_orientation(self):
        self.assert_plane(self.gradients(plane(column_rise=-10, row_rise=-20)), -0.1, -0.2)

    def test_nonplanar_corner_and_middle_weights_follow_horn_stencil(self):
        elevations = [[0, 0, 8], [0, 500, 4], [0, 0, 0]]
        result = self.gradients(elevations, cell_size_x_metres=1, cell_size_y_metres=1)
        self.assertEqual(result.gradient_columns[1][1], 2)
        self.assertEqual(result.gradient_rows[1][1], -1)
        self.assertAlmostEqual(result.slope_percent[1][1], math.sqrt(5) * 100)
        self.assertAlmostEqual(result.slope_degrees[1][1], math.degrees(math.atan(math.sqrt(5))))

    def test_constant_vertical_offset_is_invariant(self):
        original = self.gradients(plane(column_rise=10, row_rise=20))
        shifted = self.gradients(plane(column_rise=10, row_rise=20, offset=1_000_000))
        self.assertEqual(original, shifted)

    def test_explicit_metric_height_scale_conversion(self):
        metres = self.gradients(plane(column_rise=10, row_rise=20))
        centimetres = self.gradients(plane(column_rise=1000, row_rise=2000),
                                     vertical_scale_metres=0.01)
        self.assertEqual(metres, centimetres)
        feet = self.gradients(plane(column_rise=10 / 0.3048), vertical_scale_metres=0.3048)
        self.assert_plane(feet, 0.1, 0)

    def test_common_metric_scaling_leaves_slope_unchanged(self):
        original = self.gradients(plane(column_rise=10, row_rise=20))
        scaled = self.gradients(plane(column_rise=10, row_rise=20),
                                cell_size_x_metres=1000, cell_size_y_metres=1000,
                                vertical_scale_metres=10)
        self.assertEqual(original, scaled)

    def test_every_missing_stencil_position_masks_centre_including_zero_weights(self):
        for row_index in range(3):
            for column_index in range(3):
                for missing in (None, float('nan'), float('inf'), -float('inf'), -9999):
                    with self.subTest(position=(row_index, column_index), missing=missing):
                        elevations = plane(height=3, width=3, column_rise=10)
                        elevations[row_index][column_index] = missing
                        result = self.gradients(elevations, nodata=-9999)
                        self.assertFalse(any(any(row) for row in result.valid_mask))
                        self.assertTrue(all(value is None for row in result.slope_percent for value in row))

    def test_hole_propagates_exactly_one_cell_without_filling(self):
        elevations = plane(height=7, width=7, column_rise=10)
        elevations[3][3] = None
        result = self.gradients(elevations)
        for row_index in range(7):
            for column_index in range(7):
                expected = (0 < row_index < 6 and 0 < column_index < 6
                            and not (2 <= row_index <= 4 and 2 <= column_index <= 4))
                self.assertEqual(result.valid_mask[row_index][column_index], expected)
                for values in (result.gradient_columns, result.gradient_rows,
                               result.slope_percent, result.slope_degrees):
                    self.assertEqual(values[row_index][column_index] is not None, expected)

    def test_explicit_mask_matches_missing_samples_and_preserves_inputs(self):
        elevations = plane(height=7, width=7, column_rise=10)
        mask = [[True] * 7 for row_index in range(7)]
        mask[3][3] = False
        original = copy.deepcopy((elevations, mask))
        masked = self.gradients(elevations, valid_mask=mask)
        self.assertEqual((elevations, mask), original)
        elevations[3][3] = None
        self.assertEqual(masked, self.gradients(elevations))

    def test_all_missing_and_all_masked_produce_no_valid_cells(self):
        for result in (self.gradients([[None] * 3 for row_index in range(3)]),
                       self.gradients(plane(height=3, width=3),
                                      valid_mask=[[False] * 3 for row_index in range(3)])):
            self.assertFalse(any(any(row) for row in result.valid_mask))

    def test_zero_nodata_is_explicit_and_nan_nodata_is_supported(self):
        result = self.gradients(plane(height=3, width=3), nodata=0)
        self.assertFalse(result.valid_mask[1][1])
        elevations = plane(height=3, width=3, offset=10)
        elevations[1][1] = float('nan')
        self.assertFalse(self.gradients(elevations, nodata=float('nan')).valid_mask[1][1])

    def test_numeric_sentinel_is_only_missing_when_explicitly_declared(self):
        elevations = plane(height=3, width=3, offset=-9999)
        self.assertTrue(self.gradients(elevations).valid_mask[1][1])
        self.assertFalse(self.gradients(elevations, nodata=-9999).valid_mask[1][1])

    def test_invalid_cell_sizes_and_height_scale_fail(self):
        for parameter in ('cell_size_x_metres', 'cell_size_y_metres', 'vertical_scale_metres'):
            for invalid in (0, -1, float('nan'), float('inf'), -float('inf'), True, '100', None,
                            10 ** 1000):
                with self.subTest(parameter=parameter, invalid=invalid), self.assertRaises(ValueError):
                    self.gradients(plane(), **{parameter: invalid})

    def test_vertical_scale_is_required(self):
        with self.assertRaises(TypeError):
            horn3x3(plane(), cell_size_x_metres=100, cell_size_y_metres=100)

    def test_malformed_arrays_and_nonnumeric_samples_fail(self):
        invalid_arrays = (None, [], [[], [], []], [1, 2, 3], '123', [[0] * 3] * 2,
                          [[0] * 2] * 3, [[0] * 3, [0] * 4, [0] * 3],
                          [[0] * 3, '000', [0] * 3])
        for elevations in invalid_arrays:
            with self.subTest(elevations=elevations), self.assertRaises(ValueError):
                self.gradients(elevations)
        for invalid in (True, '10', complex(1, 2), object(), [10], 10 ** 1000):
            elevations = plane(height=3, width=3)
            elevations[1][1] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.gradients(elevations)

    def test_malformed_masks_and_nodata_fail(self):
        for mask in ([], [[True] * 3] * 2, [[1] * 3] * 3, [[None] * 3] * 3,
                     [[True] * 3, [True] * 2, [True] * 3]):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                self.gradients(plane(height=3, width=3), valid_mask=mask)
        for invalid in (True, '-9999', object()):
            with self.subTest(nodata=invalid), self.assertRaises(ValueError):
                self.gradients(plane(), nodata=invalid)

    def test_diagnostic_cell_cap(self):
        with self.assertRaises(ValueError):
            self.gradients([[0] * 3] * (MAX_CELLS // 3 + 1))

    def test_arithmetic_overflow_fails_instead_of_returning_nonfinite_slopes(self):
        elevations = [[-1e308, 0, 1e308] for row_index in range(3)]
        with self.assertRaises(ValueError):
            self.gradients(elevations)
        with self.assertRaises(ValueError):
            self.gradients(plane(column_rise=10), vertical_scale_metres=1e308)

    def test_deterministic_immutable_and_never_approves_physics_or_metadata(self):
        elevations = tuple(tuple(row) for row in plane(column_rise=10))
        result = self.gradients(elevations)
        for repeat_index in range(5):
            self.assertEqual(result, self.gradients(elevations))
        self.assertFalse(result.slope_enabled)
        self.assertFalse(result.vertical_units_verified)
        self.assertFalse(result.vertical_datum_verified)
        self.assertFalse(result.physics_approved)
        with self.assertRaises(FrozenInstanceError):
            result.slope_enabled = True
        with self.assertRaises(TypeError):
            result.slope_percent[1][1] = 5


if __name__ == '__main__':
    unittest.main()
