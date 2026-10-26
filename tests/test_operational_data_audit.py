import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile

from eval.audit_operational_data import (
    EGIF_FIELDS, ICS_FIELDS, ICS_INCIDENTS, ICS_REPORTS,
    clean, egif_rows, forest_area, member_info, profile, sample_groups, sample_ics,
)


def csv_text(rows, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def make_ics(path, *, expected='2', omit_field=None):
    incidents = [dict(INCIDENT_ID='exercise-one', POO_STATE='CA', START_YEAR='2020.0',
                      INCTYP_ABBREVIATION='WF', INC_MGMT_NUM_SITREPS=expected),
                 dict(INCIDENT_ID='other-year', POO_STATE='CA', START_YEAR='2019.0',
                      INCTYP_ABBREVIATION='WF', INC_MGMT_NUM_SITREPS='1')]
    reports = []
    for day in ('2020-08-01', '2020-08-02'):
        row = {field: '' for field in ICS_FIELDS}
        row.update(INCIDENT_ID='exercise-one', REPORT_TO_DATE=day, TOTAL_AERIAL='0',
                   INCIDENT_COMMANDERS_NARR='PRIVATE NAME MUST NOT BE EXPORTED')
        reports.append(row)
    fields = [field for field in ICS_FIELDS if field != omit_field] + ['INCIDENT_COMMANDERS_NARR']
    reports = [{field: row[field] for field in fields} for row in reports]
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(ICS_INCIDENTS, csv_text(incidents, incidents[0].keys()))
        archive.writestr(ICS_REPORTS, csv_text(reports, fields))


class OperationalAuditTests(unittest.TestCase):
    def test_unknown_forest_area_is_not_zero(self):
        self.assertIsNone(forest_area(dict(burned_wooded_ha='10', burned_unwooded_ha=None)))
        self.assertEqual(forest_area(dict(burned_wooded_ha='0', burned_unwooded_ha='501')), 501)

    def test_missing_is_not_zero(self):
        self.assertIsNone(clean(' NaN '))
        self.assertIsNone(clean(''))
        self.assertEqual(clean(' 0 '), '0')
        result = profile([{'count': None}, {'count': '0'}, {'count': '2'}], ['count'])
        self.assertEqual(result['count'], dict(present=2, missing=1, zero=1))

    def test_sampling_is_order_independent_and_fails_on_insufficient_groups(self):
        rows = [dict(id=f'case-{index}', group='region') for index in range(4)]
        selected = sample_groups(rows, 'group', 'id', {'region': 2})
        self.assertEqual(selected, sample_groups(reversed(rows), 'group', 'id', {'region': 2}))
        with self.assertRaises(ValueError):
            sample_groups(rows, 'group', 'id', {'missing': 1})
        with self.assertRaises(ValueError):
            sample_groups(rows + [rows[0]], 'group', 'id', {'region': 2})

    def test_full_ics_sequence_is_selected_without_narratives(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'ics.zip')
            make_ics(path)
            incidents, reports = sample_ics(path, {'CA': 1})
            self.assertEqual(len(incidents), 1)
            self.assertEqual(len(reports), 2)
            self.assertEqual(set(reports[0]), set(ICS_FIELDS))
            self.assertEqual(reports[0]['TOTAL_AERIAL'], '0')
            self.assertIsNone(reports[0]['NUM_EVACUATED'])
            self.assertNotIn('PRIVATE NAME', str(reports))

    def test_ics_count_mismatch_and_schema_change_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'ics.zip')
            make_ics(path, expected='3')
            with self.assertRaises(ValueError):
                sample_ics(path, {'CA': 1})
            make_ics(path, omit_field='NUM_EVACUATED')
            with self.assertRaises(ValueError):
                sample_ics(path, {'CA': 1})

    def test_egif_keeps_codes_and_missing_values_without_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'egif.zip')
            root = ElementTree.Element('Egif')
            record = ElementTree.SubElement(root, 'Pif')
            ElementTree.SubElement(record, 'numeroparte').text = '2021210001'
            common = ElementTree.SubElement(record, 'pif_comun')
            ElementTree.SubElement(common, 'anio').text = '2021'
            place = ElementTree.SubElement(record, 'pif_localizacion')
            ElementTree.SubElement(place, 'idprovincia').text = '21'
            conditions = ElementTree.SubElement(record, 'pif_condiciones')
            ElementTree.SubElement(conditions, 'velocidadviento').text = '0'
            cause = ElementTree.SubElement(record, 'pif_causa')
            ElementTree.SubElement(cause, 'nombrecausante').text = 'PRIVATE NAME'
            resources = ElementTree.SubElement(record, 'pif_medios')
            aircraft = ElementTree.SubElement(resources, 'RelMedioAereoPif')
            ElementTree.SubElement(aircraft, 'idmedioaereo').text = '2'
            ElementTree.SubElement(aircraft, 'numero').text = '1'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('sample.xml', ElementTree.tostring(root))
            rows = list(egif_rows(path, 2021))
            self.assertEqual(rows[0]['wind_speed'], '0')
            self.assertIsNone(rows[0]['relative_humidity'])
            self.assertEqual(rows[0]['resources']['air'][0]['type_code'], '2')
            self.assertEqual(set(rows[0]), set(EGIF_FIELDS) | {'incident_id', 'resources'})
            self.assertNotIn('PRIVATE NAME', str(rows))
            with self.assertRaises(ValueError):
                list(egif_rows(path, 2020))

    def test_archive_member_size_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'sample.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('large', b'12345')
            with zipfile.ZipFile(path) as archive:
                with self.assertRaises(ValueError):
                    member_info(archive, 'large', 4)
                with self.assertRaises(ValueError):
                    member_info(archive, 'missing', 10)


if __name__ == '__main__':
    unittest.main()
