import importlib.util
import unittest

from eval.validate_malaga_terrain_metadata import validate_encoding


AVAILABLE = importlib.util.find_spec('numpy') is not None


@unittest.skipUnless(AVAILABLE, 'Optional NumPy data dependency not installed')
class TerrainEncodingTests(unittest.TestCase):
    def test_shared_encoding_agrees_only_at_unit_scale(self):
        import numpy
        heights = numpy.full((860, 780), 100.49, dtype='float32')
        integer = numpy.full(heights.shape, 100, dtype='int16')
        result = validate_encoding(heights, integer)
        self.assertEqual(result['compared_cells'], 670800)
        self.assertEqual(result['applied_height_scale'], 1)
        self.assertTrue(result['within_half_metre_rounding'])
        self.assertNotIn('physics_approval', result)

    def test_different_scales_and_excess_quantization_are_rejected(self):
        import numpy
        heights = numpy.full((860, 780), 100.75, dtype='float32')
        for integer_value in (100, 1000, 1):
            integer = numpy.full(heights.shape, integer_value, dtype='int16')
            with self.assertRaises(ValueError):
                validate_encoding(heights, integer)

    def test_nonfinite_and_sentinel_values_are_not_repaired(self):
        import numpy
        integer = numpy.full((860, 780), 100, dtype='int16')
        for invalid in (numpy.nan, -32767, -9999):
            heights = numpy.full(integer.shape, 100, dtype='float32')
            heights[20, 20] = invalid
            with self.assertRaises(ValueError):
                validate_encoding(heights, integer)

    def test_wrong_grid_rejected_before_comparison(self):
        import numpy
        with self.assertRaises(ValueError):
            validate_encoding(numpy.zeros((41, 37)), numpy.zeros((41, 37)))


if __name__ == '__main__':
    unittest.main()
