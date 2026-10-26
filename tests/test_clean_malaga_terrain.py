import hashlib
from pathlib import Path
import tempfile
import unittest

from eval.prepare_clean_malaga_terrain import describe_vertical, file_record, screen_values


DESCRIPTION = b'''<CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
 xmlns:swe="http://www.opengis.net/swe/2.0"><wcs:CoverageId>Elevacion25830_25</wcs:CoverageId>
 <swe:Quantity><swe:nilValues/><swe:uom code="W.m-2.Sr-1"/></swe:Quantity></CoverageDescriptions>'''


class CleanTerrainTests(unittest.TestCase):
    def test_default_looking_uom_does_not_authorize_physics(self):
        result = describe_vertical(DESCRIPTION)
        self.assertEqual(result['reported_band_uom'], 'W.m-2.Sr-1')
        self.assertFalse(result['vertical_units_verified'])
        self.assertFalse(result['vertical_datum_verified'])
        self.assertFalse(result['slope_enabled'])

    def test_even_metre_uom_alone_does_not_confirm_datum(self):
        result = describe_vertical(DESCRIPTION.replace(b'W.m-2.Sr-1', b'm'))
        self.assertFalse(result['slope_enabled'])
        self.assertFalse(result['vertical_datum_verified'])

    def test_xml_identity_size_and_external_declarations_rejected(self):
        for data in (DESCRIPTION.replace(b'Elevacion25830_25', b'Elevacion25830_5'),
                     b'<!DOCTYPE test>' + DESCRIPTION, b'<!ENTITY test>' + DESCRIPTION,
                     b' ' * 1_000_001):
            with self.assertRaises(ValueError):
                describe_vertical(data)

    def test_numeric_screen_keeps_real_zero_and_does_not_mutate(self):
        values = [0, 12, 255, 1450]
        before = values[:]
        result = screen_values(values)
        self.assertEqual(values, before)
        self.assertEqual(result['zero_values'], 1)
        self.assertEqual(result['numeric_min'], 0)
        self.assertEqual(result['numeric_max'], 1450)

    def test_sentinels_nonfinite_bool_and_extremes_fail_without_repair(self):
        for invalid in (-32767, -32768, -9999, -501, 4001, float('nan'), float('inf'), True, '100'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                screen_values([100, invalid])
        with self.assertRaises(ValueError):
            screen_values([])

    def test_artifact_pins_size_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'source.tif'
            path.write_bytes(b'fixture')
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(file_record(root, 'source.tif', expected)[1]['sha256'], expected)
            with self.assertRaises(ValueError):
                file_record(root, 'source.tif', '0' * 64)
            with self.assertRaises(ValueError):
                file_record(root, 'source.tif', limit=1)
            with self.assertRaises(ValueError):
                file_record(root, '../escape')
            link = root / 'link.tif'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                file_record(root, 'link.tif')


if __name__ == '__main__':
    unittest.main()
