import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval.prepare_malaga_landscape import (
    CRS, GATES, METADATA, OUTPUT_DIRECTORY, SOURCE_DIRECTORY, STUDY_BBOX,
    classify_fuel, common_grid, encoded, file_record, pinned_source,
    prepare_landscape, terrain_diagnostics,
)


ROOT = Path(__file__).resolve().parents[1]
RASTER_AVAILABLE = (importlib.util.find_spec('numpy') is not None
                    and importlib.util.find_spec('rasterio') is not None)


class LandscapeTests(unittest.TestCase):
    def test_outward_metric_alignment_covers_retained_envelope(self):
        grid = common_grid()
        self.assertEqual(grid['crs'], 'EPSG:25830')
        self.assertEqual(grid['bounds'], [297000, 4034000, 315500, 4054500])
        self.assertEqual((grid['width'], grid['height']), (37, 41))
        self.assertEqual(grid['study_bbox'], list(STUDY_BBOX))
        self.assertEqual(grid['transform'], [500, 0, 297000, 0, -500, 4054500])
        self.assertIn('not an exact episode', grid['envelope_origin'])

    def test_grid_rejects_bad_bounds(self):
        for bbox in ([0, 0, 0, 1], [1, 2, 3], [0, 0, float('nan'), 2],
                     [0, 0, 1e9, 1e9], [False, 0, 1, 1]):
            with self.assertRaises(ValueError):
                common_grid(bbox)

    def test_fuel_nodata_unknown_and_masked_codes_are_not_measured_fuel(self):
        classes, mask = classify_fuel([0, 2, 4, 5, 7, 8, 9, 10, 11, 255, 4],
                                     [True] * 10 + [False])
        self.assertEqual(classes, [0, 2, 4, 5, 7, 8, 9, 10, 0, 0, 0])
        self.assertEqual(mask, [0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0])
        with self.assertRaises(ValueError):
            classify_fuel([2], [])

    def test_terrain_diagnostics_do_not_release_quarantine(self):
        values = [-32767, -2000, -999, 255, 123, float('nan'), float('inf'), 100]
        flags, diagnostics = terrain_diagnostics(values, [True] * 7 + [False], 255)
        self.assertEqual(flags, [1, 1, 0, 1, 0, 1, 1, 1])
        self.assertEqual(diagnostics['equal_minus32767'], 1)
        self.assertEqual(diagnostics['below_minus1000'], 2)
        self.assertEqual(diagnostics['equal_declared_nodata'], 1)
        self.assertEqual(diagnostics['nonfinite'], 2)
        self.assertFalse(diagnostics['quarantine_released'])
        self.assertFalse(diagnostics['unflagged_is_verified_elevation'])
        self.assertEqual(values[0], -32767)

    def test_plausible_values_and_missing_nodata_cannot_clear_quarantine(self):
        flags, diagnostics = terrain_diagnostics([1, 100, 1446], [True] * 3, None)
        self.assertEqual(flags, [0, 0, 0])
        self.assertFalse(diagnostics['quarantine_released'])
        self.assertFalse(diagnostics['unflagged_is_verified_elevation'])
        with self.assertRaises(ValueError):
            terrain_diagnostics([1], [], None)

    def test_import_and_pure_helpers_do_not_require_raster_packages(self):
        import builtins
        original_import = builtins.__import__

        def without_rasters(name, *args, **kwargs):
            if name.split('.')[0] in ('numpy', 'rasterio', 'pyproj'):
                raise AssertionError('Optional raster dependency imported eagerly')
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location('landscape_without_rasters',
                                                    ROOT / 'eval/prepare_malaga_landscape.py')
        module = importlib.util.module_from_spec(spec)
        with patch('builtins.__import__', side_effect=without_rasters):
            spec.loader.exec_module(module)
            self.assertEqual(module.common_grid()['width'], 37)
            self.assertEqual(module.classify_fuel([0, 4], [True, True]), ([0, 4], [0, 1]))


