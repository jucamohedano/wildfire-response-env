import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / 'data/incident_day_v2/calibration'
spec = importlib.util.spec_from_file_location('operational_evidence', CALIBRATION / 'check_operational_evidence.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def load(path):
    return json.loads((ROOT / path).read_text())


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = load('data/incident_day_v2/calibration/operational-evidence-v1.json')
        self.profile = load('data/incident_day_v2/profile.json')
        self.provenance = load('data/incident_day_v2/provenance.json')
        self.station = load('data/malaga_2021/station_weather_search_v1.json')
        self.subdaily = load('data/malaga_2021/halfhour_weather_v1.json')
        self.normal = load('data/malaga_2021/weather_normalization_v1.json')
        self.registry = load('data/malaga_2021/provenance.json')

    def evaluate(self, root):
        return checker.assess(self.ledger, self.profile, self.provenance, self.station, self.subdaily,
                              self.normal, self.registry, [Path(root)])

    def test_missing_raw_preserves_resolved_metadata_without_approval(self):
        with tempfile.TemporaryDirectory() as root:
            result = self.evaluate(root)
        self.assertEqual(result['status'], 'consistent_but_not_calibrated')
        self.assertEqual(result['weather_source_clock'], 'sidecar_resolved_raw_checks_incomplete')
        self.assertEqual(len(result['warnings']), 7)
        self.assertFalse(result['physical_calibration'])

    def test_detects_aircraft_refill_divergence(self):
        self.profile['runtime']['aircraft_refill_minutes'] = 7
        with tempfile.TemporaryDirectory() as root:
            result = self.evaluate(root)
        self.assertFalse(result['errors'])
        self.assertTrue(any('runtime and screen use air_refill_minutes' in item for item in result['warnings']))

    def test_detects_profile_drift_and_false_approval(self):
        self.profile['runtime']['aircraft_max_wind'] = 3
        self.ledger['readiness']['physical_calibration'] = True
        with tempfile.TemporaryDirectory() as root:
            result = self.evaluate(root)
        self.assertIn('profile drift: aircraft_max_wind', result['errors'])
        self.assertTrue(any('readiness' in error for error in result['errors']))

    def test_hash_match_and_mismatch(self):
        record = self.station['weather_data'][0]
        record['sha256'] = hashlib.sha256(b'evidence').hexdigest()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / record['path'].removeprefix('.runtime/')
            path.parent.mkdir(parents=True)
            path.write_bytes(b'evidence')
            self.assertEqual(self.evaluate(root)['file_coverage'][0]['status'], 'hash_verified')
            path.write_bytes(b'tampered')
            self.assertIn('evidence hash mismatch: daily', self.evaluate(root)['errors'])

    def test_normalization_and_fuel_inconsistencies_detected(self):
        self.normal['checks']['gap_count'] = 1
        self.provenance['real_source_pack']['unknown_fuel_cells'] = 79
        with tempfile.TemporaryDirectory() as root:
            result = self.evaluate(root)
        self.assertTrue(any('normalization summary' in error for error in result['errors']))
        self.assertIn('fuel unknown-cell accounting differs', result['errors'])

    def test_weather_chart_rows_and_gaps_are_checked(self):
        from datetime import datetime, timedelta, timezone
        rows, chart = [], []
        for i in range(384):
            timestamp = (datetime(2021, 9, 8, 0, 30, tzinfo=timezone.utc) + timedelta(minutes=30*i)).isoformat().replace('+00:00', 'Z')
            rows.append({'observed_at_utc': timestamp, 'source_hour': '24:00' if i % 48 == 47 else '0:30',
                         'temperature_c': 20, 'relative_humidity_percent': 50, 'wind_speed_m_s': 1,
                         'wind_direction_degrees': 30, 'precipitation_mm': 0})
            chart.append({'source_clock_utc': timestamp, 'measurements': {'TempMedia': 20, 'HumedadMedia': 50,
                          'VelViento': 1, 'DirViento': 30, 'Precipitacion': 0}})
        with tempfile.TemporaryDirectory() as root:
            for name, data in [('weather.json', {'records': rows}), ('chart_evidence.json', {'records': chart})]:
                item = next(x for x in self.normal['output_files'] if x['path'].endswith(name))
                path = Path(root) / item['path'].removeprefix('.runtime/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
                item['sha256'] = checker.digest(path)
            self.assertEqual(self.evaluate(root)['weather_source_clock'], 'resolved_rows_verified_missing_provider_inputs')
            rows[1]['observed_at_utc'] = rows[0]['observed_at_utc']
            path = Path(root) / 'malaga-weather-normalized-v1/weather.json'
            path.write_text(json.dumps({'records': rows}))
            next(x for x in self.normal['output_files'] if x['path'].endswith('weather.json'))['sha256'] = checker.digest(path)
            self.assertIn('normalized weather and chart rows disagree', self.evaluate(root)['errors'])

    def test_egif_bounded_sample_and_digest(self):
        import zipfile
        xml = b'<pifs><Pif><pif_tiempos><deteccion>2021-01-01T12:00:00</deteccion><llegadapmt>2021-01-01T12:10:00</llegadapmt><llegadapmae>2021-01-01T11:59:00</llegadapmae></pif_tiempos></Pif></pifs>'
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'sample.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('data.xml', xml)
            result = checker.egif_sample(path, checker.digest(path))
            self.assertEqual(result['ground']['median_minutes'], 10)
            self.assertEqual(result['negative']['air'], 1)
            with self.assertRaises(ValueError):
                checker.egif_sample(path, '0' * 64)


if __name__ == '__main__':
    unittest.main()
