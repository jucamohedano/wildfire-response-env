"""Offline, bounded operational-data inspection; never creates policy demonstrations."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import statistics
import time
import xml.etree.ElementTree as ElementTree
import zipfile


VERSION = 'wildfire-operational-source-audit-v1'
ICS_DIRECTORY = 'ics209plus-wildfire/'
ICS_INCIDENTS = ICS_DIRECTORY + 'ics209-plus-wf_incidents_1999to2020.csv'
ICS_REPORTS = ICS_DIRECTORY + 'ics209-plus-wf_sitreps_1999to2020.csv'
ICS_FIELDS = (
    'INCIDENT_ID', 'REPORT_FROM_DATE', 'REPORT_TO_DATE', 'LOCAL_TIMEZONE',
    'ACRES', 'TOTAL_PERSONNEL', 'TOTAL_AERIAL', 'PCT_CONTAINED_COMPLETED',
    'EVACUATION_IN_PROGRESS', 'NUM_EVACUATED', 'NUM_EVAC_TO_DATE',
    'TOTAL_EVACUATIONS', 'STR_THREATENED', 'ROAD_CLOSURE_FLAG',
)
EGIF_FIELDS = {
    'province_code': 'pif_localizacion/idprovincia',
    'community_code': 'pif_localizacion/idcomunidad',
    'detection': 'pif_tiempos/deteccion',
    'ground_arrival': 'pif_tiempos/llegadapmt',
    'air_arrival': 'pif_tiempos/llegadapmae',
    'helitack_arrival': 'pif_tiempos/llegadapbh',
    'coordination_aircraft_arrival': 'pif_tiempos/llegadapac',
    'controlled': 'pif_tiempos/controlado',
    'extinguished': 'pif_tiempos/extinguido',
    'first_notification_112': 'pif_deteccion/primeranotificaciondesde112',
    'detector_code': 'pif_deteccion/iddetectadopor',
    'weather_hour': 'pif_condiciones/hora',
    'temperature_max': 'pif_condiciones/tempmaxima',
    'relative_humidity': 'pif_condiciones/humrelativa',
    'wind_speed': 'pif_condiciones/velocidadviento',
    'wind_direction': 'pif_condiciones/direccionviento',
    'burned_wooded_ha': 'pif_perdidas/superficiearboladatotal',
    'burned_unwooded_ha': 'pif_perdidas/superficienoarboladatotal',
}
RESOURCE_TABLES = {
    'personnel': ('RelMedioPersonalPif', 'idmediopersonalext'),
    'ground': ('RelMedioPesadoPif', 'idmediopesado'),
    'air': ('RelMedioAereoPif', 'idmedioaereo'),
}


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def member_info(archive, name, limit):
    matches = [item for item in archive.infolist() if item.filename == name]
    if len(matches) != 1 or matches[0].file_size > limit or matches[0].flag_bits & 1:
        raise ValueError('Expected one unencrypted member within the inspection size cap')
    return matches[0]


def clean(value):
    return None if value is None or value.strip().lower() in ('', 'nan', 'null', 'none') else value.strip()


def sample_groups(rows, group_field, identifier_field, counts):
    buckets = {group: [] for group in counts}
    identifiers = set()
    for row in rows:
        identifier = row[identifier_field]
        if not identifier or identifier in identifiers:
            raise ValueError('Expected unique, nonempty incident identifiers')
        identifiers.add(identifier)
        if row[group_field] in buckets:
            buckets[row[group_field]].append(row)
    selected = []
    for group, count in counts.items():
        if len(buckets[group]) < count:
            raise ValueError(f'Insufficient incidents for group {group}')
        selected.extend(sorted(buckets[group], key=lambda row: hashlib.sha256(
            row[identifier_field].encode('utf-8')).hexdigest())[:count])
    return selected


def sample_ics(path, counts=None, year=2020):
    counts = counts or dict(CA=5, CO=5, OR=5, WA=5)
    with zipfile.ZipFile(path) as archive:
        member_info(archive, ICS_INCIDENTS, 64 * 1024 * 1024)
        member_info(archive, ICS_REPORTS, 384 * 1024 * 1024)
        with archive.open(ICS_INCIDENTS) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
            candidates = [dict(INCIDENT_ID=row['INCIDENT_ID'], POO_STATE=row['POO_STATE'],
                               expected_reports=clean(row['INC_MGMT_NUM_SITREPS'])) for row in reader
                          if clean(row['START_YEAR']) == f'{year}.0'
                          and row['INCTYP_ABBREVIATION'] == 'WF' and row['POO_STATE'] in counts]
        incidents = sample_groups(candidates, 'POO_STATE', 'INCIDENT_ID', counts)
        selected = {row['INCIDENT_ID'] for row in incidents}
        reports = []
        with archive.open(ICS_REPORTS) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
            if not set(ICS_FIELDS).issubset(reader.fieldnames):
                raise ValueError('ICS report schema changed')
            for row in reader:
                if row['INCIDENT_ID'] in selected:
                    reports.append({field: clean(row[field]) for field in ICS_FIELDS})
    counts_found = Counter(row['INCIDENT_ID'] for row in reports)
    if any(row['expected_reports'] is None or counts_found[row['INCIDENT_ID']] != int(
            float(row['expected_reports'])) for row in incidents):
        raise ValueError('Sample does not match the published per-incident report counts')
    return incidents, reports


def egif_rows(path, year):
    with zipfile.ZipFile(path) as archive:
        files = [item.filename for item in archive.infolist() if item.filename.lower().endswith('.xml')]
        if len(files) != 1:
            raise ValueError('Expected one annual EGIF XML member')
        member_info(archive, files[0], 64 * 1024 * 1024)
        with archive.open(files[0]) as raw:
            parser = ElementTree.iterparse(raw, events=('start', 'end'))
            _, root = next(parser)
            for event, element in parser:
                if event == 'end' and element.tag == 'Pif':
                    if element.findtext('pif_comun/anio') != str(year):
                        raise ValueError('EGIF export contains an unexpected year')
                    record = {field: clean(element.findtext(source)) for field, source in EGIF_FIELDS.items()}
                    record['incident_id'] = clean(element.findtext('numeroparte'))
                    record['resources'] = {}
                    for category, (table, code) in RESOURCE_TABLES.items():
                        record['resources'][category] = [
                            dict(type_code=clean(item.findtext(code)),
                                 ownership_code=clean(item.findtext('idtitularidadmedio')),
                                 count=clean(item.findtext('numero')))
                            for item in element.findall(f'pif_medios/{table}')]
                    yield record
                    element.clear()
                    root.clear()


def profile(rows, fields):
    return {field: dict(present=sum(row.get(field) is not None for row in rows),
                        missing=sum(row.get(field) is None for row in rows),
                        zero=sum(row.get(field) is not None and row[field].replace('.', '').isdigit()
                                 and float(row[field]) == 0 for row in rows)) for field in fields}


def forest_area(row):
    values = (row['burned_wooded_ha'], row['burned_unwooded_ha'])
    return None if any(value is None for value in values) else sum(map(float, values))


def inspect(ics_path, egif_path, output):
    started = time.monotonic()
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    if Path(ics_path).stat().st_size > 64 * 1024 * 1024 or Path(egif_path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Archive exceeds the bounded audit size')
    incidents, reports = sample_ics(ics_path)
    spanish = sample_groups(egif_rows(egif_path, 2021), 'province_code', 'incident_id',
                            {'4': 6, '21': 8, '28': 6})
    large = [row for row in egif_rows(egif_path, 2021)
             if forest_area(row) is not None and forest_area(row) > 500]
    if not large:
        raise ValueError('No large-incident records in this export')
    count_found = Counter(row['INCIDENT_ID'] for row in reports)
    gaps = []
    for incident in incidents:
        days = sorted(datetime.fromisoformat(row['REPORT_TO_DATE']).date() for row in reports
                      if row['INCIDENT_ID'] == incident['INCIDENT_ID'] and row['REPORT_TO_DATE'])
        gaps.extend((later - earlier).days for earlier, later in zip(days, days[1:]))
    weather_fields = ('temperature_max', 'relative_humidity', 'wind_speed', 'wind_direction')
    result = dict(version=VERSION, reviewed_on='2026-10-24',
        role='retrospective_evaluator_evidence', actor_input_eligible=False,
        sample_status='development_audit_not_benchmark_or_demonstrations',
        selection='Geographic samples: SHA256 ID ranking, no outcome filtering. Large-fire audit: all exported records with final forest area over 500 ha; evaluator-only size stratum.',
        inputs={name: dict(bytes=Path(path).stat().st_size, sha256=digest_file(path))
                for name, path in [('ics_zip', ics_path), ('egif_zip', egif_path)]},
        ics=dict(year=2020, incidents=len(incidents), reports=len(reports),
                 states=dict(Counter(row['POO_STATE'] for row in incidents)),
                 reports_per_incident=dict(min=min(count_found.values()), max=max(count_found.values()),
                                          median=statistics.median(count_found.values())),
                 calendar_report_gap_days=dict(min=min(gaps), max=max(gaps), median=statistics.median(gaps)),
                 report_counts_match_incident_summary=True,
                 availability=profile(reports, ICS_FIELDS[1:])),
        egif=dict(year=2021, incidents=len(spanish),
                  provinces=dict(Counter(row['province_code'] for row in spanish)),
                  availability=profile(spanish, EGIF_FIELDS),
                  resource_table_present={category: sum(bool(row['resources'][category]) for row in spanish)
                                          for category in RESOURCE_TABLES},
                  zero_temperature_and_wind_with_missing_humidity=sum(
                      row['temperature_max'] is not None and float(row['temperature_max']) == 0
                      and row['wind_speed'] is not None and float(row['wind_speed']) == 0
                      and row['relative_humidity'] is None for row in spanish),
                  all_zero_weather_tuples=sum(all(row[field] is not None and float(row[field]) == 0
                                                  for field in weather_fields) for row in spanish)),
        egif_large=dict(year=2021, incidents=len(large), selection='All qualifying records in this export, not a national census',
                        forest_area_ha=dict(min=min(map(forest_area, large)),
                                            median=statistics.median(map(forest_area, large)),
                                            max=max(map(forest_area, large))),
                        availability=profile(large, EGIF_FIELDS),
                        resource_table_present={category: sum(bool(row['resources'][category]) for row in large)
                                                for category in RESOURCE_TABLES}),
        limitations=[
            'Convenience geography/year sample; not representative and not a policy performance comparison.',
            'Field presence is not accuracy; zero-valued weather and categorical resource codes need interpretation.',
            'All-zero weather is flagged, not assumed calm or automatically declared missing.',
            'Report timestamps have no verified publication/receipt times; source timezone text remains unresolved.',
            'ICS calendar-day gaps ignore intraday timing and cannot calibrate minute-scale communication latency.',
            'EGIF resource totals are not an initial fleet inventory or per-unit dispatch sequence.',
            'Neither sample provides caller transcripts, readback exchanges or counterfactual optimal decisions.',
            'Complex incidents excluded from the US sample; future complex/resource accounting needs separate review.',
            'Source narratives, causes, names and contact fields are not exported by this sampler.',
        ])
    private = output / 'private'
    private.mkdir(parents=True)
    for name, rows in [('ics_incidents', incidents), ('ics_reports', reports),
                       ('egif_incidents', spanish), ('egif_large_incidents', large)]:
        destination = private / f'{name}.jsonl'
        destination.write_text(''.join(json.dumps(dict(row, actor_input_eligible=False), ensure_ascii=False,
                                                  allow_nan=False) + '\n' for row in rows), encoding='utf-8')
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['artifacts'] = {str(path.relative_to(output)): dict(bytes=path.stat().st_size, sha256=digest_file(path))
                           for path in sorted(private.glob('*.jsonl'))}
    (output / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                                       encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ics-zip', required=True)
    parser.add_argument('--egif-zip', required=True)
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    print(json.dumps(inspect(options.ics_zip, options.egif_zip, options.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
