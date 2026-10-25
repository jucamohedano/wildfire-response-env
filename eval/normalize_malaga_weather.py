"""Bounded, offline normalization of the September 2021 Estepona source clock."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = 'malaga-weather-normalization-v1'
START = datetime(2021, 9, 8, 0, 30, tzinfo=timezone.utc)
END = datetime(2021, 9, 16, tzinfo=timezone.utc)
FIELDS = {
    'temperature_c': 'TempMedia',
    'relative_humidity_percent': 'HumedadMedia',
    'wind_speed_m_s': 'VelViento',
    'wind_direction_degrees': 'DirViento',
    'precipitation_mm': 'Precipitacion',
}
HEADER = ['Fecha', 'Hora', 'Temp Media (ºC)', 'Hum Media (%)',
          'Vel Viento (m/s)', 'Dir Viento (º)', 'Precip (mm)']
RECORD_KEYS = set(FIELDS) | {
    'source_row', 'source_date', 'source_hour', 'observed_at_utc',
    'available_at_utc', 'actor_input_eligible', 'clock_ambiguous',
}
MANUAL = {
    'url': 'https://servicio.mapa.gob.es/siarweb/documentos/manuales/ManualUsoWebSiAR.pdf',
    'pdf_pages': [22, 25],
    'sha256': '3a55586b1e3ccde386a2f9f4875c9aa45e74241598a04b8ebfcfe974db738264',
    'scope': 'Displayed hourly/subdaily clock is UTC from 2014-04-01 onward.',
}
SCRIPT_SHA256 = '3da22a58ce002b2ebbd3bd371c7979e98ccb95975cfa4a2e3a3c64f1c22a4b70'
LIMITATIONS = [
    'Timestamps identify the documented source measurement clock; interval start/end semantics remain unknown.',
    'Historical publication, availability and decision-maker receipt are unknown; no receipt times are inferred.',
    'Wind FROM/TO convention, sensor height and fire-front representativeness are unverified.',
    'Mean wind is not gust data or an engine wind index; no simulator conversion is performed.',
    'Finite exported values do not establish sensor completeness or quality flags. No values are imputed.',
    'Chart fecha carries +02:00 serialization offsets; these are not treated as measurement timezone evidence. '
    'The manual establishes the displayed UTC clock, and fechaStr plus the official parser establish calendar rollover.',
]


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def file_info(path, data, root):
    resolved = Path(path).resolve()
    try:
        name = resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        name = str(resolved)
    return dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def read_pinned(path, expected_sha256, root, limit=2 * 1024 * 1024):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('An explicit lowercase SHA-256 expectation is required')
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Input exceeds the bounded byte limit')
    info = file_info(path, data, root)
    if info['sha256'] != expected_sha256:
        raise ValueError('Input SHA-256 differs from caller expectation')
    return data, info


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Nonfinite JSON constant: ' + value)


def load_json(data):
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def utc_label(moment):
    return moment.isoformat(timespec='seconds').replace('+00:00', 'Z')


def source_clock(date_label, hour_label):
    if not isinstance(date_label, str) or not re.fullmatch(r'\d{2}/\d{2}/2021', date_label):
        raise ValueError('Invalid source date label')
    if not isinstance(hour_label, str) or not re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-3]):(?:00|30)|24:00', hour_label):
        raise ValueError('Invalid half-hour source clock label')
    date = datetime.strptime(date_label, '%d/%m/%Y').replace(tzinfo=timezone.utc)
    hours, minutes = map(int, hour_label.split(':'))
    return date, hours, minutes


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_document(document):
    expected_keys = {'version', 'role', 'actor_input_eligible', 'station', 'header', 'records'}
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise ValueError('Unexpected input document fields')
    if (document['version'] != 'malaga-measured-halfhourly-candidate-v1'
            or document['role'] != 'retrospective_evaluator_context'
            or document['station'] != 'MA04 Estepona'
            or document['actor_input_eligible'] is not False
            or document['header'] != HEADER):
        raise ValueError('Unexpected source identity, header or eligibility')
    records = document['records']
    if not isinstance(records, list) or not 1 <= len(records) <= 384:
        raise ValueError('Expected between 1 and 384 source rows')
    identities, labels = set(), set()
    for row in records:
        if not isinstance(row, dict) or set(row) != RECORD_KEYS:
            raise ValueError('Unexpected row fields')
        identity = row['source_row']
        if type(identity) is not int or not 2 <= identity <= 385 or identity in identities:
            raise ValueError('Invalid or duplicate source row identity')
        identities.add(identity)
        date, hours, minutes = source_clock(row['source_date'], row['source_hour'])
        key = row['source_date'], row['source_hour']
        if key in labels:
            raise ValueError('Duplicate source date/hour label')
        labels.add(key)
        if hours == 24:
            valid_span = START.date() < date.date() <= END.date()
        else:
            valid_span = START <= date + timedelta(hours=hours, minutes=minutes) < END
            valid_span = valid_span and (hours, minutes) != (0, 0)
        if not valid_span:
            raise ValueError('Source clock is outside the bounded query span')
        if (row['observed_at_utc'] is not None or row['available_at_utc'] is not None
                or row['actor_input_eligible'] is not False
                or type(row['clock_ambiguous']) is not bool
                or row['clock_ambiguous'] != (hours == 24)):
            raise ValueError('Unexpected input timestamp, eligibility or ambiguity flag')
        for field in FIELDS:
            if not finite_number(row[field]):
                raise ValueError('Expected a finite numeric measurement: ' + field)
        if not (-100 <= row['temperature_c'] <= 100
                and 0 <= row['relative_humidity_percent'] <= 100
                and 0 <= row['wind_speed_m_s'] <= 150
                and 0 <= row['wind_direction_degrees'] <= 360
                and 0 <= row['precipitation_mm'] <= 1000):
            raise ValueError('Measurement outside broad validation bounds')
    return records


def extract_chart(html, script):
    if hashlib.sha256(script).hexdigest() != SCRIPT_SHA256:
        raise ValueError('Chart parser script is not the audited official snapshot')
    pattern = r'resultadoConsultaChart\.set\("29_4_Estepona",(\[.*?\])\);'
    matches = re.findall(pattern, html.decode('utf-8'))
    if len(matches) != 1:
        raise ValueError('Expected one official Estepona chart series')
    rows = load_json(matches[0])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 384:
        raise ValueError('Chart series exceeds the bounded row count')
    sanitized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not {'fecha', 'fechaStr', 'hora', 'resultadoVariable'} <= set(row):
            raise ValueError('Missing chart timestamp/value fields')
        if row.get('tieneDatos') is not True:
            raise ValueError('Chart reports missing data')
        if not isinstance(row['fechaStr'], str) or row['fechaStr'].count(' ') != 1:
            raise ValueError('Invalid chart clock label')
        date_label, hour_label = row['fechaStr'].split(' ')
        date, hours, minutes = source_clock(date_label, hour_label)
        wall_clock = date + timedelta(hours=hours, minutes=minutes)
        if not START <= wall_clock <= END:
            raise ValueError('Chart timestamp outside query span')
        expected_fecha = wall_clock.replace(tzinfo=None).isoformat(timespec='milliseconds') + '+02:00'
        if row['fecha'] != expected_fecha or row['hora'] != str(hours * 100 + minutes):
            raise ValueError('Chart fecha/fechaStr/hora disagree')
        variables = row['resultadoVariable']
        if not isinstance(variables, list) or len(variables) != len(FIELDS):
            raise ValueError('Unexpected chart measurement count')
        measurements = {}
        for variable in variables:
            if (not isinstance(variable, dict)
                    or variable.get('nombreCampoTablaDatos') not in FIELDS.values()
                    or variable['nombreCampoTablaDatos'] in measurements
                    or not isinstance(variable.get('valor'), str)):
                raise ValueError('Invalid or duplicate chart variable')
            try:
                value = float(variable['valor'])
            except ValueError as error:
                raise ValueError('Invalid chart numeric value') from error
            if not finite_number(value):
                raise ValueError('Nonfinite chart measurement')
            measurements[variable['nombreCampoTablaDatos']] = value
        sanitized.append(dict(chart_index=index, fecha=row['fecha'], fechaStr=row['fechaStr'],
                              hora=row['hora'], source_clock_utc=utc_label(wall_clock),
                              measurements=measurements))
    return sanitized


def normalize_document(document, input_info, chart_rows=None):
    records = validate_document(document)
    chart_lookup = {}
    if chart_rows is not None:
        if len(chart_rows) != len(records):
            raise ValueError('Source/chart row counts differ')
        for chart in chart_rows:
            chart_date = chart['fecha'][:10]
            key = chart_date, chart['hora']
            if key in chart_lookup:
                raise ValueError('Duplicate chart timestamp identity')
            chart_lookup[key] = chart
    normalized, known_times, used_charts = [], set(), set()
    for index, row in enumerate(records):
        date, hours, minutes = source_clock(row['source_date'], row['source_hour'])
        chart = None
        if chart_rows is not None:
            key = date.date().isoformat(), str(hours * 100 + minutes)
            chart = chart_lookup.get(key)
            if chart is None or any(chart['measurements'][column] != row[field]
                                    for field, column in FIELDS.items()):
                raise ValueError('Source/chart identity or measurement mismatch')
            used_charts.add(key)
        timestamp = None
        classification = 'unknown_24_hour_boundary'
        reason = 'Next-day table date plus 24:00 is ambiguous without independent chart evidence; order is not proof.'
        if hours != 24:
            timestamp = utc_label(date + timedelta(hours=hours, minutes=minutes))
            classification = 'documented_source_clock_utc'
            reason = 'Unambiguous table calendar/clock label; official manual specifies UTC.'
        elif chart is not None:
            timestamp = chart['source_clock_utc']
            classification = 'chart_verified_midnight_utc'
            reason = 'Matched all five measurements to alternate official fechaStr and fecha; audited chart parser rolls preceding-day 24:00 to this midnight. Manual specifies UTC.'
        if timestamp is not None:
            if timestamp in known_times:
                raise ValueError('Duplicate normalized timestamp')
            known_times.add(timestamp)
        provenance = dict(input_file=input_info['path'], input_sha256=input_info['sha256'],
                          json_pointer=f'/records/{index}', source_row=row['source_row'],
                          timestamp_classification=classification, timestamp_reason=reason,
                          timezone_evidence=MANUAL,
                          chart_evidence_pointer=None if chart is None else f"/records/{chart['chart_index']}")
        output = dict(row)
        output.update(observed_at_utc=timestamp, received_at_utc=None, available_at_utc=None,
                      clock_ambiguous=timestamp is None, source_clock_ambiguous=row['clock_ambiguous'],
                      measurement_time_semantics='source_label; interval start/end unknown',
                      receipt_time_status='unknown', historical_replay_ready=False,
                      provenance=provenance)
        normalized.append(output)
    if len(used_charts) != len(chart_lookup):
        raise ValueError('Unmatched chart evidence rows')
    expected_times = []
    moment = START
    while moment <= END:
        expected_times.append(utc_label(moment))
        moment += timedelta(minutes=30)
    gaps = [label for label in expected_times if label not in known_times]
    checks = dict(input_schema_valid=True, finite_measurements=True, unique_source_rows=True,
                  unique_source_labels=True, unique_normalized_timestamps=True,
                  source_rows=len(records), normalized_rows=len(known_times),
                  unresolved_rows=len(records) - len(known_times),
                  chart_value_matches=0 if chart_rows is None else len(used_charts),
                  midnight_rows_resolved=sum(row['provenance']['timestamp_classification'] ==
                                             'chart_verified_midnight_utc' for row in normalized),
                  expected_halfhour_slots=len(expected_times), gap_count=len(gaps),
                  gap_timestamps_utc=gaps, complete_source_clock_grid=not gaps,
                  imputed_rows=0, known_receipt_times=0,
                  interval_start_end_semantics_verified=False)
    status = 'source_clock_normalized' if not checks['unresolved_rows'] else 'partial_timestamp_normalization'
    return dict(version=VERSION, status=status, station=document['station'],
                role='retrospective_evaluator_context', actor_input_eligible=False,
                historical_replay_ready=False, query_clock_span_utc=[utc_label(START), utc_label(END)],
                checks=checks, limitations=LIMITATIONS, records=normalized)


def build_bundle(root, input_path, expected_sha256, output_dir, *, chart_html=None,
                 expected_html_sha256=None, chart_script=None, expected_script_sha256=None):
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    allowed_output = root / '.runtime/malaga-weather-normalized-v1'
    if output_dir != allowed_output:
        raise ValueError('Outputs must use .runtime/malaga-weather-normalized-v1/')
    source, source_info = read_pinned(input_path, expected_sha256, root)
    inputs = [source_info]
    outputs = []
    chart_rows = None
    evidence_args = (chart_html, expected_html_sha256, chart_script, expected_script_sha256)
    if any(value is not None for value in evidence_args):
        if any(value is None for value in evidence_args):
            raise ValueError('Chart HTML and audited script each require an explicit hash')
        html, html_info = read_pinned(chart_html, expected_html_sha256, root)
        script, script_info = read_pinned(chart_script, expected_script_sha256, root)
        inputs.extend([html_info, script_info])
        chart_rows = extract_chart(html, script)
        evidence = dict(version=VERSION + '-chart-evidence',
                        source_html_reference=html_info,
                        script_reference=script_info,
                        script_url='https://servicio.mapa.gob.es/siarweb/js/publico/consultaDatos/resultadosConsultaDatos.js',
                        parser='parseFechaHora: local calendar constructor with explicit day+1 for 24:00',
                        offset_policy='Preserve fecha literally; use fechaStr calendar rollover and documented UTC source clock, never convert +02:00 as an observation timezone.',
                        records=chart_rows)
        outputs.append((output_dir / 'chart_evidence.json', encoded(evidence)))
    document = normalize_document(load_json(source), source_info, chart_rows)
    outputs.append((output_dir / 'weather.json', encoded(document)))
    destinations = [path for path, data in outputs] + [output_dir / 'audit.json']
    if any(path.is_symlink() for path in destinations):
        raise ValueError('Output files must not be symbolic links')
    if any(path.resolve() == Path(item['path'] if Path(item['path']).is_absolute()
                                  else root / item['path']).resolve()
           for path in destinations for item in inputs):
        raise ValueError('Output would overwrite an input')
    output_infos = [file_info(path, data, root) for path, data in outputs]
    audit = {key: document[key] for key in ('version', 'status', 'actor_input_eligible',
                                           'historical_replay_ready', 'checks', 'limitations')}
    audit.update(input_files=inputs, output_files=output_infos,
                 timezone_evidence=MANUAL,
                 timestamp_mapping='weather.json records[*].provenance maps every source row; chart_evidence_pointer refers to chart_evidence.json when present.',
                 boundary_mappings=[dict(source_row=row['source_row'], source_date=row['source_date'],
                                         source_hour=row['source_hour'], observed_at_utc=row['observed_at_utc'],
                                         provenance=row['provenance'])
                                    for row in document['records'] if row['source_hour'] == '24:00'])
    command = ['python', 'eval/normalize_malaga_weather.py', '--input', source_info['path'],
               '--expected-sha256', source_info['sha256']]
    if chart_rows is not None:
        command.extend(['--chart-html', html_info['path'], '--expected-html-sha256', html_info['sha256'],
                        '--chart-script', script_info['path'], '--expected-script-sha256', script_info['sha256']])
    audit['reproduction'] = dict(command=command, working_directory=str(root), network_required=False,
                                 output_directory=output_dir.relative_to(root).as_posix(),
                                 without_chart_evidence='Omit all four chart arguments to leave eight 24:00 timestamps unknown for the full source.')
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, data in outputs:
        path.write_bytes(data)
    (output_dir / 'audit.json').write_bytes(encoded(audit))
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--chart-html', type=Path)
    parser.add_argument('--expected-html-sha256')
    parser.add_argument('--chart-script', type=Path)
    parser.add_argument('--expected-script-sha256')
    args = parser.parse_args()
    try:
        audit = build_bundle(args.root, args.input, args.expected_sha256,
                             args.output_dir or args.root / '.runtime/malaga-weather-normalized-v1',
                             chart_html=args.chart_html, expected_html_sha256=args.expected_html_sha256,
                             chart_script=args.chart_script, expected_script_sha256=args.expected_script_sha256)
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        parser.exit(2, f'Normalization failed: {error}\n')
    print(json.dumps(dict(status=audit['status'], checks=audit['checks']), indent=2))


if __name__ == '__main__':
    main()