@unittest.skipUnless(RASTER_AVAILABLE, 'Optional rasterio/numpy not installed')
class LandscapeRasterTests(unittest.TestCase):
    def setUp(self):
        (ROOT / OUTPUT_DIRECTORY).mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / OUTPUT_DIRECTORY, prefix='fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / SOURCE_DIRECTORY).mkdir(parents=True)
        (self.root / METADATA).parent.mkdir(parents=True)
        (self.root / 'eval').mkdir()
        (self.root / 'eval/prepare_malaga_landscape.py').write_bytes(
            (ROOT / 'eval/prepare_malaga_landscape.py').read_bytes())
        self.metadata = dict(version='wildfire-malaga-terrain-fuel-search-v1',
                             actor_input_eligible=False, historical_replay_ready=False)
        self.create_sources()

    def create_sources(self):
        import numpy
        import rasterio
        from rasterio.warp import transform

        centre_east, centre_north = transform(CRS, 'EPSG:3035', [307000], [4044500])
        west = int(centre_east[0] // 250) * 250 - 1000
        north = int(centre_north[0] // 250) * 250 + 1000
        fuel = numpy.array([[2, 0, 4, 5, 7, 8, 9, 10],
                            [0, 0, 0, 0, 0, 0, 0, 0],
                            [4, 4, 11, 2, 4, 4, 4, 4],
                            [7, 7, 7, 0, 10, 9, 8, 2]] * 2, dtype='uint8')
        terrain = numpy.tile(numpy.array([[-32767, -1500, 100, 255],
                                          [100, 200, 300, 400]], dtype='float32'), (20, 10))
        cases = [('fuel', 'effis-fuel-aoi-native.tif', fuel, 'EPSG:3035',
                  rasterio.Affine(250, 0, west, 0, -250, north), 0),
                 ('terrain', 'pnoa2020-21-dtm-aoi25m.tif', terrain, CRS,
                  rasterio.Affine(25.00000000000001, 0, 307000,
                                  0, -25.000000000000174, 4044500), 255)]
        for kind, filename, values, crs, affine, nodata in cases:
            path = self.root / SOURCE_DIRECTORY / filename
            with rasterio.open(path, 'w', driver='GTiff', width=values.shape[1],
                               height=values.shape[0], count=1, dtype=values.dtype,
                               crs=crs, transform=affine, nodata=nodata) as destination:
                destination.write(values, 1)
            record = file_record(path, self.root)
            self.metadata[kind] = dict(filename=filename, bytes=record['bytes'],
                                       sha256=record['sha256'])
        self.metadata['fuel'].update(member='FuelMap2000_NFFL_LAEA.tif', citation_year=2017)
        self.metadata['terrain'].update(source_epoch_evidence='Fixture pre-fire collection claim',
                                        publication_is_pre_incident=False)
        (self.root / METADATA).write_bytes(encoded(self.metadata))

    def test_preparation_preserves_nearest_classes_unknowns_and_quarantine(self):
        import numpy
        import rasterio
        from rasterio.warp import transform

        audit = prepare_landscape(self.root)
        output = self.root / OUTPUT_DIRECTORY
        with rasterio.open(self.root / SOURCE_DIRECTORY / self.metadata['fuel']['filename']) as source:
            raw = source.read(1)
            inverse = ~source.transform
            with rasterio.open(output / 'fuel_classes.tif') as prepared:
                fuel = prepared.read(1)
                for row, column in numpy.ndindex(fuel.shape):
                    east, north = prepared.xy(row, column)
                    source_east, source_north = transform(CRS, source.crs, [east], [north])
                    source_column, source_row = inverse * (float(source_east[0]), float(source_north[0]))
                    source_column, source_row = math.floor(source_column), math.floor(source_row)
                    expected = (int(raw[source_row, source_column])
                                if 0 <= source_row < raw.shape[0] and 0 <= source_column < raw.shape[1]
                                else 0)
                    if expected not in range(1, 11):
                        expected = 0
                    self.assertEqual(int(fuel[row, column]), expected)
        self.assertGreater(audit['fuel']['valid_cells'], 0)
        self.assertGreater(audit['fuel']['unknown_cells'], 0)
        self.assertEqual(audit['fuel']['source']['unrecognized_unmasked_cells'], 2)
        for record in audit['output_files']:
            self.assertEqual(record, file_record(self.root / record['path'], self.root))
            with rasterio.open(self.root / record['path']) as prepared:
                self.assertEqual(prepared.shape, (41, 37))
                self.assertEqual(prepared.crs.to_string(), CRS)
                self.assertEqual(list(prepared.transform)[:6], audit['grid']['transform'])
                self.assertEqual(prepared.tags()['physics_input_eligible'], 'false')
        with rasterio.open(output / 'terrain_valid_mask.tif') as prepared:
            self.assertIsNone(prepared.nodata)
            self.assertTrue(numpy.all(prepared.read(1) == 0))
        with rasterio.open(output / 'terrain_suspect_mask.tif') as prepared:
            self.assertTrue(numpy.any(prepared.read(1) == 1))
            self.assertTrue(numpy.any(prepared.read(1) == 255))
        for field in ('actor_input_eligible', 'simulator_input_eligible', 'physics_input_eligible',
                      'historical_replay_ready'):
            self.assertFalse(audit[field])
        self.assertFalse(any(audit['runtime_usage'].values()))
        self.assertFalse(audit['terrain']['quarantine_released'])
        self.assertFalse(audit['checks']['physics_ready_elevation_emitted'])
        self.assertEqual(audit['remaining_gates'], GATES)
        self.assertEqual(json.loads((output / 'audit.json').read_bytes()), audit)
        self.assertEqual(len(list(output.iterdir())), 7)
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            prepare_landscape(self.root)

    def test_hash_mismatch_fails_before_any_output_is_written(self):
        path = self.root / SOURCE_DIRECTORY / self.metadata['fuel']['filename']
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            prepare_landscape(self.root)
        self.assertFalse((self.root / OUTPUT_DIRECTORY).exists())

    def test_pinned_source_rejects_path_escape_and_symlink(self):
        descriptor = dict(self.metadata['fuel'], filename='../escape.tif')
        with self.assertRaisesRegex(ValueError, 'flat source'):
            pinned_source(self.root, SOURCE_DIRECTORY, descriptor)
        path = self.root / SOURCE_DIRECTORY / 'link.tif'
        path.symlink_to(self.metadata['fuel']['filename'])
        descriptor = dict(self.metadata['fuel'], filename='link.tif')
        with self.assertRaisesRegex(ValueError, 'non-symlink'):
            pinned_source(self.root, SOURCE_DIRECTORY, descriptor)


if __name__ == '__main__':
    unittest.main()
