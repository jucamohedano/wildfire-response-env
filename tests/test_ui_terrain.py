import copy
import hashlib
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import zlib

from eval import ui_terrain as terrain


def plane(height=5, width=6, column_rise=0, row_rise=0, offset=100):
    return [[offset + row * row_rise + column * column_rise
             for column in range(width)] for row in range(height)]


def documents(width=3, height=2, left=0, top=0):
    parent = dict(sha256='caller-pinned-parent', grid=dict(
        width=37, height=41, cell_km=0.5, crs='EPSG:25830',
        transform=[500, 0, 297000, 0, -500, 4054500]))
    domain = dict(prepared_inputs_sha256=parent['sha256'],
                  crop=dict(left=left, top=top, width=width, height=height),
                  grid=dict(width=width, height=height, cell_km=0.5, crs='EPSG:25830',
                            transform=[500, 0, 297000 + left * 500,
                                       0, -500, 4054500 - top * 500]),
                  elevations_metres=plane(height, width),
                  height_support_mask=[[1] * width for row in range(height)])
    return parent, domain


def ascii_source(value='100'):
    return (b'ncols 780\nnrows 860\nxllcorner 296500\nyllcorner 4033500\ncellsize 25\n'
            + ((value.encode() + b' ') * 780 + b'\n') * 860)


