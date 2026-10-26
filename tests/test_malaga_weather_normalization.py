import copy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval.normalize_malaga_weather import (
    END, FIELDS, HEADER, START, build_bundle, encoded, extract_chart, load_json,
    normalize_document, read_pinned, utc_label,
)


SCRIPT = b'Offline fixture for the audited calendar parser contract.'
SCRIPT_HASH = hashlib.sha256(SCRIPT).hexdigest()
INPUT_INFO = dict(path='fixture.json', bytes=1, sha256='a' * 64)


def fixture():
    records = []
    moment = START
    identity = 2
    while moment <= END:
        midnight = moment.hour == 0 and moment.minute == 0
        records.append(dict(source_row=identity, source_date=moment.strftime('%d/%m/%Y'),
                            source_hour='24:00' if midnight else f'{moment.hour}:{moment.minute:02}',
                            temperature_c=20 + identity / 1000, relative_humidity_percent=50.0,
                            wind_speed_m_s=2.5, wind_direction_degrees=123.4, precipitation_mm=0.0,
                            observed_at_utc=None, available_at_utc=None,
                            actor_input_eligible=False, clock_ambiguous=midnight))
        identity += 1
        moment += timedelta(minutes=30)
    return dict(version='malaga-measured-halfhourly-candidate-v1',
                role='retrospective_evaluator_context', actor_input_eligible=False,
                station='MA04 Estepona', header=HEADER.copy(), records=records)


def chart_fixture(document):
    chart = []
    for row in document['records']:
        moment = START + timedelta(minutes=(row['source_row'] - 2) * 30)
        midnight = row['source_hour'] == '24:00'
        date = moment - timedelta(days=1) if midnight else moment
        chart.append(dict(fecha=moment.replace(tzinfo=None).isoformat(timespec='milliseconds') + '+02:00',
                          fechaStr=date.strftime('%d/%m/%Y') + ' ' + row['source_hour'],
                          hora='2400' if midnight else str(moment.hour * 100 + moment.minute),
                          tieneDatos=True,
                          resultadoVariable=[dict(nombreCampoTablaDatos=column, valor=str(row[field]))
                                             for field, column in FIELDS.items()]))
    return chart


def chart_html(chart):
    return ('<input name="_csrf" value="DO-NOT-COPY-THIS-TOKEN">'
            'resultadoConsultaChart.set("29_4_Estepona",' + json.dumps(chart) + ');').encode()


def extracted(document):
    with patch('eval.normalize_malaga_weather.SCRIPT_SHA256', SCRIPT_HASH):
        return extract_chart(chart_html(chart_fixture(document)), SCRIPT)


