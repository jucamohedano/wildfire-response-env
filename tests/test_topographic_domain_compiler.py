import builtins
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from eval import compile_topographic_domain as compiler
from envs.wildfire_response.regional.malaga.pack import pack_digest
from envs.wildfire_response.regional.ops.generator import digest


PROJECTION_AVAILABLE = importlib.util.find_spec('pyproj') is not None
ASCII_HEADER = b'ncols 780\nnrows 860\nxllcorner 296500\nyllcorner 4033500\ncellsize 25\n'


def ascii_fixture(value='100'):
    return ASCII_HEADER + ((value.encode() + b' ') * 780 + b'\n') * 860


def encoded(document):
    return (json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def prepared_fixture():
    episode = dict(version='wildfire-malaga-episode-pack-v1', role='simulation_configuration',
                   actor_input_eligible=False, source_bundle_sha256='0' * 64,
                   region_bbox_lon_lat=[-5.26, 36.44, -5.06, 36.61],
                   detection_local='2021-09-08T21:35:00', assumed_ground_transit_minutes=8)
    episode['sha256'] = pack_digest(episode)
    fuel = [[4] * 37 for row in range(41)]
    fuel[20][20] = 0
    weather = [dict(observed_at_utc=moment, temperature_c=20, relative_humidity_percent=50,
                    wind_speed_m_s=2, wind_direction_degrees=180, precipitation_mm=0)
               for moment in ('2021-09-08T19:30:00Z', '2021-09-08T21:35:00Z')]
    document = dict(version='wildfire-malaga-prepared-inputs-v1', role='simulation_configuration',
                    grid=dict(width=37, height=41, cell_km=.5, crs='EPSG:25830',
                              transform=[500, 0, 297000, 0, -500, 4054500],
                              northwest_lon_lat=[-5.2699285941, 36.614427263],
                              centre_lon_lat=[[[-5.25 + column / 10000, 36.6 - row / 10000]
                                               for column in range(37)] for row in range(41)]),
                    fuel_classes=fuel,
                    fuel_valid_mask=[[int(value != 0) for value in row] for row in fuel],
                    study_mask=[[1] * 37 for row in range(41)],
                    weather=dict(station='MA04 Estepona', source_clock='UTC', records=weather),
                    episode_pack=episode, episode_start_utc='2021-09-08T19:35:00Z',
                    slope_enabled=False, historical_replay=False, provenance={},
                    assumptions=dict(unknown_fuel_physics='Synthetic scrub fallback; not measured scrub'))
    document['sha256'] = digest(document)
    return document


class RectangleTests(unittest.TestCase):
    def test_all_true_rectangle_includes_every_guard_cell(self):
        self.assertEqual(compiler.largest_supported_rectangle([[True] * 37 for row in range(36)]),
                         dict(left=0, top=0, width=37, height=36))

    def test_internal_hole_cannot_be_in_the_rectangle(self):
        mask = [[1] * 5 for row in range(4)]
        mask[1][2] = 0
        self.assertEqual(compiler.largest_supported_rectangle(mask, 1, 1),
                         dict(left=0, top=2, width=5, height=2))

    def test_all_unknown_and_too_small_domains_fail(self):
        for mask in ([[0] * 37 for row in range(41)],
                     [[1] * 19 for row in range(41)], [[1] * 37 for row in range(15)]):
            with self.subTest(shape=(len(mask), len(mask[0]))), self.assertRaises(ValueError):
                compiler.largest_supported_rectangle(mask)
        self.assertEqual(compiler.largest_supported_rectangle([[1] * 20 for row in range(16)]),
                         dict(left=0, top=0, width=20, height=16))

    def test_ties_prefer_top_then_left_then_width(self):
        fixtures = [
            ([[0, 0, 0, 1, 1], [0, 0, 0, 1, 1], [0] * 5,
              [1, 1, 0, 0, 0], [1, 1, 0, 0, 0]], dict(left=3, top=0, width=2, height=2)),
            ([[1, 1, 0, 1, 1]] * 2, dict(left=0, top=0, width=2, height=2)),
            ([[1, 1, 1, 1]] * 2 + [[1, 1, 0, 0]] * 2,
             dict(left=0, top=0, width=4, height=2)),
        ]
        for mask, expected in fixtures:
            with self.subTest(mask=mask):
                for repeat in range(3):
                    self.assertEqual(compiler.largest_supported_rectangle(mask, 1, 1), expected)

    def test_matches_independent_exhaustive_search(self):
        generator = random.Random(3781)
        for case in range(60):
            mask = [[generator.randrange(2) for column in range(5)] for row in range(4)]
            candidates = []
            for top in range(4):
                for left in range(5):
                    for bottom in range(top + 1, 5):
                        for right in range(left + 1, 6):
                            if all(mask[row][column] for row in range(top, bottom)
                                   for column in range(left, right)):
                                width, height = right - left, bottom - top
                                candidates.append((-width * height, top, left, -width, -height))
            if not candidates:
                with self.assertRaises(ValueError):
                    compiler.largest_supported_rectangle(mask, 1, 1)
                continue
            area, top, left, negative_width, negative_height = min(candidates)
            self.assertEqual(compiler.largest_supported_rectangle(mask, 1, 1),
                             dict(left=left, top=top, width=-negative_width, height=-negative_height))

    def test_malformed_masks_and_minimums_fail(self):
        masks = [[], [[]], [[1], [1, 0]], [[None]], [[2]], [[1.0]], [[float('nan')]],
                 'mask', [[1] * 129], [[1] * 128 for row in range(128)]]
        for mask in masks:
            with self.subTest(mask=repr(mask)[:50]), self.assertRaises(ValueError):
                compiler.largest_supported_rectangle(mask, 1, 1)
        for minimum in (0, -1, True, 1.0, None, 129):
            with self.subTest(minimum=minimum):
                with self.assertRaises(ValueError):
                    compiler.largest_supported_rectangle([[1]], minimum, 1)
                with self.assertRaises(ValueError):
                    compiler.largest_supported_rectangle([[1]], 1, minimum)


class HeightTests(unittest.TestCase):
    def setUp(self):
        self.samples = [[100 + row * 10 + column for column in range(6)] for row in range(6)]
        self.study = [[1, 1], [1, 1]]

    def coarsen(self, samples=None, study=None):
        return compiler.coarsen_heights(self.samples if samples is None else samples,
                                        self.study if study is None else study,
                                        block_size=2, offset=1)

    def test_mean_uses_block_not_halo_and_preserves_inputs(self):
        before = copy.deepcopy(self.samples)
        elevations, support = self.coarsen()
        self.assertEqual(elevations, [[116.5, 118.5], [136.5, 138.5]])
        self.assertEqual(support, [[1, 1], [1, 1]])
        self.assertEqual(self.samples, before)
        self.assertTrue(all(type(value) is float for row in elevations for value in row))

    def test_nonpositive_and_nonfinite_corner_halo_excludes_cell(self):
        for value in (0, -1, float('nan'), float('inf'), -float('inf')):
            self.samples[0][0] = value
            with self.subTest(value=value):
                elevations, support = self.coarsen()
                self.assertEqual(support, [[0, 1], [1, 1]])
                self.assertIsNone(elevations[0][0])
                self.assertEqual(elevations[0][1], 118.5)

    def test_invalid_sample_affects_every_touching_horn_halo(self):
        self.samples[2][2] = 0
        self.assertEqual(self.coarsen(), ([[None, None], [None, None]], [[0, 0], [0, 0]]))

    def test_all_400_samples_and_the_22_by_22_halo_are_mandatory(self):
        samples = [[100.0] * 22 for row in range(22)]
        elevations, support = compiler.coarsen_heights(samples, [[1]], offset=1)
        self.assertEqual((elevations, support), ([[100.0]], [[1]]))
        for row, column in ((10, 10), (0, 0), (21, 21), (0, 10), (10, 21)):
            samples[row][column] = 0
            with self.subTest(point=(row, column)):
                self.assertEqual(compiler.coarsen_heights(samples, [[1]], offset=1),
                                 ([[None]], [[0]]))
            samples[row][column] = 100.0

    def test_study_mask_is_required_but_no_fuel_mask_is_used(self):
        self.assertEqual(self.coarsen(study=[[1, 0], [1, 1]]),
                         ([[116.5, None], [136.5, 138.5]], [[1, 0], [1, 1]]))

    def test_malformed_height_values_and_geometry_fail(self):
        for value in (True, None, '100', {}, 10 ** 1000):
            self.samples[0][0] = value
            with self.subTest(value=repr(value)[:50]), self.assertRaises(ValueError):
                self.coarsen()
        for samples in ([], [[]], [[1, 1], [1]], [[100] * 5 for row in range(5)]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                self.coarsen(samples=samples)
        for options in (dict(block_size=True), dict(block_size=0), dict(offset=0),
                        dict(offset=True), dict(block_size=33), dict(offset=1025)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                compiler.coarsen_heights([[100.0] * 22 for row in range(22)], [[1]], **options)


class AsciiTests(unittest.TestCase):
    def test_exact_source_geometry_and_floating_ascii_precision(self):
        data = ascii_fixture('100.125').replace(b'xllcorner 296500', b'xllcorner 296499.999999999884')
        samples = compiler.read_ascii(data)
        self.assertEqual((len(samples), len(samples[0])), (860, 780))
        self.assertEqual(samples[20][20], 100.125)

    def test_bad_headers_shape_tokens_encoding_and_cap_fail(self):
        data = ascii_fixture()
        malformed = [data.replace(b'ncols 780', b'ncols 781'),
                     data.replace(b'cellsize 25', b'cellsize 5'),
                     data.replace(b'xllcorner 296500', b'xllcorner 297000'),
                     data.replace(b'nrows 860', b'ncols 860'),
                     data.replace(b'cellsize 25', b'cellsize nan'),
                     data.replace(b'cellsize 25\n', b'cellsize 25\nNODATA_value -9999\n'),
                     data + b'100\n', data.replace(b'100 ', b'invalid ', 1),
                     data.replace(b'100 ', b'', 1), data + b'\xff', b'', 'text']
        for invalid in malformed:
            with self.subTest(data=repr(invalid)[:80]), self.assertRaises(ValueError):
                compiler.read_ascii(invalid)
        with patch.object(compiler, 'MAX_ASCII_BYTES', 1), self.assertRaises(ValueError):
            compiler.read_ascii(data)

    def test_import_needs_no_optional_geospatial_packages(self):
        original = builtins.__import__

        def no_optional(name, *args, **kwargs):
            if name.split('.')[0] in ('numpy', 'rasterio', 'pyproj'):
                raise ImportError(name)
            return original(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location('topographic_compiler_import_test', compiler.__file__)
        with patch('builtins.__import__', side_effect=no_optional):
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.largest_supported_rectangle([[1] * 20 for row in range(16)]),
                             dict(left=0, top=0, width=20, height=16))


@unittest.skipUnless(PROJECTION_AVAILABLE, 'Optional offline pyproj not installed')
class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='topographic-compiler-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pins = {}
        self.parent = prepared_fixture()
        self.write(compiler.PREPARED_INPUTS, encoded(self.parent))
        self.write(compiler.TERRAIN_ASCII, ascii_fixture())
        for relative in compiler.INPUT_PINS:
            if relative not in self.pins:
                self.write(relative, b'pinned local metadata fixture')
        self.addCleanup(patch.stopall)
        patch.object(compiler, 'INPUT_PINS', self.pins).start()
        patch.object(compiler, 'PREPARED_INPUTS_SHA256', self.parent['sha256']).start()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.pins[relative] = hashlib.sha256(data).hexdigest()

    def assert_no_output(self):
        self.assertFalse((self.root / compiler.DEFAULT_OUTPUT).parent.exists())

    def test_exact_contract_reproducible_payload_and_fuel_hole_retained_in_parent(self):
        before = (self.root / compiler.PREPARED_INPUTS).read_bytes()
        result = compiler.compile_inputs(self.root)
        content = (self.root / result['path']).read_bytes()
        payload = json.loads(content)
        self.assertEqual(set(payload), {'version', 'role', 'prepared_inputs_sha256', 'crop', 'grid',
                                        'elevations_metres', 'height_support_mask', 'policy',
                                        'provenance', 'sha256'})
        self.assertEqual(payload['version'], compiler.VERSION)
        self.assertEqual(payload['role'], 'simulation_configuration')
        self.assertEqual(payload['prepared_inputs_sha256'], self.parent['sha256'])
        self.assertEqual(payload['crop'], dict(left=0, top=0, width=37, height=41))
        self.assertEqual(payload['grid'], self.parent['grid'])
        self.assertEqual(payload['height_support_mask'], [[1] * 37 for row in range(41)])
        self.assertEqual(payload['elevations_metres'], [[100.0] * 37 for row in range(41)])
        self.assertEqual(payload['policy'], compiler.POLICY)
        self.assertEqual(len(payload['provenance']), 4)
        for record in payload['provenance']:
            raw = (self.root / record['path']).read_bytes()
            self.assertEqual(record, dict(path=record['path'], bytes=len(raw),
                                          sha256=hashlib.sha256(raw).hexdigest()))
        self.assertEqual(payload['sha256'], digest({key: value for key, value in payload.items()
                                                   if key != 'sha256'}))
        self.assertEqual(result['sha256'], payload['sha256'])
        self.assertEqual(result['file_sha256'], hashlib.sha256(content).hexdigest())
        second = compiler.compile_inputs(self.root, '.runtime/second')
        self.assertEqual((self.root / second['path']).read_bytes(), content)
        self.assertEqual((self.root / compiler.PREPARED_INPUTS).read_bytes(), before)
        self.assertEqual(self.parent['fuel_valid_mask'][20][20], 0)
        self.assertEqual(payload['height_support_mask'][20][20], 1)
        self.assertNotIn('fuel_valid_mask', payload)
        self.assertNotIn('episode_pack', payload)

    def test_nonzero_crop_slices_centres_and_offsets_affine_and_northwest(self):
        for row in range(41):
            for column in range(37):
                self.parent['study_mask'][row][column] = int(row >= 3 and column >= 2)
        self.parent.pop('sha256')
        self.parent['sha256'] = digest(self.parent)
        self.write(compiler.PREPARED_INPUTS, encoded(self.parent))
        with patch.object(compiler, 'PREPARED_INPUTS_SHA256', self.parent['sha256']):
            result = compiler.compile_inputs(self.root)
        payload = json.loads((self.root / result['path']).read_bytes())
        self.assertEqual(payload['crop'], dict(left=2, top=3, width=35, height=38))
        grid = payload['grid']
        self.assertEqual(grid['transform'], [500, 0, 298000, 0, -500, 4053000])
        self.assertEqual(grid['centre_lon_lat'], [row[2:] for row in self.parent['grid']['centre_lon_lat'][3:]])
        self.assertEqual((grid['width'], grid['height']), (35, 38))
        from pyproj import Transformer
        longitude, latitude = grid['northwest_lon_lat']
        easting, northing = Transformer.from_crs('EPSG:4326', 'EPSG:25830', always_xy=True).transform(
            longitude, latitude)
        self.assertAlmostEqual(easting, 298000, places=4)
        self.assertAlmostEqual(northing, 4053000, places=4)

    def test_unknown_heights_fail_before_writing(self):
        self.write(compiler.TERRAIN_ASCII, ascii_fixture('0'))
        with self.assertRaisesRegex(ValueError, 'No fully height-supported rectangle'):
            compiler.compile_inputs(self.root)
        self.assert_no_output()

    def test_every_input_file_is_pinned_before_writing(self):
        for relative in self.pins:
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, 'SHA256/size mismatch'):
                compiler.compile_inputs(self.root)
            self.assert_no_output()
            path.write_bytes(original)

    def test_parent_payload_pin_and_source_geometry_fail_before_writing(self):
        original = (self.root / compiler.PREPARED_INPUTS).read_bytes()
        self.parent['role'] = 'unexpected'
        self.write(compiler.PREPARED_INPUTS, encoded(self.parent))
        with self.assertRaises(ValueError):
            compiler.compile_inputs(self.root)
        self.assert_no_output()
        self.write(compiler.PREPARED_INPUTS, original)
        self.write(compiler.TERRAIN_ASCII, ascii_fixture().replace(b'cellsize 25', b'cellsize 5'))
        with self.assertRaises(ValueError):
            compiler.compile_inputs(self.root)
        self.assert_no_output()

    def test_json_rejects_duplicate_nonfinite_and_oversized_inputs(self):
        for raw in (b'{"field":1,"field":2}', b'{"field":NaN}', b'{"field":Infinity}',
                    b'{"field":1e9999}', b'[]', b'{' + b' ' * 1_000_000 + b'}'):
            self.write(compiler.PREPARED_INPUTS, raw)
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                compiler.compile_inputs(self.root)
            self.assert_no_output()

    def test_unsafe_outputs_symlinks_and_existing_output_are_rejected(self):
        (self.root / '.runtime/link').symlink_to(self.root, target_is_directory=True)
        (self.root / '.runtime/dangling').symlink_to(self.root / 'missing')
        for output in ('../escape.json', '/tmp/escape.json', '.runtime/../escape.json',
                       '.runtime/link/new.json', '.runtime/dangling/new.json',
                       '.runtime/malaga-terrain-validation-v2/new.json', 'new.json'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                compiler.compile_inputs(self.root, output)
            self.assert_no_output()
        result = compiler.compile_inputs(self.root)
        original = (self.root / result['path']).read_bytes()
        with self.assertRaises(ValueError):
            compiler.compile_inputs(self.root)
        self.assertEqual((self.root / result['path']).read_bytes(), original)

    def test_symlink_inputs_and_root_are_rejected(self):
        relative = next(path for path in self.pins if path.endswith('.xml'))
        path = self.root / relative
        backup = path.with_suffix('.backup')
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaises(ValueError):
            compiler.compile_inputs(self.root)
        self.assert_no_output()
        path.unlink()
        backup.rename(path)
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            compiler.compile_inputs(alias)
        self.assert_no_output()

    def test_bounded_output_fails_before_writing(self):
        with patch.object(compiler, 'MAX_OUTPUT_BYTES', 1), self.assertRaises(ValueError):
            compiler.compile_inputs(self.root)
        self.assert_no_output()


if __name__ == '__main__':
    unittest.main()