def decode_png(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError('Invalid PNG signature')
    position, compressed, dimensions = 8, bytearray(), None
    kinds = []
    while position < len(data):
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        crc = struct.unpack_from('>I', data, position + 8 + length)[0]
        if crc != zlib.crc32(kind + payload) & 0xffffffff:
            raise AssertionError('Invalid PNG CRC')
        kinds.append(kind)
        if kind == b'IHDR':
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(
                '>IIBBBBB', payload)
            if (depth, colour, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                raise AssertionError('Unexpected PNG format')
            dimensions = width, height
        elif kind == b'IDAT':
            compressed.extend(payload)
        position += 12 + length
    if kinds != [b'IHDR', b'IDAT', b'IEND']:
        raise AssertionError('Unexpected PNG chunks')
    width, height = dimensions
    scanlines = zlib.decompress(compressed)
    stride = width * 3 + 1
    if len(scanlines) != stride * height or any(scanlines[row * stride] for row in range(height)):
        raise AssertionError('Unexpected scanlines')
    return width, height, b''.join(scanlines[row * stride + 1:(row + 1) * stride]
                                  for row in range(height))


class ShadingTests(unittest.TestCase):
    def test_relief_is_transparent_on_flat_ground_and_feathered_without_height_changes(self):
        samples = plane(height=22, width=22, column_rise=-25, row_rise=-12, offset=3000)
        before = copy.deepcopy(samples)
        width, height, pixels = terrain.terrain_relief(samples, cell_size_metres=25)
        self.assertEqual((width, height), (20, 20))
        self.assertEqual(samples, before)
        alpha = list(pixels[3::4])
        self.assertGreater(alpha[10 * width + 10], 0)
        self.assertLessEqual(max(alpha), 105)
        self.assertTrue(all(alpha[column] == alpha[(height - 1) * width + column] == 0
                            for column in range(width)))
        self.assertTrue(all(alpha[row * width] == alpha[row * width + width - 1] == 0
                            for row in range(height)))
        flat = terrain.terrain_relief(plane(), cell_size_metres=25)[2]
        self.assertEqual(set(flat[3::4]), {0})

    def test_relief_png_is_rgba_and_retains_validated_grid_and_missing_data_rules(self):
        prepared, domain = documents(width=5, height=5)
        result = terrain.render_terrain(prepared, domain, source_reader=lambda: None,
                                        relief=True)
        header = struct.unpack_from('>IIBBBBB', result['png'], 16)
        self.assertEqual(header, (5, 5, 8, 6, 0, 0, 0))
        self.assertEqual(result['extent'], [297000, 4052000, 299500, 4054500])
        self.assertEqual(result['crs'], terrain.CRS)
        self.assertIn('500 m', result['source_label'])
        domain['elevations_metres'][1][1] = -9999
        with self.assertRaises(ValueError):
            terrain.render_terrain(prepared, domain, source_reader=lambda: None,
                                   relief=True)
        with self.assertRaises(ValueError):
            terrain.terrain_relief([[100, float('nan')], [100, 100]],
                                   cell_size_metres=25, halo=0)
        with self.assertRaises(ValueError):
            terrain.encode_png(1, 1, b'1234', channels=2)

    def expected_pixel(self, height, column_gradient=0, row_gradient=0):
        light = max(0, (0.5 * column_gradient + 0.5 * row_gradient + math.sqrt(0.5))
                    / math.sqrt(1 + column_gradient ** 2 + row_gradient ** 2))
        return bytes(round((low + (high - low) * min(1, height / 2000)) * (0.72 + 0.28 * light))
                     for low, high in ((202, 244), (215, 236), (188, 213)))

    def test_constant_plane_has_exact_uniform_pixels(self):
        for spacing in (5, 25, 500):
            self.assertEqual(terrain.terrain_rgb(plane(), cell_size_metres=spacing),
                             (4, 3, self.expected_pixel(100) * 12))

    def test_tilted_planes_have_analytical_horn_light_in_physical_units(self):
        for spacing in (5, 25, 500):
            for column_gradient, row_gradient in ((0.1, 0), (0, 0.2), (0.3, -0.1), (-0.4, -0.5)):
                samples = plane(column_rise=spacing * column_gradient,
                                row_rise=spacing * row_gradient, offset=3000)
                width, height, pixels = terrain.terrain_rgb(samples, cell_size_metres=spacing)
                expected = b''.join(self.expected_pixel(samples[row][column], column_gradient, row_gradient)
                                    for row in range(1, 4) for column in range(1, 5))
                self.assertEqual((width, height, pixels), (4, 3, expected))

    def test_coarse_edges_use_supported_one_sided_gradients(self):
        samples = plane(column_rise=50, row_rise=100, offset=3000)
        width, height, pixels = terrain.terrain_rgb(samples, cell_size_metres=500, halo=0)
        self.assertEqual((width, height, pixels), (6, 5, self.expected_pixel(3000, 0.1, 0.2) * 30))
        self.assertEqual(terrain.terrain_rgb([[100]], cell_size_metres=500, halo=0),
                         (1, 1, self.expected_pixel(100)))

    def test_nonplanar_horn_weights_and_centre_tint(self):
        samples = [[100, 100, 108], [100, 500, 104], [100, 100, 100]]
        self.assertEqual(terrain.terrain_rgb(samples, cell_size_metres=25),
                         (1, 1, self.expected_pixel(500, 2 / 25, -1 / 25)))

    def test_every_stencil_position_including_centre_and_halo_must_be_known(self):
        for row in range(3):
            for column in range(3):
                for invalid in (None, 0, -9999, float('nan'), float('inf'), -float('inf'), True, '100'):
                    samples = plane(3, 3)
                    samples[row][column] = invalid
                    with self.subTest(row=row, column=column, invalid=invalid):
                        with self.assertRaises(ValueError):
                            terrain.terrain_rgb(samples, cell_size_metres=25)

    def test_malformed_geometry_spacing_and_overflow_fail(self):
        for samples in ([], [[]], [[100] * 3] * 2, [[100] * 3, [100] * 4, [100] * 3],
                        [[100] * 1025] * 1024):
            with self.assertRaises(ValueError):
                terrain.terrain_rgb(samples, cell_size_metres=25)
        for spacing in (0, -1, None, True, '25', float('nan'), float('inf'), 10 ** 1000):
            with self.assertRaises(ValueError):
                terrain.terrain_rgb(plane(), cell_size_metres=spacing)
        with self.assertRaises(ValueError):
            terrain.terrain_rgb(plane(), cell_size_metres=25, halo=True)
        with self.assertRaises(ValueError):
            terrain.terrain_rgb([[1, 1, 1e308]] * 3, cell_size_metres=1e-308)

    def test_png_signature_dimensions_crc_pixels_and_determinism(self):
        samples = plane(column_rise=10, row_rise=20)
        before = copy.deepcopy(samples)
        pixels = terrain.terrain_rgb(samples, cell_size_metres=25)
        encoded = terrain.encode_png(*pixels)
        self.assertEqual(decode_png(encoded), pixels)
        self.assertEqual(terrain.encode_png(*terrain.terrain_rgb(samples, cell_size_metres=25)), encoded)
        self.assertEqual(samples, before)
        for args in ((0, 1, b''), (True, 1, b'123'), (1, 1, b''), (1, 1, bytearray(b'123')),
                     (1024, 1025, b'')):
            with self.assertRaises(ValueError):
                terrain.encode_png(*args)


class CropTests(unittest.TestCase):
    def test_active_crop_dimensions_and_offset_include_full_halo(self):
        samples = [[row * 1000 + column + 1 for column in range(780)] for row in range(860)]
        before = copy.deepcopy(samples)
        parent, domain = documents(37, 36)
        window = terrain.crop_samples(samples, domain['grid'])
        self.assertEqual((len(window), len(window[0])), (722, 742))
        self.assertEqual(window[0][0], samples[19][19])
        self.assertEqual(window[1][1], samples[20][20])
        self.assertEqual(window[-2][-2], samples[739][759])
        self.assertEqual(window[-1][-1], samples[740][760])
        self.assertEqual(samples, before)

    def test_nonzero_crop_offsets_are_not_stretched_to_parent_extent(self):
        samples = [[row * 1000 + column + 1 for column in range(780)] for row in range(860)]
        parent, domain = documents(2, 3, left=2, top=4)
        window = terrain.crop_samples(samples, domain['grid'])
        self.assertEqual((len(window), len(window[0])), (62, 42))
        self.assertEqual(window[1][1], samples[100][60])
        self.assertEqual(window[-1][-1], samples[160][100])

    def test_unknown_outside_window_unused_but_halo_holes_and_missing_halo_fail(self):
        samples = [[100] * 780 for row in range(860)]
        parent, domain = documents(1, 1)
        samples[0][0] = None
        self.assertEqual(terrain.crop_samples(samples, domain['grid'])[0][0], 100)
        samples[19][19] = None
        with self.assertRaises(ValueError):
            terrain.crop_samples(samples, domain['grid'])
        for transform in ([500, 0, 296500, 0, -500, 4055000],
                          [500, 0, 297001, 0, -500, 4054500],
                          [500, 0, 316000, 0, -500, 4054500]):
            domain['grid']['transform'] = transform
            with self.assertRaises(ValueError):
                terrain.crop_samples(samples, domain['grid'])


class MeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        header = b'ncols 780\nnrows 860\nxllcorner 296500\nyllcorner 4033500\ncellsize 25\n'
        cls.source = header + b''.join(
            (' '.join(str(row * 1000 + column + 1) for column in range(780)) + '\n').encode()
            for row in range(860))
        cls.source_sha256 = hashlib.sha256(cls.source).hexdigest()

    def mesh(self, parent, domain, data=None):
        data = self.source if data is None else data
        with patch.dict(terrain.compiler.INPUT_PINS,
                        {terrain.compiler.TERRAIN_ASCII: hashlib.sha256(data).hexdigest()}):
            return terrain.terrain_mesh(parent, domain, source_reader=lambda: data)

    def test_active_mesh_axes_cell_centre_interpolation_edges_and_input_immutability(self):
        parent, domain = documents(37, 36)
        before = copy.deepcopy((parent, domain))
        result = self.mesh(parent, domain)
        self.assertEqual(set(result), {'version', 'crs', 'extent', 'columns', 'rows',
            'eastings', 'northings', 'heights_metres', 'source_sha256', 'source_label', 'role'})
        self.assertEqual(result['version'], 'wildfire-display-mesh-v1')
        self.assertEqual(result['role'], 'display_only')
        self.assertEqual(result['crs'], 'EPSG:25830')
        self.assertEqual(result['extent'], [297000, 4036500, 315500, 4054500])
        self.assertEqual((result['columns'], result['rows']), (371, 361))
        self.assertLessEqual(result['columns'] * result['rows'], 160_000)
        self.assertEqual(result['eastings'], list(range(297000, 315501, 50)))
        self.assertEqual(result['northings'], list(range(4054500, 4036499, -50)))
        expected = [[(19.5 + row * 2) * 1000 + 19.5 + column * 2 + 1
                     for column in range(371)] for row in range(361)]
        self.assertEqual(result['heights_metres'], expected)
        self.assertEqual(result['source_sha256'], self.source_sha256)
        self.assertIn('50 m display mesh', result['source_label'])
        self.assertNotIn(terrain.compiler.TERRAIN_ASCII, result['source_label'])
        self.assertEqual((parent, domain), before)
        result['heights_metres'][0][0] = -1
        self.assertEqual(self.mesh(parent, domain)['heights_metres'][0][0], 19520.5)

    def test_nonzero_crop_offsets_keep_their_own_projected_extent(self):
        parent, domain = documents(width=2, height=3, left=2, top=4)
        result = self.mesh(parent, domain)
        self.assertEqual(result['extent'], [298000, 4051000, 299000, 4052500])
        self.assertEqual((result['columns'], result['rows']), (21, 31))
        self.assertEqual((result['eastings'][0], result['eastings'][-1]), (298000, 299000))
        self.assertEqual((result['northings'][0], result['northings'][-1]), (4052500, 4051000))
        self.assertEqual(result['heights_metres'][0][0], 99560.5)
        self.assertEqual(result['heights_metres'][-1][-1], 159600.5)

    def test_exact_final_edge_is_retained_for_nondivisible_stride_and_cap_is_enforced(self):
        self.assertEqual(terrain._mesh_indices(8), [1, 3, 5, 7])
        self.assertEqual(terrain._mesh_indices(6), [1, 3, 5])
        self.assertEqual(terrain._mesh_indices(7), [1, 3, 5, 6])
        parent, domain = documents()
        with patch.object(terrain, 'MAX_MESH_VERTICES', 1), self.assertRaises(ValueError):
            self.mesh(parent, domain)
        domain['grid']['width'] = 129
        with self.assertRaises(ValueError):
            self.mesh(parent, domain)

    def test_absent_changed_oversized_and_malformed_sources_never_use_coarse_heights(self):
        parent, domain = documents()
        for data in (None, b'', b'changed', self.source, 'not bytes',
                     b'x' * (terrain.compiler.MAX_ASCII_BYTES + 1)):
            with self.subTest(kind=type(data)), self.assertRaises(ValueError):
                terrain.terrain_mesh(parent, domain, source_reader=lambda: data)
        declared_nodata = ascii_source().replace(b'cellsize 25\n', b'cellsize 25\nNODATA_value -9999\n')
        for data in (b'malformed but hash matches', ascii_source('nan'), ascii_source('0'),
                     ascii_source('-9999'), declared_nodata):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.mesh(parent, domain, data)
        for error in (PermissionError('unreadable'), OSError('read failure')):
            with self.assertRaises(type(error)):
                terrain.terrain_mesh(parent, domain, source_reader=unittest.mock.Mock(side_effect=error))

    def test_unsampled_holes_and_last_edge_halo_are_validated_without_fill(self):
        parent, domain = documents(37, 36)
        for row, column in ((19, 19), (21, 21), (740, 760)):
            lines = ascii_source().splitlines()
            tokens = lines[5 + row].split()
            tokens[column] = b'-9999'
            lines[5 + row] = b' '.join(tokens)
            data = b'\n'.join(lines) + b'\n'
            with self.subTest(row=row, column=column), self.assertRaises(ValueError):
                self.mesh(parent, domain, data)

    def test_domain_and_prepared_parent_validation_are_shared_with_png(self):
        for mutate in (
                lambda parent, domain: domain['height_support_mask'][0].__setitem__(0, 0),
                lambda parent, domain: domain['elevations_metres'][0].__setitem__(0, None),
                lambda parent, domain: domain.__setitem__('prepared_inputs_sha256', 'other parent'),
                lambda parent, domain: domain['grid']['transform'].__setitem__(1, 1),
                lambda parent, domain: domain['grid']['transform'].__setitem__(2, 297001)):
            parent, domain = documents()
            mutate(parent, domain)
            with self.assertRaises(ValueError):
                self.mesh(parent, domain)
        parent, domain = documents()
        parent['grid']['transform'][2] += 500
        domain['grid']['transform'][2] += 500
        with self.assertRaises(ValueError):
            self.mesh(parent, domain)


class RendererTests(unittest.TestCase):
    def render_fallback(self, prepared, domain):
        return terrain.render_terrain(prepared, domain, source_reader=lambda: None)

    def test_explicit_fallback_extent_pixels_and_input_immutability(self):
        parent, domain = documents(3, 2, left=2, top=4)
        before = copy.deepcopy((parent, domain))
        result = self.render_fallback(parent, domain)
        self.assertEqual(set(result), {'png', 'extent', 'crs', 'source_label'})
        self.assertEqual(result['extent'], [298000, 4051500, 299500, 4052500])
        self.assertEqual(result['crs'], 'EPSG:25830')
        self.assertIn('500 m', result['source_label'])
        self.assertIn('fallback', result['source_label'])
        self.assertNotIn('25 m', result['source_label'])
        self.assertEqual(decode_png(result['png']), terrain.terrain_rgb(
            domain['elevations_metres'], cell_size_metres=500, halo=0))
        self.assertEqual(result, self.render_fallback(parent, domain))
        self.assertEqual((parent, domain), before)

    def test_injected_pinned_raw_renders_740_by_720_on_exact_domain_extent(self):
        data = ascii_source()
        parent, domain = documents(37, 36)
        before = copy.deepcopy((parent, domain))
        with patch.dict(terrain.compiler.INPUT_PINS,
                        {terrain.compiler.TERRAIN_ASCII: hashlib.sha256(data).hexdigest()}):
            result = terrain.render_terrain(parent, domain, source_reader=lambda: data)
        width, height, pixels = decode_png(result['png'])
        self.assertEqual((width, height), (740, 720))
        self.assertEqual(pixels, ShadingTests().expected_pixel(100) * (740 * 720))
        self.assertEqual(result['extent'], [297000, 4036500, 315500, 4054500])
        self.assertIn('25 m sampled ASC', result['source_label'])
        self.assertIn(hashlib.sha256(data).hexdigest(), result['source_label'])
        self.assertEqual((parent, domain), before)

    def test_present_changed_corrupt_or_oversized_raw_never_falls_back(self):
        parent, domain = documents()
        for data in (b'', b'changed', ascii_source(), 'not bytes',
                     b'x' * (terrain.compiler.MAX_ASCII_BYTES + 1)):
            with self.subTest(kind=type(data), size=len(data)), self.assertRaises(ValueError):
                terrain.render_terrain(parent, domain, source_reader=lambda: data)
        for data in (b'malformed but hash matches', ascii_source('nan'), ascii_source('0')):
            with patch.dict(terrain.compiler.INPUT_PINS,
                            {terrain.compiler.TERRAIN_ASCII: hashlib.sha256(data).hexdigest()}):
                with self.assertRaises(ValueError):
                    terrain.render_terrain(parent, domain, source_reader=lambda: data)

    def test_reader_io_failures_propagate(self):
        parent, domain = documents()
        for error in (PermissionError('unreadable'), OSError('read failure')):
            with self.assertRaises(type(error)):
                terrain.render_terrain(parent, domain, source_reader=unittest.mock.Mock(side_effect=error))

    def test_default_source_discovery_is_module_relative_and_absence_only(self):
        expected = Path(terrain.__file__).resolve().parents[1] / terrain.compiler.TERRAIN_ASCII
        with patch.object(Path, 'open', side_effect=FileNotFoundError) as reader:
            with patch.object(Path, 'is_symlink', return_value=False):
                self.assertIsNone(terrain._read_source())
            self.assertEqual(reader.call_args.args, ('rb',))
            with patch.object(Path, 'is_symlink', return_value=True):
                with self.assertRaises(ValueError):
                    terrain._read_source()
        with patch.object(Path, 'open', autospec=True) as reader:
            reader.return_value.__enter__.return_value.read.return_value = b'local source'
            self.assertEqual(terrain._read_source(), b'local source')
            self.assertEqual(reader.call_args.args, (expected, 'rb'))

    def test_missing_nonfinite_or_unsupported_domain_heights_fail_without_filling(self):
        for invalid in (None, 0, -1, float('nan'), float('inf'), True, '100'):
            parent, domain = documents()
            domain['elevations_metres'][0][0] = invalid
            with self.assertRaises(ValueError):
                self.render_fallback(parent, domain)
        for support in (0, None, '1', 1.0):
            parent, domain = documents()
            domain['height_support_mask'][0][0] = support
            with self.assertRaises(ValueError):
                self.render_fallback(parent, domain)

    def test_mismatched_crop_grid_parent_and_rotated_affine_fail(self):
        mutations = (
            lambda parent, domain: domain['grid']['transform'].__setitem__(1, 1),
            lambda parent, domain: domain['grid']['transform'].__setitem__(3, 1),
            lambda parent, domain: domain['grid']['transform'].__setitem__(2, 297001),
            lambda parent, domain: domain['grid']['transform'].__setitem__(5, float('nan')),
            lambda parent, domain: domain['grid'].__setitem__('crs', 'EPSG:4326'),
            lambda parent, domain: domain['grid'].__setitem__('width', 4),
            lambda parent, domain: domain['crop'].__setitem__('left', -1),
            lambda parent, domain: domain['crop'].__setitem__('top', 40),
            lambda parent, domain: domain['crop'].__setitem__('width', True),
            lambda parent, domain: domain.__setitem__('prepared_inputs_sha256', 'other parent'),
            lambda parent, domain: domain.__setitem__('elevations_metres', [[100]]),
            lambda parent, domain: domain.__setitem__('height_support_mask', [[1]]))
        for mutate in mutations:
            parent, domain = documents()
            mutate(parent, domain)
            with self.assertRaises(ValueError):
                self.render_fallback(parent, domain)

    def test_matching_but_shifted_parent_cannot_claim_pinned_raw(self):
        parent, domain = documents()
        parent['grid']['transform'][2] += 500
        domain['grid']['transform'][2] += 500
        self.assertIn('fallback', self.render_fallback(parent, domain)['source_label'])
        data = ascii_source()
        with patch.dict(terrain.compiler.INPUT_PINS,
                        {terrain.compiler.TERRAIN_ASCII: hashlib.sha256(data).hexdigest()}):
            with self.assertRaises(ValueError):
                terrain.render_terrain(parent, domain, source_reader=lambda: data)


if __name__ == '__main__':
    unittest.main()