class NormalizationTests(unittest.TestCase):
    def test_without_chart_normalizes_376_and_leaves_eight_unknown(self):
        document = fixture()
        before = copy.deepcopy(document)
        result = normalize_document(document, INPUT_INFO)
        self.assertEqual(document, before)
        self.assertEqual(result['status'], 'partial_timestamp_normalization')
        self.assertEqual(result['checks']['normalized_rows'], 376)
        self.assertEqual(result['checks']['unresolved_rows'], 8)
        self.assertEqual(result['checks']['gap_count'], 8)
        self.assertEqual(result['checks']['gap_timestamps_utc'],
                         [f'2021-09-{day:02}T00:00:00Z' for day in range(9, 17)])
        self.assertEqual(result['records'][0]['observed_at_utc'], '2021-09-08T00:30:00Z')
        for index, row in enumerate(result['records']):
            self.assertEqual(row['source_date'], document['records'][index]['source_date'])
            self.assertEqual(row['source_hour'], document['records'][index]['source_hour'])
            self.assertEqual(row['provenance']['json_pointer'], f'/records/{index}')
            self.assertEqual(row['provenance']['input_sha256'], INPUT_INFO['sha256'])
            if row['source_hour'] == '24:00':
                self.assertIsNone(row['observed_at_utc'])
                self.assertTrue(row['clock_ambiguous'])
                self.assertIn('order is not proof', row['provenance']['timestamp_reason'])

    def test_independent_chart_labels_resolve_all_midnights(self):
        document = fixture()
        chart = extracted(document)
        result = normalize_document(document, INPUT_INFO, chart)
        self.assertEqual(result['checks']['normalized_rows'], 384)
        self.assertEqual(result['checks']['midnight_rows_resolved'], 8)
        self.assertEqual(result['checks']['chart_value_matches'], 384)
        self.assertEqual(result['checks']['gap_count'], 0)
        for index, row in enumerate(result['records']):
            self.assertEqual(row['observed_at_utc'], utc_label(START + timedelta(minutes=30 * index)))
            self.assertIsNone(row['available_at_utc'])
            self.assertIsNone(row['received_at_utc'])
            self.assertFalse(row['actor_input_eligible'])
            self.assertFalse(row['historical_replay_ready'])
        self.assertEqual(result['records'][47]['source_date'], '09/09/2021')
        self.assertEqual(result['records'][47]['observed_at_utc'], '2021-09-09T00:00:00Z')
        self.assertEqual(chart[47]['fechaStr'], '08/09/2021 24:00')
        self.assertEqual(chart[47]['fecha'], '2021-09-09T00:00:00.000+02:00')

    def test_chart_matching_does_not_depend_on_row_order(self):
        document = fixture()
        chart = extracted(document)
        document['records'].reverse()
        chart.reverse()
        result = normalize_document(document, INPUT_INFO, chart)
        self.assertEqual(result['checks']['gap_count'], 0)
        self.assertEqual(result['records'][0]['observed_at_utc'], '2021-09-16T00:00:00Z')

    def test_missing_rows_are_reported_without_imputation(self):
        document = fixture()
        document['records'].pop(0)
        result = normalize_document(document, INPUT_INFO)
        self.assertEqual(result['checks']['gap_count'], 9)
        self.assertIn('2021-09-08T00:30:00Z', result['checks']['gap_timestamps_utc'])
        self.assertEqual(len(result['records']), 383)
        self.assertEqual(result['checks']['imputed_rows'], 0)

    def test_invalid_fields_types_clocks_duplicates_and_values(self):
        edits = [
            lambda doc: doc.update(station='Different station'),
            lambda doc: doc.update(header=[]),
            lambda doc: doc.update(actor_input_eligible=0),
            lambda doc: doc.update(records=[]),
            lambda doc: doc['records'].append(copy.deepcopy(doc['records'][0])),
            lambda doc: doc['records'][0].update(source_row=True),
            lambda doc: doc['records'][0].update(source_row=386),
            lambda doc: doc['records'][1].update(source_row=2),
            lambda doc: doc['records'][1].update(source_date='08/09/2021', source_hour='0:30'),
            lambda doc: doc['records'][0].update(source_date='07/09/2021'),
            lambda doc: doc['records'][0].update(source_date='31/09/2021'),
            lambda doc: doc['records'][0].update(source_date=20210908),
            lambda doc: doc['records'][0].update(source_hour='00:00'),
            lambda doc: doc['records'][0].update(source_hour='0:00'),
            lambda doc: doc['records'][0].update(source_hour='24:30'),
            lambda doc: doc['records'][0].update(source_hour='1:15'),
            lambda doc: doc['records'][0].update(source_hour=None),
            lambda doc: doc['records'][0].update(clock_ambiguous=0),
            lambda doc: doc['records'][47].update(clock_ambiguous=False),
            lambda doc: doc['records'][47].update(source_date='08/09/2021'),
            lambda doc: doc['records'][0].update(observed_at_utc='2021-09-08T00:30:00Z'),
            lambda doc: doc['records'][0].update(available_at_utc='2021-09-08T01:00:00Z'),
            lambda doc: doc['records'][0].update(extra='unexpected'),
            lambda doc: doc['records'][0].pop('temperature_c'),
        ]
        for field in FIELDS:
            for value in (True, None, '1.0', float('nan'), float('inf'), -float('inf'), 10 ** 400):
                edits.append(lambda doc, field=field, value=value: doc['records'][0].update({field: value}))
        for field, value in [('temperature_c', 101), ('relative_humidity_percent', -1),
                             ('wind_speed_m_s', -1), ('wind_direction_degrees', 361),
                             ('precipitation_mm', -1)]:
            edits.append(lambda doc, field=field, value=value: doc['records'][0].update({field: value}))
        for index, edit in enumerate(edits):
            with self.subTest(edit=index):
                document = fixture()
                edit(document)
                with self.assertRaises(ValueError):
                    normalize_document(document, INPUT_INFO)

    def test_chart_mismatch_is_rejected_instead_of_guessed(self):
        document = fixture()
        chart = extracted(document)
        chart[47]['measurements']['TempMedia'] += 1
        with self.assertRaisesRegex(ValueError, 'measurement mismatch'):
            normalize_document(document, INPUT_INFO, chart)
        chart = extracted(document)
        chart[47]['fecha'] = '2021-09-10T00:00:00.000+02:00'
        with self.assertRaises(ValueError):
            normalize_document(document, INPUT_INFO, chart)
        with self.assertRaises(ValueError):
            normalize_document(document, INPUT_INFO, extracted(document)[:-1])

    def test_chart_parser_rejects_inconsistent_or_nonfinite_evidence(self):
        edits = [
            lambda chart: chart[47].update(fechaStr='09/09/2021 24:00'),
            lambda chart: chart[0].update(fecha='2021-09-08T00:30:00.000Z'),
            lambda chart: chart[0].update(hora='0'),
            lambda chart: chart[0].update(tieneDatos=False),
            lambda chart: chart[0]['resultadoVariable'][0].update(valor='nan'),
            lambda chart: chart[0]['resultadoVariable'][0].update(valor=None),
            lambda chart: chart[0]['resultadoVariable'][0].update(nombreCampoTablaDatos='UNKNOWN'),
            lambda chart: chart[0]['resultadoVariable'][0].update(nombreCampoTablaDatos='HumedadMedia'),
        ]
        for edit in edits:
            chart = chart_fixture(fixture())
            edit(chart)
            with self.subTest(chart=chart[0]['fecha']):
                with patch('eval.normalize_malaga_weather.SCRIPT_SHA256', SCRIPT_HASH):
                    with self.assertRaises(ValueError):
                        extract_chart(chart_html(chart), SCRIPT)
        with self.assertRaises(ValueError):
            extract_chart(chart_html(chart_fixture(fixture())), SCRIPT)

    def test_json_rejects_duplicate_keys_and_nonfinite_constants(self):
        for source in ('{"station":1,"station":2}', '[NaN]', '[Infinity]', '[-Infinity]'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                load_json(source)


class BundleTests(unittest.TestCase):
    def test_hash_expectation_and_byte_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_bytes(b'{}')
            for expected in (None, '', 'A' * 64, 'a' * 64):
                with self.subTest(expected=expected), self.assertRaises(ValueError):
                    read_pinned(path, expected, directory)
            with self.assertRaisesRegex(ValueError, 'byte limit'):
                read_pinned(path, hashlib.sha256(b'{}').hexdigest(), directory, limit=1)

    def test_bundle_has_audited_hashes_and_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / '.runtime/malaga-weather-normalized-v1'
            source = root / 'source.json'
            source.write_bytes(encoded(fixture()))
            html = root / 'reference.html'
            html.write_bytes(chart_html(chart_fixture(fixture())))
            script = root / 'script.js'
            script.write_bytes(SCRIPT)
            kwargs = dict(chart_html=html, expected_html_sha256=hashlib.sha256(html.read_bytes()).hexdigest(),
                          chart_script=script, expected_script_sha256=SCRIPT_HASH)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch('eval.normalize_malaga_weather.SCRIPT_SHA256', SCRIPT_HASH):
                audit = build_bundle(root, source, expected, output, **kwargs)
            self.assertFalse(audit['actor_input_eligible'])
            self.assertFalse(audit['historical_replay_ready'])
            self.assertEqual(audit['version'], 'malaga-weather-normalization-v1')
            self.assertEqual(audit['status'], 'source_clock_normalized')
            self.assertEqual(len(audit['input_files']), 3)
            self.assertEqual(len(audit['output_files']), 2)
            self.assertEqual(len(audit['boundary_mappings']), 8)
            for info in audit['input_files'] + audit['output_files']:
                content = (root / info['path']).read_bytes()
                self.assertEqual(info['bytes'], len(content))
                self.assertEqual(info['sha256'], hashlib.sha256(content).hexdigest())
            for path in output.iterdir():
                self.assertNotIn(b'DO-NOT-COPY-THIS-TOKEN', path.read_bytes())
                self.assertNotIn(b'_csrf', path.read_bytes())
            self.assertEqual(load_json((output / 'audit.json').read_bytes()), audit)
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            with patch('eval.normalize_malaga_weather.SCRIPT_SHA256', SCRIPT_HASH):
                build_bundle(root, source, expected, output, **kwargs)
            self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_bad_hash_and_partial_evidence_write_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_bytes(encoded(fixture()))
            output = root / '.runtime/malaga-weather-normalized-v1'
            with self.assertRaises(ValueError):
                build_bundle(root, source, 'a' * 64, output)
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                build_bundle(root, source, hashlib.sha256(source.read_bytes()).hexdigest(),
                             output, chart_html=root / 'reference.html')
            self.assertFalse(output.exists())

    def test_bundle_restricts_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Outputs must use'):
                build_bundle(directory, 'unused', 'a' * 64, Path(directory) / 'elsewhere')

    def test_bundle_does_not_overwrite_an_input_or_follow_output_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / '.runtime/malaga-weather-normalized-v1'
            output.mkdir(parents=True)
            source = output / 'audit.json'
            source.write_bytes(encoded(fixture()))
            original = source.read_bytes()
            expected = hashlib.sha256(original).hexdigest()
            with self.assertRaisesRegex(ValueError, 'overwrite an input'):
                build_bundle(root, source, expected, output)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((output / 'weather.json').exists())
            (output / 'weather.json').symlink_to(source)
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                build_bundle(root, source, expected, output)
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
