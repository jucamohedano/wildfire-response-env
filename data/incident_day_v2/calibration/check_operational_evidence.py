"""Bounded source-integrity and consistency checks; never grant calibration."""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
LEDGER = Path(__file__).with_name('operational-evidence-v1.json')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def assess(ledger, profile, provenance, station, subdaily, normalization, registry, roots):
    errors, warnings, coverage = [], [], []
    runtime = profile['runtime']
    source_ids = {source['id'] for source in ledger['sources']}
    for assumption in ledger['assumptions']:
        key = assumption['profile_key']
        if runtime.get(key) != assumption['value']:
            errors.append(f'profile drift: {key}')
        if assumption['status'] != 'unvalidated' or not set(assumption['evidence_ids']) <= source_ids:
            errors.append(f'invalid evidence status or source: {assumption["id"]}')
    if runtime.get('air_refill_minutes') != runtime.get('aircraft_refill_minutes'):
        warnings.append('legacy aircraft_refill_minutes alias differs; runtime and screen use air_refill_minutes')
    pack = provenance['real_source_pack']
    if pack['source_masked_pixels'] + pack['outside_source_coverage'] != pack['unknown_fuel_cells']:
        errors.append('fuel unknown-cell accounting differs')
    if pack['approval'] != 'blocked' or any(ledger['readiness'][key] for key in ('physical_calibration', 'real_source_pack_approved', 'expert_approval', 'actor_input_eligible')):
        errors.append('readiness gate unexpectedly changed; independent review required')
    if station['weather_data'][0]['observed_ranges']['daily_mean_wind_m_s'] != [0.525, 2.325]:
        errors.append('station daily wind summary differs from ledger')
    checks = normalization['checks']
    expected = {'source_rows': 384, 'normalized_rows': 384, 'unresolved_rows': 0, 'chart_value_matches': 384,
                'midnight_rows_resolved': 8, 'gap_count': 0, 'unique_normalized_timestamps': True,
                'complete_source_clock_grid': True, 'imputed_rows': 0}
    if subdaily['records'] != 384 or subdaily['ambiguous_24h_rows'] != 8 or any(checks.get(k) != v for k, v in expected.items()):
        errors.append('weather acquisition or normalization summary differs; re-review evidence')
    entry = next(item for item in registry['entries'] if item['id'] == 'weather-normalized')
    norm_path = ROOT / 'data/malaga_2021/weather_normalization_v1.json'
    if digest(norm_path) != entry['metadata']['sha256'] or entry['checks'] != checks:
        errors.append('normalization metadata differs from provenance registry')
    refs = [('daily', station['weather_data'][0]['path'], station['weather_data'][0]['sha256'])]
    refs += [(Path(item['path']).name, item['path'], item['sha256']) for item in normalization['input_files'] + normalization['output_files']]
    refs += [('siar_manual', '.runtime/malaga-station-weather/siar-user-manual.pdf', normalization['timezone_evidence']['sha256'])]
    loaded = {}
    for label, relative, expected_hash in refs:
        suffix = Path(relative).relative_to('.runtime')
        candidates = [root / suffix for root in roots]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            coverage.append({'file': label, 'status': 'missing'})
            warnings.append(f'evidence file unavailable: {label}')
        elif digest(path) != expected_hash:
            coverage.append({'file': label, 'status': 'hash_mismatch'})
            errors.append(f'evidence hash mismatch: {label}')
        else:
            coverage.append({'file': label, 'status': 'hash_verified', 'path': str(path)})
            loaded[label] = path
    weather_state = 'sidecar_resolved_raw_checks_incomplete'
    if 'weather.json' in loaded and 'chart_evidence.json' in loaded:
        weather = json.loads(loaded['weather.json'].read_text())['records']
        chart = json.loads(loaded['chart_evidence.json'].read_text())['records']
        try:
            times = [datetime.fromisoformat(row['observed_at_utc'].replace('Z', '+00:00')) for row in weather]
            fields = {'temperature_c': 'TempMedia', 'relative_humidity_percent': 'HumedadMedia',
                      'wind_speed_m_s': 'VelViento', 'wind_direction_degrees': 'DirViento', 'precipitation_mm': 'Precipitacion'}
            valid = (len(times) == len(chart) == 384 and len(set(times)) == 384
                     and all(b-a == timedelta(minutes=30) for a,b in zip(times, times[1:]))
                     and sum(row['source_hour'] == '24:00' for row in weather) == 8
                     and all(row['observed_at_utc'] == c['source_clock_utc'] and all(row[k] == c['measurements'][v] for k,v in fields.items())
                             for row,c in zip(weather,chart)))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            errors.append('normalized weather and chart rows disagree')
        else:
            weather_state = ('resolved_rows_verified_with_manual_and_provider_inputs' if all(x in loaded for x in ('siar_manual', 'siar-halfhourly-20210908-15.html', 'resultadosConsultaDatos.js', 'siar-halfhourly-20210908-15.json'))
                             else 'resolved_rows_verified_missing_provider_inputs')
    return {'status': 'inconsistent' if errors else 'consistent_but_not_calibrated', 'errors': errors,
            'warnings': warnings, 'file_coverage': coverage, 'weather_source_clock': weather_state,
            'physical_calibration': False, 'real_source_pack_approved': False,
            'limitations': ['Hashes verify bytes against stored digests, not authenticity or sensor quality',
                            'Interval semantics and historical receipt remain unknown; wind index mapping unvalidated']}


