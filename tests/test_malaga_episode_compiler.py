import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from eval.prepare_malaga_episode import canonical, compile_pack


def fixture_bundle(path):
    path.mkdir()
    (path / 'private').mkdir()
    (path / 'private/future.json').write_text(json.dumps(dict(
        extinguished='2021-10-24T19:00:00', population=71925, final_area=8651.26)))
    manifest = dict(version='wildfire-malaga-evidence-v1',
        incident_id='ES-MA-SIERRA-BERMEJA-2021', event_year=2021,
        actor_input_eligible=False, historical_replay_ready=False,
        products=[dict(layers=dict(
            areaOfInterestA=dict(bbox_crs84=[-5.263180459999944, 36.437454941000055,
                                          -5.065065475999972, 36.61232644100005]),
            observedEventA=dict(bbox_crs84=[0, 0, 90, 90]))) for _ in range(5)],
        egif_match=dict(clocks=dict(
            detection=dict(source_timestamp='2021-09-08T21:35:00'),
            ground_arrival=dict(source_timestamp='2021-09-08T21:50:00'))))
    (path / 'manifest.json').write_bytes(canonical(manifest))
    files = {file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
             for file in path.rglob('*') if file.is_file()}
    fingerprint = hashlib.sha256(canonical(files)).hexdigest()
    (path / 'audit.json').write_text(json.dumps(dict(content_sha256='not_trusted')))
    return fingerprint


class MalagaEpisodeCompilerTests(unittest.TestCase):
    def test_only_initial_allowlist_is_compiled_and_source_fingerprint_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = fixture_bundle(root / 'bundle')
            result = compile_pack(root / 'bundle', expected, root / 'episode.json')
            pack = json.loads((root / 'episode.json').read_text())
            self.assertEqual(pack['source_bundle_sha256'], expected)
            self.assertEqual(pack['assumed_ground_transit_minutes'], 8)
            self.assertEqual(pack['region_bbox_lon_lat'][0], -5.263180459999944)
            self.assertEqual((root / 'episode.json').stat().st_mode & 0o777, 0o600)
            for forbidden in ('2021-10-24', '8651.26', '71925', 'observedEventA'):
                self.assertNotIn(forbidden, json.dumps(pack))
            self.assertFalse(result['actor_input_eligible'])
            with self.assertRaises(ValueError):
                compile_pack(root / 'bundle', expected, root / 'episode.json')

    def test_future_evidence_changes_are_not_ignored_by_integrity_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = fixture_bundle(root / 'bundle')
            (root / 'bundle/private/future.json').write_text('{}')
            with self.assertRaises(ValueError):
                compile_pack(root / 'bundle', expected, root / 'rejected.json')
            self.assertFalse((root / 'rejected.json').exists())

    def test_compiler_cannot_write_into_evidence_or_trust_its_mutable_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = fixture_bundle(root / 'bundle')
            with self.assertRaises(ValueError):
                compile_pack(root / 'bundle', '0' * 64, root / 'wrong.json')
            with self.assertRaises(ValueError):
                compile_pack(root / 'bundle', expected, root / 'bundle/episode.json')
            (root / 'bundle/link').symlink_to(root / 'bundle/manifest.json')
            with self.assertRaises(ValueError):
                compile_pack(root / 'bundle', expected, root / 'linked.json')


if __name__ == '__main__':
    unittest.main()
