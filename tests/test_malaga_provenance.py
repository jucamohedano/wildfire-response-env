import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from eval.build_malaga_provenance import (
    PROFILE_VERSION, artifact, build_document, encoded, fingerprint, metadata,
    validate_document,
)


def profile_fixture():
    profile = dict(version=PROFILE_VERSION, evidence_class='synthetic', historical_truth=False,
                   source_code=[])
    profile['sha256'] = fingerprint(profile)
    return profile


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.profile = profile_fixture()

    def document(self):
        return build_document(self.root, '2026-10-25', self.profile)

    def test_deterministic_document_and_classifications(self):
        document = self.document()
        self.assertEqual(encoded(document), encoded(self.document()))
        self.assertEqual(document['sha256'], fingerprint(document))
        entries = {entry['id']: entry for entry in document['entries']}
        self.assertEqual(len(entries), 10)
        self.assertEqual(entries['weather-measured']['evidence_class'], 'measured')
        self.assertEqual(entries['weather-normalized']['evidence_class'], 'derived')
        self.assertEqual(entries['generated-exercise-inputs']['evidence_class'], 'synthetic')
        self.assertEqual(entries['episode-configuration']['evidence_class'], 'mixed')
        self.assertFalse(document['readiness']['historical_replay_ready'])
        self.assertTrue(all(entry['actor_input_eligible'] is False for entry in entries.values()))

    def test_profile_missing_is_not_reported_as_cached(self):
        entry = next(entry for entry in self.document()['entries'] if entry['id'] == 'generated-exercise-inputs')
        self.assertEqual(entry['metadata']['integrity'], 'declared_hash_only_not_cached')
        self.assertEqual(entry['metadata']['sha256'], hashlib.sha256(encoded(self.profile)).hexdigest())

    def test_cached_profile_must_match_generated_bytes(self):
        path = self.root / 'data/malaga_2021/synthetic_runtime_inputs_v1.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(encoded(self.profile))
        document = self.document()
        entry = next(entry for entry in document['entries'] if entry['id'] == 'generated-exercise-inputs')
        self.assertEqual(entry['metadata']['integrity'], 'pinned_match')
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'pinned metadata'):
            self.document()

    def test_invalid_profile_and_snapshot_rejected(self):
        for key, value in [('historical_truth', True), ('evidence_class', 'measured'), ('sha256', '0' * 64)]:
            profile = {**self.profile, key: value}
            with self.assertRaises(ValueError):
                build_document(self.root, '2026-10-25', profile)
        with self.assertRaises(ValueError):
            build_document(self.root, 'not-a-date', self.profile)

    def test_artifact_hash_and_pins(self):
        path = self.root / 'source.bin'
        path.write_bytes(b'official fixture bytes')
        result = artifact(self.root, 'source.bin')
        self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result['integrity'], 'locally_hashed_not_source_authenticated')
        self.assertEqual(artifact(self.root, 'source.bin', result)['integrity'], 'pinned_match')
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'pinned metadata'):
            artifact(self.root, 'source.bin', result)

    def test_no_path_escape_or_symlink(self):
        for path in ('../escape.json', '/tmp/escape.json'):
            with self.assertRaises(ValueError):
                artifact(self.root, path)
        (self.root / 'link').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            artifact(self.root, 'link/absent.json')

    def test_metadata_rejects_duplicate_keys(self):
        (self.root / 'metadata.json').write_text('{"clock": 1, "clock": 2}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            metadata(self.root, 'metadata.json')

    def test_tampered_digest_rejected(self):
        document = self.document()
        document['snapshot_date'] = '2026-01-01'
        with self.assertRaises(ValueError):
            validate_document(document)

    def test_privacy_runtime_and_lineage_guards(self):
        original = self.document()
        mutations = [
            lambda document: document['entries'][0].update(actor_input_eligible=True),
            lambda document: document['entries'][0].update(used_by_current_runtime=True),
            lambda document: document['entries'][0].update(evidence_class='historical_ground_truth'),
            lambda document: document['entries'][0].update(id=document['entries'][1]['id']),
            lambda document: document['entries'][-1].update(field_lineage={}),
            lambda document: document['readiness'].update(historical_replay_ready=True),
            lambda document: document['lineage'][0].update(inputs=['missing']),
            lambda document: document['entries'][0].update(source_urls=['https://user:password@example.com/']),
            lambda document: document['entries'][0]['metadata'].update(path='../escape'),
        ]
        for mutate in mutations:
            document = copy.deepcopy(original)
            mutate(document)
            document['sha256'] = fingerprint(document)
            with self.assertRaises(ValueError):
                validate_document(document)

    def test_repository_snapshot_and_profile_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        document_path = root / 'data/malaga_2021/provenance.json'
        profile_path = root / 'data/malaga_2021/synthetic_runtime_inputs_v1.json'
        document = json.loads(document_path.read_bytes())
        profile = json.loads(profile_path.read_bytes())
        validate_document(document)
        self.assertEqual(profile['sha256'], fingerprint(profile))
        self.assertEqual(document['synthetic_profile_sha256'], profile['sha256'])
        self.assertFalse(profile['fuel']['physical_moisture_model_implemented'])
        self.assertIsNone(profile['fuel']['physical_moisture_percent'])
        for entry in document['entries']:
            records = [entry['metadata'], *entry['raw_artifacts'], *entry['derived_artifacts'],
                       *entry['transformations']]
            for record in records:
                path = root / record['path']
                if path.is_file():
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'], record['path'])
        self.assertEqual(hashlib.sha256((root / document['generator']['path']).read_bytes()).hexdigest(),
                         document['generator']['sha256'])


if __name__ == '__main__':
    unittest.main()
