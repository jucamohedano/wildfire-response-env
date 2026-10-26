import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ElementTree
import zipfile

from envs.wildfire_response.regional.grounded import anchors


def source_record(identifier, province, area, **changes):
    record = dict(
        incident_id=identifier, province_code=province,
        community_code={'4': '4', '21': '4', '28': '16'}.get(province, '11'),
        year='2021', detection='2021-07-01T12:00:00',
        ground_arrival='2021-07-01T12:20:00', air_arrival=None,
        controlled='2021-07-01T13:00:00', extinguished='2021-07-02T14:00:00',
        burned_wooded_ha='0', burned_unwooded_ha=area,
        weather_hour='2021-07-01T12:00:00', wind_speed='4',
        temperature_max='30', relative_humidity='40',
    )
    record.update(changes)
    return record


def fixture_records():
    return [
        source_record('2021040001', '4', '0.1', weather_hour=None,
                      wind_speed='0', temperature_max='0', relative_humidity=None),
        source_record('2021280001', '28', '0.9', weather_hour='2025-01-02T12:00:00'),
        source_record('2021210001', '21', '501', weather_hour='2021-07-02T12:00:00'),
        source_record('2021020001', '2', '900', weather_hour='unresolved'),
    ]


def xml_payload(records):
    fields = dict(anchors._HISTORICAL_FIELDS, incident_id='numeroparte',
                  province_code='pif_localizacion/idprovincia',
                  community_code='pif_localizacion/idcomunidad', year='pif_comun/anio',
                  detection='pif_tiempos/deteccion', weather_hour='pif_condiciones/hora',
                  wind_speed='pif_condiciones/velocidadviento',
                  temperature_max='pif_condiciones/tempmaxima',
                  relative_humidity='pif_condiciones/humrelativa')
    root = ElementTree.Element('pifs')
    for record in records:
        incident = ElementTree.SubElement(root, 'Pif')
        for field, source in fields.items():
            parent = incident
            for tag in source.split('/'):
                child = parent.find(tag)
                parent = child if child is not None else ElementTree.SubElement(parent, tag)
            parent.text = record[field]
        ElementTree.SubElement(incident, 'observaciones').text = 'PRIVATE NARRATIVE'
        ElementTree.SubElement(incident, 'nombre').text = 'PRIVATE IDENTITY'
        ElementTree.SubElement(incident, 'causa').text = 'PRIVATE CAUSE'
    return ElementTree.tostring(root, encoding='utf-8', xml_declaration=True)


class GroundedAnchorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.archive = self.directory / 'annual.zip'
        self.output = self.directory / 'anchors.json'
        self.write_archive(fixture_records())
        self.expected = patch.object(anchors, 'EXPECTED_SOURCE_ARCHIVE_SHA256', self.archive_digest())
        self.expected.start()
        self.addCleanup(self.expected.stop)

    def archive_digest(self):
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def write_archive(self, records=None, payload=None, names=('annual.xml',)):
        with zipfile.ZipFile(self.archive, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.writestr(name, payload if payload is not None else xml_payload(records))

    def build(self, records=None):
        if records is not None:
            self.write_archive(records)
        with patch.object(anchors, 'EXPECTED_SOURCE_ARCHIVE_SHA256', self.archive_digest()):
            return anchors.build_anchor_pack(self.archive, self.output)

    def assert_invalid(self, mutate):
        pack = anchors.build_anchor_pack(self.archive, self.output)
        mutate(pack)
        pack['sha256'] = anchors.anchor_pack_sha256(pack)
        with self.assertRaises(ValueError):
            anchors.validate_anchor_pack(pack)

    def test_deterministic_private_pack_and_load_isolation(self):
        first = anchors.build_anchor_pack(self.archive, self.output)
        other = self.directory / 'other.json'
        second = anchors.build_anchor_pack(self.archive, other)
        self.assertEqual(first, second)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertIs(anchors.validate_anchor_pack(first), first)
        self.assertEqual(first['version'], anchors.VERSION)
        self.assertFalse(first['actor_input_eligible'])
        self.assertEqual(first['source_year'], 2021)
        self.assertEqual(first['role'], 'retrospective_evaluator_evidence')
        self.assertEqual([anchor['id'] for anchor in first['anchors']],
                         ['anchor-000', 'anchor-001', 'anchor-002', 'anchor-003'])
        self.assertEqual([(anchor['stratum'], anchor['province_code']) for anchor in first['anchors']],
                         [('small', '4'), ('small', '28'), ('large', '21'), ('large', '2')])
        first['anchors'][0]['historical_reference']['ground_arrival'] = 'changed'
        loaded = anchors.load_anchor_pack(self.output)
        loaded['anchors'][0]['quality_flags'].clear()
        self.assertEqual(anchors.load_anchor_pack(self.output), second)
        serialized = self.output.read_text()
        for forbidden in ('PRIVATE', 'cause', 'causa', 'narrative', 'nombre', 'wind_speed',
                          'temperature_max', 'relative_humidity', 'community_code', 'resources'):
            self.assertNotIn(forbidden, serialized)

    def test_selection_ranks_eligible_ids_not_outcomes_or_xml_order(self):
        records = fixture_records()
        records.extend([
            source_record('2021040002', '4', '0.01', ground_arrival='2021-07-01T12:01:00'),
            source_record('2021040003', '4', '0.99', ground_arrival='2021-07-01T12:59:00'),
            source_record('2021030001', '3', '600', ground_arrival='2021-07-01T12:00:00'),
        ])
        first = self.build(records)
        expected_small = min(records[:1] + records[4:6],
                             key=lambda row: hashlib.sha256(row['incident_id'].encode()).hexdigest())
        self.assertEqual(first['anchors'][0]['source_incident_id'], expected_small['incident_id'])
        self.output.unlink()
        second = self.build(list(reversed(records)))
        self.assertEqual(first['anchors'], second['anchors'])

    def test_ineligible_records_do_not_fill_missing_bucket(self):
        invalid_changes = [
            dict(ground_arrival=None), dict(ground_arrival='2021-07-01T11:59:00'),
            dict(ground_arrival='2021-07-01T14:00:01', controlled=None),
            dict(controlled='2021-07-01T12:19:00'),
            dict(extinguished='2021-07-01T12:19:00', controlled=None),
            dict(controlled='2021-07-03T12:00:00'),
            dict(air_arrival='2021-07-01T11:59:00'),
            dict(air_arrival='2021-07-01T13:01:00'),
            dict(detection='2025-07-01T12:00:00'),
            dict(detection='2021-07-01T12:00:00Z'),
            dict(ground_arrival='invalid'), dict(burned_unwooded_ha='1'),
            dict(burned_unwooded_ha='NaN'), dict(burned_unwooded_ha='Infinity'),
            dict(burned_unwooded_ha=None), dict(burned_wooded_ha='-1'),
            dict(community_code='16'), dict(province_code='04'),
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                records = fixture_records()
                records[0].update(changes)
                with self.assertRaisesRegex(ValueError, 'bucket'):
                    self.build(records)
                self.assertFalse(self.output.exists())
        records = fixture_records()
        records[2]['burned_unwooded_ha'] = '500'
        with self.assertRaisesRegex(ValueError, 'bucket'):
            self.build(records)

    def test_area_is_sum_and_duration_uses_naive_source_times(self):
        records = fixture_records()
        records[0].update(detection='2021-07-01T23:59:30', ground_arrival='2021-07-02T00:00:00',
                          controlled=None, extinguished=None, burned_wooded_ha='0.2',
                          burned_unwooded_ha='0.3')
        records[1].update(ground_arrival='2021-07-01T14:00:00', controlled=None, extinguished=None)
        records[2].update(burned_wooded_ha='250.5', burned_unwooded_ha='250')
        pack = self.build(records)
        self.assertEqual(pack['anchors'][0]['ground_response_minutes'], 0.5)
        self.assertEqual(pack['anchors'][1]['ground_response_minutes'], 120)
        self.assertIsNone(pack['anchors'][0]['historical_reference']['controlled'])
        self.assertEqual(pack['anchors'][2]['historical_reference']['burned_wooded_ha'], '250.5')

    def test_weather_anomalies_never_verify_wind(self):
        pack = anchors.build_anchor_pack(self.archive, self.output)
        flags = [set(anchor['quality_flags']) for anchor in pack['anchors']]
        self.assertTrue({'weather_hour_missing', 'zero_wind_temperature_missing_humidity'} <= flags[0])
        self.assertTrue({'weather_hour_date_mismatch', 'weather_hour_year_mismatch'} <= flags[1])
        self.assertIn('weather_hour_date_mismatch', flags[2])
        self.assertNotIn('weather_hour_year_mismatch', flags[2])
        self.assertIn('weather_hour_invalid', flags[3])
        for anchor in pack['anchors']:
            self.assertIn('weather_not_actor_input', anchor['quality_flags'])
            self.assertIn('weather_hour_semantics_unresolved', anchor['quality_flags'])
            self.assertIn('detection_local_timezone_unresolved', anchor['quality_flags'])
        self.output.unlink()
        records = fixture_records()
        records[0].update(wind_speed=None, temperature_max='0')
        changed = self.build(records)
        self.assertNotIn('zero_wind_temperature_missing_humidity', changed['anchors'][0]['quality_flags'])

    def test_strict_schema_privacy_and_time_validation_even_with_new_digest(self):
        base = anchors.build_anchor_pack(self.archive, self.output)
        changes = [
            ('cause', 'private'), ('narrative', 'private'), ('wind', 0),
            ('ground_response_minutes', True), ('ground_response_minutes', -1),
            ('ground_response_minutes', 121), ('ground_response_minutes', 10 ** 1000),
            ('ground_response_minutes', '20'), ('ground_response_minutes', 19),
            ('detection_local', '2021-07-01T12:00:00+00:00'),
            ('detection_local', '2021-07-01'), ('detection_local', '2021-02-30T12:00:00'),
            ('detection_local', '2020-07-01T12:00:00'), ('id', '2021040001'),
            ('province_code', 4), ('province_code', '53'), ('stratum', 'large'),
            ('quality_flags', []), ('quality_flags', ['verified_wind']),
            ('source_incident_id', 'a name'),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                pack = copy.deepcopy(base)
                pack['anchors'][0][field] = value
                pack['sha256'] = anchors.anchor_pack_sha256(pack)
                with self.assertRaises(ValueError):
                    anchors.validate_anchor_pack(pack)
        for field, value in [('cause', 'private'), ('burned_wooded_ha', 0),
                             ('burned_wooded_ha', None), ('burned_wooded_ha', 'nan'),
                             ('burned_wooded_ha', 'inf'), ('burned_wooded_ha', '-0.1'),
                             ('burned_wooded_ha', '1_0'), ('burned_wooded_ha', '1' * 129),
                             ('ground_arrival', None), ('controlled', '2021-07-01T12:01:00')]:
            with self.subTest(historical_field=field):
                pack = copy.deepcopy(base)
                pack['anchors'][0]['historical_reference'][field] = value
                pack['sha256'] = anchors.anchor_pack_sha256(pack)
                with self.assertRaises(ValueError):
                    anchors.validate_anchor_pack(pack)
        for field, value in [('actor_input_eligible', True), ('source_year', True),
                             ('source_year', 2025), ('role', 'actor_input'),
                             ('source_archive_sha256', '0' * 64), ('extra', None),
                             ('version', 'other'), ('anchors', base['anchors'][:3])]:
            with self.subTest(pack_field=field):
                pack = copy.deepcopy(base)
                pack[field] = value
                pack['sha256'] = anchors.anchor_pack_sha256(pack)
                with self.assertRaises(ValueError):
                    anchors.validate_anchor_pack(pack)

    def test_duplicate_ids_and_inconsistent_flags_rejected(self):
        def duplicate(pack):
            pack['anchors'][1]['source_incident_id'] = pack['anchors'][0]['source_incident_id']
        self.assert_invalid(duplicate)
        self.output.unlink()

        def flags(pack):
            pack['anchors'][0]['quality_flags'] = sorted(anchors._BASE_FLAGS | {'weather_hour_year_mismatch'})
        self.assert_invalid(flags)

    def test_canonical_digest_and_tamper_detection(self):
        pack = anchors.build_anchor_pack(self.archive, self.output)
        digest = pack['sha256']
        reversed_pack = dict(reversed(list(pack.items())))
        reversed_pack['sha256'] = 'ignored'
        self.assertEqual(anchors.anchor_pack_sha256(reversed_pack), digest)
        payload = {key: value for key, value in pack.items() if key != 'sha256'}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertEqual(digest, expected)
        pack['anchors'][0]['source_incident_id'] = '2021040099'
        self.output.write_text(json.dumps(pack))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            anchors.load_anchor_pack(self.output)
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(nonfinite=value):
                pack['anchors'][0]['ground_response_minutes'] = value
                with self.assertRaises(ValueError):
                    anchors.anchor_pack_sha256(pack)
                with self.assertRaises(ValueError):
                    anchors.validate_anchor_pack(pack)

    def test_input_hash_rejected_without_bypass(self):
        with patch.object(anchors, 'EXPECTED_SOURCE_ARCHIVE_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                anchors.build_anchor_pack(self.archive, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_and_symlink_are_not_overwritten(self):
        self.output.write_text('keep')
        with self.assertRaises(FileExistsError):
            anchors.build_anchor_pack(self.archive, self.output)
        self.assertEqual(self.output.read_text(), 'keep')
        self.output.unlink()
        self.output.symlink_to(self.directory / 'missing')
        with self.assertRaises(FileExistsError):
            anchors.build_anchor_pack(self.archive, self.output)
        self.assertFalse((self.directory / 'missing').exists())

    def test_load_bounds_invalid_json_and_duplicate_keys(self):
        for payload in (b' ' * (anchors.MAX_PACK_BYTES + 1), b'\xff', b'[]',
                        b'{"version":1,"version":2}', b'{'):
            with self.subTest(prefix=payload[:25]):
                self.output.write_bytes(payload)
                with self.assertRaises(ValueError):
                    anchors.load_anchor_pack(self.output)

    def test_bounded_archive_and_xml_validation(self):
        for option, limit in [('MAX_ARCHIVE_BYTES', 1), ('MAX_XML_BYTES', 1), ('MAX_RECORDS', 3)]:
            with self.subTest(option=option):
                with patch.object(anchors, option, limit):
                    with self.assertRaises(ValueError):
                        anchors.build_anchor_pack(self.archive, self.output)
                self.assertFalse(self.output.exists())
        for payload in (b'<!DOCTYPE pifs [<!ENTITY injected "text">]><pifs/>',
                        b'<pifs>', b'', '<pifs/>'.encode('utf-16')):
            with self.subTest(payload=payload):
                self.write_archive(payload=payload)
                with patch.object(anchors, 'EXPECTED_SOURCE_ARCHIVE_SHA256', self.archive_digest()):
                    with self.assertRaises(ValueError):
                        anchors.build_anchor_pack(self.archive, self.output)
        self.write_archive(fixture_records(), names=('first.xml', 'second.xml'))
        with patch.object(anchors, 'EXPECTED_SOURCE_ARCHIVE_SHA256', self.archive_digest()):
            with self.assertRaisesRegex(ValueError, 'XML member'):
                anchors.build_anchor_pack(self.archive, self.output)

    def test_source_year_and_duplicate_source_ids_rejected(self):
        records = fixture_records()
        records[1]['incident_id'] = records[0]['incident_id']
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.build(records)
        records = fixture_records()
        records[1]['year'] = '2025'
        with self.assertRaisesRegex(ValueError, 'year'):
            self.build(records)


if __name__ == '__main__':
    unittest.main()