def egif_sample(path, expected_sha):
    if digest(path) != expected_sha:
        raise ValueError('EGIF archive hash mismatch')
    results = {'records_inspected': 0, 'method': 'first 100 XML records in source order; not a representative sample',
               'ground': [], 'air': [], 'missing_or_invalid': {'ground': 0, 'air': 0}, 'negative': {'ground': 0, 'air': 0}}
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith('.xml')]
        if len(names) != 1:
            raise ValueError('expected exactly one XML member')
        with archive.open(names[0]) as stream:
            for _, element in ET.iterparse(stream, events=['end']):
                if element.tag != 'Pif':
                    continue
                results['records_inspected'] += 1
                item = element.find('pif_tiempos')
                for name, key in (('ground', 'llegadapmt'), ('air', 'llegadapmae')):
                    try:
                        delta = (datetime.fromisoformat(item.findtext(key)) - datetime.fromisoformat(item.findtext('deteccion'))).total_seconds() / 60
                    except (TypeError, ValueError, AttributeError):
                        results['missing_or_invalid'][name] += 1
                    else:
                        if delta < 0:
                            results['negative'][name] += 1
                        else:
                            results[name].append(delta)
                element.clear()
                if results['records_inspected'] == 100:
                    break
    for name in ('ground', 'air'):
        values = results[name]
        results[name] = {'nonnegative_count': len(values), 'min_minutes': min(values) if values else None,
                         'median_minutes': statistics.median(values) if values else None,
                         'max_minutes': max(values) if values else None}
    results['limits'] = 'Recorded detection-to-arrival only; unknown timezones/quality and source ordering. Not setup, dispatch, duty, representative rates or actor receipt.'
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', action='append', type=Path, default=[], help='Runtime root containing malaga-station-weather or malaga-weather-normalized-v1; repeatable')
    parser.add_argument('--egif-archive', type=Path, help='Optional local EGIF 2021 zip; inspect first 100 records')
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text())
    result = assess(read(LEDGER), read(ROOT / 'data/incident_day_v2/profile.json'),
                    read(ROOT / 'data/incident_day_v2/provenance.json'), read(ROOT / 'data/malaga_2021/station_weather_search_v1.json'),
                    read(ROOT / 'data/malaga_2021/halfhour_weather_v1.json'), read(ROOT / 'data/malaga_2021/weather_normalization_v1.json'),
                    read(ROOT / 'data/malaga_2021/provenance.json'), args.runtime or [ROOT / '.runtime'])
    if args.egif_archive:
        try:
            audit = read(ROOT / 'data/operational/source_audit_v1.json')
            result['egif_sample'] = egif_sample(args.egif_archive, audit['sources']['egif']['archive_sha256'])
        except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
            result['errors'].append(f'EGIF sample failed: {exc}')
            result['status'] = 'inconsistent'
    print(json.dumps(result, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
