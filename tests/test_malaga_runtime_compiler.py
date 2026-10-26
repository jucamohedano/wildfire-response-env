import builtins
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval import compile_malaga_runtime_data as compiler
from envs.wildfire_response.regional.malaga.pack import pack_digest
from envs.wildfire_response.regional.ops.generator import digest


ROOT = Path(__file__).resolve().parents[1]
RASTER_AVAILABLE = importlib.util.find_spec('rasterio') is not None


def encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode()


def descriptor(root, relative):
    data = (root / relative).read_bytes()
    return dict(path=relative, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def weather_fixture():
    start = datetime(2021, 9, 8, 0, 30, tzinfo=timezone.utc)
    records = []
    for index in range(384):
        moment = start + timedelta(minutes=30 * index)
        records.append(dict(observed_at_utc=moment.strftime('%Y-%m-%dT%H:%M:%SZ'),
                            temperature_c=23.5, relative_humidity_percent=55.0,
                            wind_speed_m_s=2.5, wind_direction_degrees=180.0,
                            precipitation_mm=0.0, clock_ambiguous=False,
                            received_at_utc=None, available_at_utc=None,
                            private_future_outcome='MUST_NOT_COPY',
                            html='<html>SECRET_CSRF</html>'))
    return dict(version='malaga-weather-normalization-v1', status='source_clock_normalized',
                station='MA04 Estepona', actor_input_eligible=False,
                historical_replay_ready=False, records=records)


class CompilerPureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='malaga-compiler-pure-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_strict_json_rejects_duplicates_nonfinite_nesting_and_oversize(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}',
                     b'[]', b'{' + b' ' * compiler.MAX_JSON_BYTES + b'}',
                     b'{"a":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'):
            with self.subTest(data=data[:32]):
                with self.assertRaises(ValueError):
                    compiler.load_json(data)

    def test_path_rejects_escapes_symlink_parents_and_dangling_links(self):
        (self.root / 'link').symlink_to('/tmp', target_is_directory=True)
        (self.root / 'dangling').symlink_to(self.root / 'missing')
        for relative in ('../escape', '/etc/passwd', 'a/../b', 'a//b', './file',
                         'a\\b', 'link/file', 'dangling', '<html>', ''):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                compiler.safe_path(self.root, relative)

    def test_byte_hash_size_count_and_total_caps(self):
        path = self.root / 'source.json'
        path.write_bytes(b'{}')
        info = descriptor(self.root, path.name)
        files = compiler.PinnedFiles(self.root)
        self.assertEqual(files.verify(info)[1], info)
        for changed in (dict(info, sha256='0' * 64), dict(info, bytes=3),
                        dict(info, bytes=True), dict(info, sha256='invalid'),
                        dict(info, extra='field')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                files.verify(changed)
        with patch.object(compiler, 'MAX_FILE_BYTES', 1), self.assertRaises(ValueError):
            compiler.PinnedFiles(self.root).verify(info)
        with patch.object(compiler, 'MAX_TOTAL_BYTES', 1), self.assertRaises(ValueError):
            compiler.PinnedFiles(self.root).verify(info)
        with patch.object(compiler, 'MAX_FILES', 0), self.assertRaises(ValueError):
            compiler.PinnedFiles(self.root).verify(info)

    def test_weather_contract_copies_only_six_fields_and_preserves_midnight(self):
        result = compiler.weather_records(weather_fixture())
        self.assertEqual(result['source_clock'], 'UTC')
        self.assertEqual(result['records'][47]['observed_at_utc'], '2021-09-09T00:00:00Z')
        self.assertEqual(result['records'][-1]['observed_at_utc'], '2021-09-16T00:00:00Z')
        self.assertEqual(set(result['records'][0]), {
            'observed_at_utc', 'temperature_c', 'relative_humidity_percent',
            'wind_speed_m_s', 'wind_direction_degrees', 'precipitation_mm'})
        self.assertNotIn('SECRET_CSRF', json.dumps(result))
        self.assertNotIn('MUST_NOT_COPY', json.dumps(result))

    def test_weather_rejects_null_nonfinite_bool_bad_range_and_ambiguous_clocks(self):
        changes = [('temperature_c', None), ('temperature_c', float('nan')),
                   ('temperature_c', True), ('relative_humidity_percent', 101),
                   ('wind_speed_m_s', -1), ('wind_direction_degrees', 361),
                   ('precipitation_mm', -1), ('clock_ambiguous', True),
                   ('received_at_utc', '2021-09-08T01:00:00Z'),
                   ('available_at_utc', '2021-09-08T01:00:00Z'),
                   ('observed_at_utc', '2021-09-08T00:30:00+00:00')]
        for field, value in changes:
            document = weather_fixture()
            document['records'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                compiler.weather_records(document)
        for changed in ('missing', 'duplicate', 'unsorted', 'station'):
            document = weather_fixture()
            if changed == 'missing':
                document['records'].pop()
            elif changed == 'duplicate':
                document['records'][1] = document['records'][0]
            elif changed == 'unsorted':
                document['records'].reverse()
            else:
                document['station'] = 'Different station'
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                compiler.weather_records(document)

    def test_module_import_does_not_require_optional_packages(self):
        original_import = builtins.__import__

        def no_optional(name, *args, **kwargs):
            if name.split('.')[0] in ('numpy', 'rasterio', 'pyproj'):
                raise AssertionError('Optional package imported eagerly')
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location('compiler_stdlib',
                                                    ROOT / 'eval/compile_malaga_runtime_data.py')
        module = importlib.util.module_from_spec(spec)
        with patch('builtins.__import__', side_effect=no_optional):
            spec.loader.exec_module(module)
            self.assertEqual(module.weather_records(weather_fixture())['station'], 'MA04 Estepona')

    def test_fresh_output_file_directory_and_protected_paths(self):
        self.assertEqual(compiler.output_path(self.root, '.runtime/new', []),
                         self.root / '.runtime/new/inputs.json')
        self.assertEqual(compiler.output_path(self.root, '.runtime/new.json', []),
                         self.root / '.runtime/new.json')
        (self.root / '.runtime').mkdir()
        (self.root / '.runtime/existing').mkdir()
        for path, records in (('.runtime/existing', []), ('outside.json', []),
                              ('/tmp/elsewhere.json', []),
                              ('.runtime/source/new.json', [dict(path='.runtime/source/weather.json')])):
            with self.subTest(path=path), self.assertRaises(ValueError):
                compiler.output_path(self.root, path, records)


@unittest.skipUnless(RASTER_AVAILABLE, 'Optional rasterio not installed')
class CompilerRasterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='malaga-runtime-compiler-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pins = {}
        self.addCleanup(patch.stopall)
        patch.object(compiler, 'SNAPSHOT_PINS', self.pins).start()
        self.raw = '.runtime/source-fixture/raw.html'
        self.write(self.raw, b'<html>SECRET_CSRF private future perimeter MUST_NOT_COPY</html>')
        self.write(compiler.WEATHER_FILE, encoded(weather_fixture()))
        self.rasters = {}
        for name in compiler.RASTER_NAMES:
            values = [[4 if name == 'fuel_classes' else 0 if name.startswith('terrain_') else 1
                       for column in range(37)] for row in range(41)]
            if name in ('fuel_classes', 'fuel_valid_mask', 'fuel_source_coverage_mask'):
                values[0][0] = 0
            if name in ('fuel_classes', 'fuel_valid_mask'):
                values[1][1] = 0
            self.rasters[name] = values
            self.write_raster(name, values)
        episode = dict(version='wildfire-malaga-episode-pack-v1', role='simulation_configuration',
                       actor_input_eligible=False, source_bundle_sha256='0' * 64,
                       region_bbox_lon_lat=[-5.26, 36.44, -5.06, 36.61],
                       detection_local='2021-09-08T21:35:00', assumed_ground_transit_minutes=8)
        episode['sha256'] = pack_digest(episode)
        self.snapshot(compiler.EPISODE, episode)
        self.landscape = dict(version='wildfire-malaga-landscape-preparation-v1',
                              status='fuel_prepared_terrain_quarantined', checks={},
                              terrain=dict(quarantine_released=False),
                              grid=dict(compiler.GRID, resolution_metres=500),
                              input_files=[descriptor(self.root, self.raw)],
                              output_files=[descriptor(self.root, compiler.RASTER_DIRECTORY + name + '.tif')
                                            for name in compiler.RASTER_NAMES],
                              private_outcome='MUST_NOT_COPY')
        self.weather = dict(version='malaga-weather-normalization-v1', status='source_clock_normalized',
                            checks={}, input_files=[descriptor(self.root, self.raw)],
                            output_files=[descriptor(self.root, compiler.WEATHER_FILE)])
        self.refresh_audits()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def snapshot(self, relative, document):
        self.write(relative, encoded(document))
        self.pins[relative] = descriptor(self.root, relative)['sha256']

    def refresh_audits(self):
        for relative, local, document in (
                (compiler.LANDSCAPE, '.runtime/malaga-landscape-prepared-v1/audit.json', self.landscape),
                (compiler.WEATHER, '.runtime/malaga-weather-normalized-v1/audit.json', self.weather)):
            self.snapshot(relative, document)
            self.snapshot(local, document)

    def write_raster(self, name, values, **changes):
        import numpy
        import rasterio
        path = self.root / (compiler.RASTER_DIRECTORY + name + '.tif')
        path.parent.mkdir(parents=True, exist_ok=True)
        options = dict(driver='GTiff', count=1, width=37, height=41, dtype='uint8',
                       crs='EPSG:25830', transform=rasterio.Affine(*compiler.GRID['transform']),
                       nodata=0 if name == 'fuel_classes' else 255 if name == 'terrain_suspect_mask' else None)
        options.update(changes)
        with rasterio.open(path, 'w', **options) as destination:
            destination.write(numpy.asarray(values, dtype='uint8'), 1)

    def update_raster_pin(self, name):
        relative = compiler.RASTER_DIRECTORY + name + '.tif'
        for index, record in enumerate(self.landscape['output_files']):
            if record['path'] == relative:
                self.landscape['output_files'][index] = descriptor(self.root, relative)
        self.refresh_audits()

    def assert_failed_without_output(self, message=None):
        with self.assertRaises(ValueError) as caught:
            compiler.compile_inputs(self.root)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertFalse((self.root / compiler.DEFAULT_OUTPUT).exists())

    def test_reproducible_bundle_exact_schema_geometry_and_privacy(self):
        before = {path: (self.root / path).read_bytes() for path in self.pins}
        first = compiler.compile_inputs(self.root)
        second = compiler.compile_inputs(self.root, '.runtime/second')
        content = (self.root / first['path']).read_bytes()
        self.assertEqual(content, (self.root / second['path']).read_bytes())
        payload = json.loads(content)
        from envs.wildfire_response.regional.prepared.data import validate_inputs
        validate_inputs(payload, first['sha256'])
        self.assertEqual(set(payload), {
            'version', 'role', 'grid', 'fuel_classes', 'fuel_valid_mask', 'study_mask',
            'weather', 'episode_start_utc', 'episode_pack', 'provenance', 'slope_enabled',
            'historical_replay', 'assumptions', 'sha256'})
        self.assertEqual(payload['sha256'], digest({key: value for key, value in payload.items() if key != 'sha256'}))
        self.assertEqual(payload['sha256'], first['sha256'])
        self.assertEqual(first['file_sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(payload['episode_start_utc'], '2021-09-08T19:35:00Z')
        self.assertEqual(payload['episode_pack'], json.loads(before[compiler.EPISODE]))
        self.assertEqual(payload['provenance']['episode_pack'], descriptor(self.root, compiler.EPISODE))
        self.assertEqual(len(payload['grid']['centre_lon_lat']), 41)
        self.assertTrue(all(len(row) == 37 for row in payload['grid']['centre_lon_lat']))
        northwest = payload['grid']['northwest_lon_lat']
        first_centre = payload['grid']['centre_lon_lat'][0][0]
        self.assertLess(northwest[0], first_centre[0])
        self.assertGreater(northwest[1], first_centre[1])
        self.assertAlmostEqual(first_centre[0], -5.265836641, places=2)
        self.assertGreater(payload['grid']['centre_lon_lat'][0][-1][0], first_centre[0])
        self.assertLess(payload['grid']['centre_lon_lat'][-1][0][1], first_centre[1])
        self.assertEqual(payload['fuel_classes'][1][1], 0)
        self.assertEqual(payload['fuel_valid_mask'][1][1], 0)
        self.assertEqual(payload['study_mask'][1][1], 1)
        self.assertIn('Synthetic scrub fallback', payload['assumptions']['unknown_fuel_physics'])
        self.assertFalse(payload['slope_enabled'])
        self.assertFalse(payload['historical_replay'])
        for forbidden in ('SECRET_CSRF', 'MUST_NOT_COPY', '<html>', 'private_outcome', 'private_future_outcome'):
            self.assertNotIn(forbidden, content.decode())
        self.assertLess(len(content), 200_000)
        self.assertEqual((self.root / first['path']).stat().st_mode & 0o777, 0o600)
        for relative, data in before.items():
            self.assertEqual((self.root / relative).read_bytes(), data)
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            compiler.compile_inputs(self.root)

    def test_corrupt_snapshot_prepared_weather_and_even_excluded_raw_html(self):
        for relative in (compiler.LANDSCAPE, compiler.WEATHER, compiler.EPISODE,
                         compiler.WEATHER_FILE, self.raw,
                         compiler.RASTER_DIRECTORY + 'terrain_suspect_mask.tif'):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(relative=relative):
                self.assert_failed_without_output()
            path.write_bytes(original)

    def test_missing_file_symlink_file_and_symlink_directory(self):
        path = self.root / compiler.WEATHER_FILE
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):
            compiler.compile_inputs(self.root)
        target = self.root / 'other.json'
        target.write_bytes(original)
        path.symlink_to(target)
        self.assert_failed_without_output('symlink')
        path.unlink()
        path.write_bytes(original)
        directory = path.parent
        moved = directory.with_name('moved-weather')
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        self.assert_failed_without_output('symlink')

    def test_reject_cross_grid_transform_crs_and_scale(self):
        import rasterio
        cases = [dict(transform=rasterio.Affine(500, 0, 297500, 0, -500, 4054500)),
                 dict(crs='EPSG:4326')]
        for changes in cases:
            self.write_raster('fuel_classes', self.rasters['fuel_classes'], **changes)
            self.update_raster_pin('fuel_classes')
            with self.subTest(changes=changes):
                self.assert_failed_without_output('grid')
        self.write_raster('fuel_classes', self.rasters['fuel_classes'])
        with rasterio.open(self.root / (compiler.RASTER_DIRECTORY + 'fuel_classes.tif'), 'r+') as source:
            source.scales = (2.0,)
        self.update_raster_pin('fuel_classes')
        self.assert_failed_without_output('grid')

    def test_reject_cross_grid_dimensions_and_excessive_raster_blocks(self):
        values = [row[:-1] for row in self.rasters['fuel_classes']]
        self.write_raster('fuel_classes', values, width=36)
        self.update_raster_pin('fuel_classes')
        self.assert_failed_without_output('grid')
        self.write_raster('fuel_classes', self.rasters['fuel_classes'],
                          tiled=True, blockxsize=512, blockysize=512)
        self.update_raster_pin('fuel_classes')
        self.assert_failed_without_output('grid')

    def test_reject_class_mask_coverage_and_quarantine_conflicts(self):
        for name, value in (('fuel_classes', 11), ('fuel_valid_mask', 2),
                            ('fuel_valid_mask', 0), ('fuel_source_coverage_mask', 0),
                            ('terrain_valid_mask', 1)):
            values = [row[:] for row in self.rasters[name]]
            values[2][2] = value
            self.write_raster(name, values)
            self.update_raster_pin(name)
            with self.subTest(name=name, value=value):
                self.assert_failed_without_output()
            self.write_raster(name, self.rasters[name])
            self.update_raster_pin(name)

    def test_reject_external_raster_reference_without_opening_it(self):
        relative = compiler.RASTER_DIRECTORY + 'fuel_classes.tif'
        self.write(relative, b'<VRTDataset><SourceFilename>/etc/passwd</SourceFilename></VRTDataset>')
        self.update_raster_pin('fuel_classes')
        self.assert_failed_without_output('GeoTIFF')

    def test_reject_changed_local_audit_and_inventory_escape(self):
        local = '.runtime/malaga-landscape-prepared-v1/audit.json'
        self.snapshot(local, dict(self.landscape, status='changed'))
        self.assert_failed_without_output('Local audit')
        self.refresh_audits()
        self.landscape['input_files'][0]['path'] = '../escape'
        self.refresh_audits()
        self.assert_failed_without_output('escapes')

    def test_reject_extra_episode_fields_and_wrong_episode_digest(self):
        episode = json.loads((self.root / compiler.EPISODE).read_bytes())
        self.snapshot(compiler.EPISODE, dict(episode, private_future_outcome='MUST_NOT_COPY'))
        self.assert_failed_without_output('exact')
        self.snapshot(compiler.EPISODE, dict(episode, sha256='0' * 64))
        self.assert_failed_without_output('digest')

    def test_reject_output_symlink_and_oversized_bundle(self):
        directory = self.root / '.runtime/malaga-prepared-inputs-v1'
        directory.symlink_to(self.root / '.runtime/source-fixture', target_is_directory=True)
        self.assert_failed_without_output('symlink')
        directory.unlink()
        with patch.object(compiler, 'MAX_OUTPUT_BYTES', 100):
            self.assert_failed_without_output('output cap')

    def test_rasterio_fallback_matches_pyproj_grid_without_runtime_dependency(self):
        expected = compiler.geographic_grid()
        original_import = builtins.__import__

        def no_pyproj(name, *args, **kwargs):
            if name.split('.')[0] == 'pyproj':
                raise ImportError('Fixture omits optional pyproj')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=no_pyproj):
            actual = compiler.geographic_grid()
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
