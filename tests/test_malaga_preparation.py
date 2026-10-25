import copy
import csv
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ElementTree
import zipfile

from eval.prepare_malaga_2021 import (
    VERSION, WGS84_WKT, build_bundle, check_geometry, eligible_actor_records, encoded,
    layer_data, load_json, local_clock, pinned_file, population_rows,
    product_layers, source_table, terrain_summary,
)


def dbf_bytes():
    values = {'source_nam': 'Sentinel-3', 'src_date': '09/09/2021',
              'source_tm': 'T10:16:09Z', 'sensor_gsd': '300 m',
              'eventphase': 'Post-event', 'src_id': '1'}
    header = bytearray(32)
    header[0] = 3
    struct.pack_into('<IHH', header, 4, 1, 33 + 32 * len(values), 1 + 32 * len(values))
    descriptors = bytearray()
    for name in values:
        descriptor = bytearray(32)
        descriptor[:len(name)] = name.encode()
        descriptor[11] = ord('C')
        descriptor[16] = 32
        descriptors.extend(descriptor)
    row = b' ' + b''.join(value.encode().ljust(32) for value in values.values())
    return bytes(header + descriptors) + b'\r' + row + b'\x1a'


def geojson():
    return dict(type='FeatureCollection',
                crs=dict(type='name', properties=dict(name='urn:ogc:def:crs:OGC:1.3:CRS84')),
                features=[dict(type='Feature', properties=dict(dmg_src_id=1, area=2.5,
                                                             caller_phone='PRIVATE CONTACT'),
                               geometry=dict(type='Polygon', coordinates=[[
                                   [-5.2, 36.5], [-5.1, 36.5], [-5.1, 36.6],
                                   [-5.2, 36.6], [-5.2, 36.5]]]))])


def population_fixture():
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(['Municipios', 'Sexo', 'Periodo', 'Total'])
    municipalities = []
    for index in range(7):
        code = f'29{index:03}'
        label = f'{code} Exercise municipality {index}'
        count = index + 1
        writer.writerow([label, 'Total', '2021', str(count)])
        municipalities.append(dict(municipality_code=code, source_label=label,
                                   source_line=index + 2, population_total=count))
    return stream.getvalue().encode('utf-8-sig'), dict(actor_input_eligible=False,
                                                      municipalities=municipalities)


def bundle_fixture(root):
    inputs, config = root / 'inputs', root / 'config'
    inputs.mkdir()
    config.mkdir()
    prefix = 'EXERCISE'
    archive_stream = io.BytesIO()
    with zipfile.ZipFile(archive_stream, 'w') as archive:
        archive.writestr(prefix + '_source_r1_v1.dbf', dbf_bytes())
        for layer in ('areaOfInterestA', 'observedEventA', 'imageFootprintA'):
            archive.writestr(prefix + '_' + layer + '_r1_v1.json', encoded(geojson()))
    population, metadata = population_fixture()
    files = [('product', 'product.zip', archive_stream.getvalue()),
             ('ine-population', 'population.csv', population),
             ('terrain-aoi', 'terrain.asc', b'ncols 2\nnrows 1\nxllcorner 1\nyllcorner 2\ncellsize 25\n10 20\n')]
    sources = []
    for identifier, filename, data in files:
        (inputs / filename).write_bytes(data)
        sources.append(dict(id=identifier, filename=filename, bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest()))
    element = ElementTree.Element('Egif')
    record = ElementTree.SubElement(element, 'Pif')
    ElementTree.SubElement(record, 'numeroparte').text = 'exercise-private-id'
    common = ElementTree.SubElement(record, 'pif_comun')
    ElementTree.SubElement(common, 'anio').text = '2021'
    place = ElementTree.SubElement(record, 'pif_localizacion')
    ElementTree.SubElement(place, 'idprovincia').text = '29'
    times = ElementTree.SubElement(record, 'pif_tiempos')
    for field, value in [('deteccion', '2021-09-08T21:35:00'),
                         ('controlado', '2021-09-14T06:45:00'),
                         ('extinguido', '2021-10-24T19:00:00')]:
        ElementTree.SubElement(times, field).text = value
    egif = inputs / 'egif.zip'
    with zipfile.ZipFile(egif, 'w') as archive:
        archive.writestr('exercise.xml', ElementTree.tostring(element))
    data = egif.read_bytes()
    lock = dict(version=VERSION, files=sources,
                egif=dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()),
                products=[dict(id='exercise', source_id='product', member_prefix=prefix,
                               include_context=False, delivery_source_timestamp='2021-09-09T20:13:47')],
                uncached_sources={}, remaining_gates=['exercise_not_historical'])
    claims = dict(version=VERSION, records=[dict(id='claim-one', source_id='egif',
                  actor_input_eligible=False, available_at_utc=None,
                  event_local='2021-09-08T21:35:00')], conflicts=[])
    for filename, value in [('sources.lock.json', lock), ('claims.json', claims),
                            ('population_sources.json', metadata)]:
        (config / filename).write_bytes(encoded(value))
    return inputs, config, egif


class MalagaPreparationTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_nonfinite_and_oversize(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(ValueError):
                load_json(data)
        with patch('eval.prepare_malaga_2021.MAX_JSON', 2):
            with self.assertRaises(ValueError):
                load_json(b'{"a":1}')

    def test_local_civil_time_is_explicitly_an_interpretation(self):
        result = local_clock('2021-09-08T21:35:00')
        self.assertEqual(result['interpreted_utc'], '2021-09-08T19:35:00+00:00')
        self.assertFalse(result['timezone_explicit_in_source'])
        for value in ('2026-09-08T21:35:00', '2021-09-08T21:35:00Z'):
            with self.assertRaises(ValueError):
                local_clock(value)

    def test_unknown_receipt_future_and_retrospective_evidence_stay_hidden(self):
        record = dict(role='received_observation', actor_input_eligible=True,
                      observed_at_utc='2021-09-09T10:00:00Z',
                      available_at_utc='2021-09-09T11:00:00Z')
        self.assertEqual(eligible_actor_records([record], '2021-09-09T10:30:00Z'), [])
        self.assertEqual(eligible_actor_records([record], '2021-09-09T11:00:00Z'), [record])
        for changes in ({'available_at_utc': None}, {'actor_input_eligible': False},
                        {'role': 'retrospective_evaluator_evidence'},
                        {'observed_at_utc': '2021-09-09T12:00:00Z'}):
            self.assertEqual(eligible_actor_records([{**record, **changes}],
                                                   '2021-09-09T13:00:00Z'), [])
        with self.assertRaises(ValueError):
            eligible_actor_records([record], '2021-09-09T12:00:00')

    def test_source_table_retains_explicit_satellite_utc_clock(self):
        result = source_table(dbf_bytes())
        self.assertEqual(result['1']['observed_at_utc'], '2021-09-09T10:16:09+00:00')
        with self.assertRaises(ValueError):
            source_table(dbf_bytes()[:-20])

    def test_geometry_coordinates_must_be_finite_and_in_declared_frame(self):
        bounds, count = check_geometry(geojson()['features'][0]['geometry'])
        self.assertEqual(bounds, [-5.2, 36.5, -5.1, 36.6])
        self.assertEqual(count, 5)
        for coordinate in ([float('nan'), 36.5], [-5, 91], [True, 36.5]):
            with self.assertRaises(ValueError):
                check_geometry(dict(type='Point', coordinates=coordinate))

    def test_layers_drop_contact_fields_and_require_source_lineage(self):
        value, summary = layer_data(encoded(geojson()), source_table(dbf_bytes()))
        self.assertNotIn('PRIVATE CONTACT', str(value))
        self.assertFalse(value['actor_input_eligible'])
        self.assertEqual(summary['features'], 1)
        with self.assertRaises(ValueError):
            layer_data(encoded(geojson()), {})
        invalid = geojson()
        invalid['crs']['properties']['name'] = 'EPSG:25830'
        with self.assertRaises(ValueError):
            layer_data(encoded(invalid), source_table(dbf_bytes()))

    def test_missing_crs_needs_independent_wgs84_sidecar_verification(self):
        value = geojson()
        del value['crs']
        with self.assertRaises(ValueError):
            layer_data(encoded(value), source_table(dbf_bytes()))
        result, summary = layer_data(encoded(value), source_table(dbf_bytes()), True)
        self.assertEqual(result['crs']['properties']['name'], 'urn:ogc:def:crs:OGC:1.3:CRS84')
        self.assertIn('checked WGS84 PRJ', summary['coordinate_interpretation'])

    def test_product_reader_cross_checks_missing_crs_against_pinned_prj(self):
        value = geojson()
        del value['crs']
        product = dict(member_prefix='EXERCISE', include_context=False)
        for prj, valid in ((WGS84_WKT, True), ('PROJCS["UTM"]', False)):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('EXERCISE_source_r1.dbf', dbf_bytes())
                for layer in ('areaOfInterestA', 'observedEventA', 'imageFootprintA'):
                    archive.writestr('EXERCISE_' + layer + '_r1.json', encoded(value))
                    archive.writestr('EXERCISE_' + layer + '_r1.prj', prj)
            if valid:
                _, layers = product_layers(stream.getvalue(), product)
                self.assertEqual(len(layers), 3)
            else:
                with self.assertRaises(ValueError):
                    product_layers(stream.getvalue(), product)

    def test_terrain_counts_cells_without_inventing_nodata_or_vertical_units(self):
        data = b'ncols 2\nnrows 1\nxllcorner 1\nyllcorner 2\ncellsize 25\n10 20\n'
        result = terrain_summary(data)
        self.assertEqual(result['cells'], 2)
        self.assertFalse(result['nodata_declared'])
        self.assertFalse(result['vertical_units_verified'])
        self.assertFalse(result['historical_survey_epoch_verified'])
        for invalid in (data.replace(b'10 20', b'10'), data.replace(b'10 20', b'nan 20')):
            with self.assertRaises(ValueError):
                terrain_summary(invalid)

    def test_population_extracts_only_2021_and_checks_source_lines(self):
        data, metadata = population_fixture()
        future = b'29000 Exercise municipality 0;Total;2026;999999\n'
        result = population_rows(data + future, metadata)
        self.assertEqual(sum(row['population_total'] for row in result['municipalities']), 28)
        self.assertFalse(result['actor_input_eligible'])
        self.assertEqual(result['publication_date'], '2021-12-23')
        invalid = copy.deepcopy(metadata)
        invalid['municipalities'][0]['population_total'] = 99
        with self.assertRaises(ValueError):
            population_rows(data, invalid)
        with self.assertRaises(ValueError):
            population_rows(data + data.splitlines(keepends=True)[1], metadata)

    def test_pins_reject_modified_sources_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'raw').write_bytes(b'data')
            source = dict(filename='raw', bytes=4, sha256=hashlib.sha256(b'data').hexdigest())
            self.assertEqual(pinned_file(root, source), b'data')
            with self.assertRaises(ValueError):
                pinned_file(root, {**source, 'sha256': '0' * 64})
            with self.assertRaises(ValueError):
                pinned_file(root, {**source, 'filename': '../raw'})

    def test_bundle_rebuild_is_identical_and_no_historical_claim_is_actor_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, config, egif = bundle_fixture(root)
            first = build_bundle(inputs, egif, root / 'first', config)
            second = build_bundle(inputs, egif, root / 'second', config)
            self.assertEqual(first['content_sha256'], second['content_sha256'])
            manifest = load_json((root / 'first/manifest.json').read_bytes())
            self.assertFalse(manifest['historical_replay_ready'])
            self.assertIsNone(manifest['products'][0]['available_at_utc'])
            self.assertNotIn('exercise-private-id', str(manifest))
            claims = load_json((root / 'first/private/claims.json').read_bytes())
            self.assertEqual(eligible_actor_records(claims['records'], '2021-12-31T00:00:00Z'), [])
            with self.assertRaises(ValueError):
                build_bundle(inputs, egif, root / 'first', config)

    def test_actor_labels_fail_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, config, egif = bundle_fixture(root)
            claims = load_json((config / 'claims.json').read_bytes())
            claims['records'][0]['actor_input_eligible'] = True
            (config / 'claims.json').write_bytes(encoded(claims))
            with self.assertRaises(ValueError):
                build_bundle(inputs, egif, root / 'rejected', config)
            self.assertFalse((root / 'rejected').exists())

    def test_wrong_egif_control_milestone_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, config, egif = bundle_fixture(root)
            with zipfile.ZipFile(egif) as archive:
                data = archive.read('exercise.xml')
            data = data.replace(b'2021-09-14T06:45:00', b'2021-09-14T06:46:00')
            with zipfile.ZipFile(egif, 'w') as archive:
                archive.writestr('exercise.xml', data)
            lock = load_json((config / 'sources.lock.json').read_bytes())
            raw = egif.read_bytes()
            lock['egif'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            (config / 'sources.lock.json').write_bytes(encoded(lock))
            with self.assertRaises(ValueError):
                build_bundle(inputs, egif, root / 'rejected', config)
            self.assertFalse((root / 'rejected').exists())

    @unittest.skipUnless(importlib.util.find_spec('pyproj'), 'Optional data-only projection tools')
    def test_cross_crs_check_detects_insufficient_terrain_and_forged_metadata(self):
        from eval.check_malaga_geography import check_coverage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, config, egif = bundle_fixture(root)
            build_bundle(inputs, egif, root / 'bundle', config)
            self.assertFalse(check_coverage(root / 'bundle')['all_vertices_covered'])
            path = root / 'bundle/manifest.json'
            manifest = load_json(path.read_bytes())
            manifest['terrain']['header']['ncols'] += 1
            path.write_bytes(encoded(manifest))
            with self.assertRaises(ValueError):
                check_coverage(root / 'bundle')


if __name__ == '__main__':
    unittest.main()
