import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

from envs.wildfire_response.regional.grounded import anchors
from envs.wildfire_response.regional.protection import context


def example_row(identifier='weather-001', **changes):
    row = dict(
        id=identifier, source_id='source-1', observed_at='2021-07-01T10:00:00Z',
        available_at='2021-07-01T10:05:00Z', longitude=-3.5, latitude=40,
        wind_speed_m_s=4, wind_from_degrees=270, temperature_c=30, humidity_percent=40,
    )
    row.update(changes)
    return row


def example_context():
    return dict(
        version=context.VERSION, role='evaluator_only',
        sources=[dict(id='source-1', url='https://example.invalid/weather.csv',
                      sha256='a' * 64, format='csv')],
        geography=dict(crs='EPSG:4326', frame='longitude_latitude_degrees', verified=True,
                       bbox=[-4, 39, -3, 41], source_id='source-1'),
        weather=[example_row()],
    )


def example_anchor(index=0):
    return dict(
        id=f'anchor-{index:03d}', source_incident_id=f'202100000{index}',
        province_code=('4', '28', '21', '2')[index], stratum='small' if index < 2 else 'large',
        detection_local='2021-07-01T12:00:00', ground_response_minutes=20,
        historical_reference=dict(
            ground_arrival='2021-07-01T12:20:00', air_arrival=None,
            controlled='2021-07-01T13:00:00', extinguished='2021-07-01T14:00:00',
            burned_wooded_ha='0', burned_unwooded_ha='0.1' if index < 2 else '600',
        ),
        quality_flags=sorted([
            'detection_local_timezone_unresolved', 'weather_not_actor_input',
            'weather_hour_semantics_unresolved', 'weather_hour_date_mismatch',
            'weather_hour_year_mismatch', 'zero_wind_temperature_missing_humidity',
        ]),
    )


def example_pack():
    pack = dict(
        version=anchors.VERSION, source_archive_sha256=anchors.EXPECTED_SOURCE_ARCHIVE_SHA256,
        source_year=2021, role='retrospective_evaluator_evidence', actor_input_eligible=False,
        anchors=[example_anchor(index) for index in range(4)],
    )
    pack['sha256'] = anchors.anchor_pack_sha256(pack)
    return anchors.validate_anchor_pack(pack)


class ProtectionContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.pack_path = self.directory / 'anchors.json'
        self.context_path = self.directory / 'context.json'
        self.output = self.directory / 'report.json'
        self.pack_path.write_text(json.dumps(example_pack()), encoding='utf-8')
        self.context_path.write_text(json.dumps(example_context()), encoding='utf-8')

    def run_cli(self, *options):
        with redirect_stdout(io.StringIO()):
            return context.main(['--pack', str(self.pack_path), '--output', str(self.output), *options])

    def test_source_anchor_missingness_flags_and_wrapper_refusal(self):
        anchor = example_anchor()
        original = copy.deepcopy(anchor)
        report = context.assess_source_anchor(anchor)
        self.assertEqual(report['source_quality_flags'], original['quality_flags'])
        self.assertTrue(report['weather_calendar_mismatch'])
        self.assertTrue(report['weather_unverified'])
        self.assertTrue(report['weather_missing'])
        self.assertTrue(report['georeference_missing'])
        self.assertFalse(report['readiness_for_historical_replay'])
        self.assertFalse(report['actor_input_eligible'])
        self.assertFalse(report['source_authenticity_verified'])
        self.assertEqual(report['role'], 'evaluator_only')
        self.assertEqual(anchor, original)
        report['source_quality_flags'].clear()
        self.assertEqual(anchor, original)
        anchor['quality_flags'] = ['weather_hour_missing']
        missing = context.assess_source_anchor(anchor)
        self.assertTrue(missing['weather_missing'])
        self.assertFalse(missing['weather_calendar_mismatch'])
        for forbidden in ('wind_speed_m_s', 'source_incident_id', 'historical_reference',
                          'detection_local', 'observation', 'policy_observation'):
            self.assertNotIn(forbidden, report)

    def test_source_anchor_rejects_extra_fields_and_unbounded_flags(self):
        for field, value in [('weather', []), ('id', 'anchor-004'), ('quality_flags', ['duplicate'] * 2),
                             ('quality_flags', ['x' * 81]), ('quality_flags', [None]),
                             ('quality_flags', [str(index) for index in range(33)])]:
            with self.subTest(field=field):
                anchor = example_anchor()
                anchor[field] = value
                with self.assertRaises(ValueError):
                    context.assess_source_anchor(anchor)

    def test_schema_and_declared_metadata_readiness_do_not_enable_replay(self):
        metadata = example_context()
        original = copy.deepcopy(metadata)
        self.assertIs(context.validate_context(metadata), metadata)
        result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        self.assertTrue(result['readiness_for_join'])
        self.assertTrue(result['declared_metadata_validated'])
        self.assertFalse(result['source_authenticity_verified'])
        self.assertFalse(result['historical_replay_ready'])
        self.assertFalse(result['actor_input_eligible'])
        self.assertTrue(result['weather_unverified'])
        self.assertFalse(result['weather_missing'])
        self.assertFalse(result['geography_missing'])
        self.assertEqual(result['eligible_weather_ids'], ['weather-001'])
        self.assertEqual(result['role'], 'evaluator_only')
        self.assertEqual(metadata, original)
        self.assertNotIn('wind_speed_m_s', json.dumps(result))
        self.assertNotIn('policy_observation', result)
        result['limitations'].clear()
        self.assertTrue(context.assess_context(metadata, '2021-07-01T10:05:00Z')['limitations'])

    def test_future_observation_and_availability_and_outside_rows_are_excluded(self):
        metadata = example_context()
        metadata['weather'].extend([
            example_row('future-observation-secret', observed_at='2021-07-01T10:06:00Z',
                        available_at='2021-07-01T10:07:00Z', wind_speed_m_s=999),
            example_row('future-availability-secret', available_at='2021-07-01T10:06:00Z'),
            example_row('outside-secret', longitude=-4.001),
        ])
        result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        self.assertEqual(result['eligible_weather_ids'], ['weather-001'])
        self.assertEqual(result['excluded_weather_counts'], dict(
            future_observation=1, future_availability=1, outside_geography=1, missing_geography=0))
        self.assertIn('future_weather_excluded', result['quality_flags'])
        self.assertIn('weather_outside_coverage_excluded', result['quality_flags'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('999', json.dumps(result))
        before = context.assess_context(metadata, '2021-07-01T09:59:59Z')
        self.assertFalse(before['readiness_for_join'])
        self.assertTrue(before['weather_missing'])
        self.assertEqual(before['eligible_weather_ids'], [])

    def test_timezone_offsets_and_datetime_incident_use_absolute_time(self):
        metadata = example_context()
        metadata['weather'][0].update(observed_at='2021-07-01T12:00:00+02:00',
                                      available_at='2021-07-01T11:05:00+01:00')
        reference = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        for incident in ('2021-07-01T12:05:00+02:00',
                         datetime(2021, 7, 1, 10, 5, tzinfo=timezone.utc)):
            with self.subTest(incident=incident):
                self.assertEqual(context.assess_context(metadata, incident), reference)
        metadata['weather'][0]['available_at'] = '2021-07-01T10:05:00.000001Z'
        self.assertFalse(context.assess_context(metadata, '2021-07-01T10:05:00Z')['readiness_for_join'])

    def test_missing_geography_weather_and_empty_context_stay_missing(self):
        for geography in ('absent', None):
            with self.subTest(geography=geography):
                metadata = example_context()
                if geography == 'absent':
                    del metadata['geography']
                else:
                    metadata['geography'] = None
                result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
                self.assertTrue(result['geography_missing'])
                self.assertTrue(result['weather_missing'])
                self.assertEqual(result['excluded_weather_counts']['missing_geography'], 1)
                self.assertFalse(result['readiness_for_join'])
        metadata = dict(version=context.VERSION, role='evaluator_only', sources=[], weather=[])
        result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        self.assertTrue(result['weather_missing'])
        self.assertTrue(result['geography_missing'])
        self.assertFalse(result['readiness_for_join'])
        metadata = example_context()
        metadata['weather'] = []
        self.assertTrue(context.assess_context(metadata, '2021-07-01T10:05:00Z')['weather_missing'])

    def test_geographic_edges_valid_and_outside_coverage_not_joined(self):
        metadata = example_context()
        metadata['weather'] = [example_row('west-south', longitude=-4, latitude=39),
                               example_row('east-north', longitude=-3, latitude=41)]
        result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        self.assertEqual(result['eligible_weather_ids'], ['east-north', 'west-south'])
        metadata['weather'] = [example_row(longitude=-3, latitude=41.01)]
        result = context.assess_context(metadata, '2021-07-01T10:05:00Z')
        self.assertFalse(result['readiness_for_join'])
        self.assertEqual(result['excluded_weather_counts']['outside_geography'], 1)

    def test_bad_units_unknown_fields_and_nonfinite_values_rejected(self):
        changes = [
            ('wind_speed_m_s', -1), ('wind_speed_m_s', '4'), ('wind_speed_m_s', None),
            ('wind_speed_m_s', True), ('wind_speed_m_s', 10 ** 1000),
            ('wind_from_degrees', 360), ('wind_from_degrees', -1),
            ('temperature_c', -90.1), ('temperature_c', 60.1),
            ('humidity_percent', -1), ('humidity_percent', 100.1),
            ('longitude', -180.1), ('longitude', 180.1), ('latitude', 90.1), ('latitude', -90.1),
            ('units', 'km/h'), ('wind_speed_km_h', 4), ('wind_to_degrees', 270),
            ('temperature_f', 80), ('actor_input_eligible', True), ('location', [40, -3.5]),
        ]
        changes.extend((field, number) for field in ('longitude', 'latitude', 'wind_speed_m_s',
                       'wind_from_degrees', 'temperature_c', 'humidity_percent')
                       for number in (float('nan'), float('inf'), float('-inf')))
        for field, value in changes:
            with self.subTest(field=field, value=value):
                metadata = example_context()
                metadata['weather'][0][field] = value
                with self.assertRaises(ValueError):
                    context.validate_context(metadata)
        metadata = example_context()
        metadata['weather'][0].update(wind_speed_m_s=0, wind_from_degrees=0,
                                      temperature_c=-90, humidity_percent=0)
        self.assertIs(context.validate_context(metadata), metadata)

    def test_invalid_crs_bounds_and_unverified_geography_rejected(self):
        for field, value in [('crs', 'EPSG:3857'), ('frame', 'latitude_longitude'),
                             ('verified', False), ('verified', 1), ('source_id', 'unknown'),
                             ('bbox', [-3, 39, -4, 41]), ('bbox', [-4, 41, -3, 39]),
                             ('bbox', [-4, 39, -4, 41]), ('bbox', [-181, 39, -3, 41]),
                             ('bbox', [-4, 39, -3, 91]), ('bbox', [-4, 39, -3]),
                             ('bbox', [False, 39, -3, 41]), ('bbox', [-4, 39, float('nan'), 41]),
                             ('bbox', [-4, 39, float('inf'), 41]), ('extra', 'private')]:
            with self.subTest(field=field, value=value):
                metadata = example_context()
                metadata['geography'][field] = value
                with self.assertRaises(ValueError):
                    context.validate_context(metadata)

    def test_bad_source_refs_duplicate_source_and_row_ids_rejected(self):
        bad_urls = ['http://example.invalid/weather.csv', 'file:///tmp/weather.csv', 'https://',
                    'https://name:password@example.invalid/weather.csv', 'https://host:bad/file',
                    'https://host:65536/file', 'https://host:0/file', 'https://host/file#fragment',
                    'https://bad_host/file', 'https://host\n.invalid/file', 'https://host\\path',
                    'https://example.invalid/' + 'a' * 2048]
        changes = [('url', url) for url in bad_urls]
        changes += [('sha256', 'A' * 64), ('sha256', 'a' * 63), ('sha256', None),
                    ('format', 'unknown'), ('format', {'units': 'mph'}), ('id', 'a name'),
                    ('extra', 'value')]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                metadata = example_context()
                metadata['sources'][0][field] = value
                with self.assertRaises(ValueError):
                    context.validate_context(metadata)
        for category in ('sources', 'weather'):
            metadata = example_context()
            metadata[category].append(copy.deepcopy(metadata[category][0]))
            with self.subTest(duplicate=category):
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    context.validate_context(metadata)
        metadata = example_context()
        metadata['weather'][0]['source_id'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Unknown weather source'):
            context.validate_context(metadata)

    def test_naive_invalid_unknown_offset_and_impossible_time_order_rejected(self):
        invalid_times = ['2021-07-01T10:00:00', '2021-07-01', '2021-02-30T10:00:00Z',
                         '2021-07-01T10:00:00-00:00', '2021-07-01T10:00:00+01:60',
                         '2021-07-01T10:00:00+24:00', 0, None]
        for value in invalid_times:
            with self.subTest(value=value):
                metadata = example_context()
                metadata['weather'][0]['observed_at'] = value
                with self.assertRaises(ValueError):
                    context.validate_context(metadata)
                with self.assertRaises(ValueError):
                    context.assess_context(example_context(), value)
        with self.assertRaises(ValueError):
            context.assess_context(example_context(), datetime(2021, 7, 1))
        metadata = example_context()
        metadata['weather'][0]['observed_at'] = '2021-07-01T10:06:00Z'
        with self.assertRaisesRegex(ValueError, 'Observation follows'):
            context.validate_context(metadata)

    def test_credential_urls_rejected_without_persisting_credentials(self):
        credential_queries = [
            'token=placeholder-secret', 'access_token=placeholder-secret',
            'refresh_token=placeholder-secret', 'id_token=placeholder-secret',
            'Token=placeholder-secret', 'TOKEN', 'api_key=placeholder-secret',
            'API-KEY=placeholder-secret', 'apiKey=placeholder-secret',
            'x-api-key=placeholder-secret', '%61pi%5Fkey=placeholder-secret',
            'access%5Ftoken=placeholder-secret', 'api%252Dkey=placeholder-secret',
            'api.key=placeholder-secret', 'api+key=placeholder-secret',
            'key=placeholder-secret', 'password=placeholder-secret',
            'username=placeholder-secret', 'X-Amz-Credential=placeholder-secret',
            'X-Amz-Security-Token=placeholder-secret', 'sig=placeholder-secret',
            'year=2021&token=&token=placeholder-secret',
            'year=2021;token=placeholder-secret',
        ]
        urls = ['https://example.invalid/weather.csv?' + query for query in credential_queries]
        urls.extend(['https://placeholder-secret@example.invalid/weather.csv',
                     'https://:placeholder-secret@example.invalid/weather.csv'])
        for url in urls:
            with self.subTest(url=url):
                metadata = example_context()
                metadata['sources'][0]['url'] = url
                self.context_path.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError) as captured:
                    self.run_cli('--context', str(self.context_path), '--incident-time', '2021-07-01T10:05:00Z')
                self.assertNotIn('placeholder-secret', str(captured.exception))
                self.assertFalse(self.output.exists())
        metadata = example_context()
        metadata['sources'][0]['url'] += '?year=2021&station_id=public-1&format=csv'
        self.assertIs(context.validate_context(metadata), metadata)

    def test_schema_required_fields_and_collection_bounds(self):
        for field, value in [('version', 'other'), ('role', 'actor_input'), ('actor_input_eligible', False),
                             ('sources', {}), ('weather', {}), ('observations', [])]:
            metadata = example_context()
            metadata[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    context.validate_context(metadata)
        for category in ('sources', 'weather'):
            metadata = example_context()
            metadata.pop(category)
            with self.assertRaises(ValueError):
                context.validate_context(metadata)
        metadata = example_context()
        metadata['weather'] = [example_row(f'row-{index}') for index in range(1000)]
        self.assertIs(context.validate_context(metadata), metadata)
        metadata['weather'].append(example_row('overflow'))
        with self.assertRaisesRegex(ValueError, 'Too many'):
            context.validate_context(metadata)
        metadata = example_context()
        metadata['sources'] = [dict(metadata['sources'][0], id=f'source-{index}') for index in range(101)]
        with self.assertRaisesRegex(ValueError, 'Too many'):
            context.validate_context(metadata)

    def test_cli_pack_only_needs_no_weather_terrain_or_incident_time(self):
        result = self.run_cli()
        self.assertEqual(result, json.loads(self.output.read_text()))
        self.assertIsNone(result['context'])
        self.assertEqual(result['context_status'], 'not_provided')
        self.assertIn('context_not_provided', result['quality_flags'])
        self.assertFalse(result['declared_metadata_validated'])
        self.assertFalse(result['source_authenticity_verified'])
        self.assertTrue(result['geography_missing'])
        self.assertTrue(result['weather_missing'])
        self.assertFalse(result['readiness_for_join'])
        self.assertFalse(result['historical_replay_ready'])
        self.assertFalse(result['actor_input_eligible'])
        self.assertEqual(len(result['anchors']), 4)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertNotIn('historical_reference', self.output.read_text())

    def test_cli_context_only_emits_eligible_metadata_ids(self):
        metadata = example_context()
        metadata['weather'].append(example_row('future-secret', observed_at='2025-01-01T00:00:00Z',
                                               available_at='2025-01-01T00:00:00Z'))
        self.context_path.write_text(json.dumps(metadata))
        result = self.run_cli('--context', str(self.context_path), '--incident-time', '2021-07-01T10:05:00Z')
        self.assertTrue(result['readiness_for_join'])
        self.assertEqual(result['context_status'], 'assessed')
        self.assertTrue(result['declared_metadata_validated'])
        self.assertFalse(result['source_authenticity_verified'])
        self.assertEqual(result['context']['eligible_weather_ids'], ['weather-001'])
        self.assertNotIn('future-secret', self.output.read_text())
        self.assertNotIn('wind_speed_m_s', self.output.read_text())
        self.assertFalse(result['actor_input_eligible'])

    def test_missing_context_file_produces_deterministic_not_ready_report(self):
        missing_path = self.directory / 'missing-context.json'
        options = ('--context', str(missing_path), '--incident-time', '2021-07-01T10:05:00Z')
        first = self.run_cli(*options)
        self.assertEqual(first['context_status'], 'file_missing')
        self.assertIn('context_file_missing', first['quality_flags'])
        self.assertIsNone(first['context'])
        self.assertFalse(first['readiness_for_join'])
        self.assertFalse(first['declared_metadata_validated'])
        self.assertFalse(first['source_authenticity_verified'])
        self.assertTrue(first['geography_missing'])
        self.assertTrue(first['weather_missing'])
        self.assertFalse(first['historical_replay_ready'])
        self.assertFalse(first['actor_input_eligible'])
        self.assertEqual(len(first['anchors']), 4)
        self.assertNotIn('wind_speed_m_s', self.output.read_text())
        self.output.unlink()
        self.assertEqual(self.run_cli(*options), first)
        self.output.unlink()
        with self.assertRaises(ValueError):
            self.run_cli('--context', str(missing_path), '--incident-time', '2021-07-01T10:05:00')
        self.assertFalse(self.output.exists())

    def test_cli_duplicate_json_fields_invalid_encoding_and_bounded_files(self):
        invalid_payloads = [b'{"version":1,"version":2}', b'\xff', b'[]', b'{',
                            b' ' * (context.MAX_FILE_BYTES + 1)]
        payload = self.context_path.read_text()
        invalid_payloads.append(payload.replace('"wind_speed_m_s": 4',
                                                '"wind_speed_m_s": 4, "wind_speed_m_s": 5').encode())
        invalid_payloads.append(payload.replace('"wind_speed_m_s": 4', '"wind_speed_m_s": NaN').encode())
        for payload in invalid_payloads:
            self.context_path.write_bytes(payload)
            with self.subTest(prefix=payload[:25]):
                with self.assertRaises(ValueError):
                    self.run_cli('--context', str(self.context_path), '--incident-time', '2021-07-01T10:05:00Z')
                self.assertFalse(self.output.exists())
        self.pack_path.write_text('{"version":1,"version":2}')
        with self.assertRaises(ValueError):
            self.run_cli()
        self.pack_path.write_bytes(b' ' * (anchors.MAX_PACK_BYTES + 1))
        with self.assertRaises(ValueError):
            self.run_cli()
        with patch.object(context, 'MAX_FILE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'cap'):
                context.validate_context(example_context())

    def test_cli_nonoverwrite_symlinks_output_bounds_and_option_pairing(self):
        self.output.write_text('preserve')
        with self.assertRaises(FileExistsError):
            self.run_cli()
        self.assertEqual(self.output.read_text(), 'preserve')
        self.output.unlink()
        self.output.symlink_to(self.directory / 'missing')
        with self.assertRaises(FileExistsError):
            self.run_cli()
        self.assertFalse((self.directory / 'missing').exists())
        self.output.unlink()
        with patch.object(context, 'MAX_FILE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'cap'):
                self.run_cli()
        self.assertFalse(self.output.exists())
        for options in [('--context', str(self.context_path)), ('--incident-time', '2021-07-01T10:05:00Z')]:
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(*options)
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
