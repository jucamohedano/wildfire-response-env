import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from eval.stress_storage import RunStore, TRANSITION_VERSION, VERSION


def fixture():
    initial = dict(landscape=dict(fuel_classes=[[1, 2]], study_mask=[[True, False]],
                                  mapping={'1': {'heat': 0.25}}, basis='Synthetic terrain'),
                   minute=0, resources=[{'id': 'unit-1', 'status': 'available'}],
                   reports=[], topographic_status={'boundary_censored': False},
                   weather_context={'records': [{'wind': [1.0, 0.0]}]})
    first = copy.deepcopy(initial)
    first.update(minute=2, reports=[{'id': 'report-1', 'received_at': 2}])
    last = copy.deepcopy(first)
    last.update(minute=4, sensor_memory={'cells': [[0, 1, 0.5]]})
    trajectory = dict(interface_version='ops-v1', reward_version='objective-v1',
                      task_id='exercise-01', seed=7, ticks_per_action=2,
                      objectives=[{'id': 'arrival'}], initial_observation=initial,
                      transitions=[dict(action={'requests': []}, observation=first, action_valid=True,
                                        objective_reward=0.25, terminated=False, truncated=False),
                                   dict(action={}, observation=last, action_valid=True,
                                        objective_reward=0.75, terminated=True, truncated=False)])
    verification = dict(passed=True, suitable_for_training=True, boundary_censored=False,
                        objective_score=1.0, evaluator_evidence={'synthetic_private_marker': [1, 2]})
    return trajectory, verification


class StressStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'run'
        self.store = RunStore(self.root)
        self.trajectory, self.verification = fixture()

    def write(self, **overrides):
        arguments = dict(case_id='case-01', policy='control', trajectory=self.trajectory,
                         verification=self.verification, split='train', group_id='group-01')
        arguments.update(overrides)
        return self.store.write_episode(**arguments)

    def read_json(self, relative):
        return json.loads(gzip.decompress((self.root / relative).read_bytes()))

    def read_records(self, record):
        payload = gzip.decompress((self.root / record['transitions_path']).read_bytes())
        return [json.loads(line) for line in payload.splitlines()]

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_fresh_root_and_required_directories(self):
        for name in ('public/trajectories', 'public/transitions', 'shared/maps', 'private/evaluator'):
            self.assertTrue((self.root / name).is_dir())
        with self.assertRaises(ValueError):
            RunStore(self.root)
        existing_file = Path(self.directory.name) / 'file'
        existing_file.write_text('untouched')
        with self.assertRaises(ValueError):
            RunStore(existing_file)
        link = Path(self.directory.name) / 'link'
        link.symlink_to(Path(self.directory.name) / 'missing')
        with self.assertRaises(ValueError):
            RunStore(link)

    def test_roundtrip_byte_hashes_and_manifest_metadata(self):
        record = self.write()
        self.assertEqual(self.read_json(record['trajectory_path']), self.trajectory)
        self.assertEqual(self.read_json(record['verification_path']), self.verification)
        self.assertEqual(json.loads(json.dumps(record, allow_nan=False)), record)
        self.assertEqual(record['version'], VERSION)
        self.assertEqual(record['reward'], 1.0)
        self.assertEqual(record['decisions'], 2)
        for key in ('interface_version', 'reward_version', 'task_id', 'seed'):
            self.assertEqual(record[key], self.trajectory[key])
        self.assertEqual(record['group_id'], 'group-01')
        self.assertEqual(record['split'], 'train')
        self.assertTrue(record['training_eligible'])
        for name in ('trajectory', 'transitions', 'verification', 'landscape'):
            payload = (self.root / record[name + '_path']).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), record[name + '_sha256'])
            self.assertEqual(len(payload), record[name + '_bytes'])
            if name != 'landscape':
                self.assertEqual(payload[4:8], bytes(4))
        landscape = self.root / record['landscape_path']
        self.assertEqual(landscape.stem, record['landscape_sha256'])

    def test_compact_observations_and_transitions_are_lossless(self):
        record = self.write()
        records = self.read_records(record)
        self.assertEqual([item['type'] for item in records], ['reset', 'step', 'step'])
        self.assertEqual([item['step_index'] for item in records[1:]], [1, 2])
        original = [self.trajectory['initial_observation']]
        original.extend(step['observation'] for step in self.trajectory['transitions'])
        for item, observation in zip(records, original):
            self.assertEqual(item['version'], TRANSITION_VERSION)
            compact = item['observation']
            self.assertNotIn('landscape', compact)
            reference = compact.pop('landscape_ref')
            self.assertFalse(Path(reference).is_absolute())
            path = (self.root / record['transitions_path']).parent / reference
            self.assertEqual(path.resolve(), (self.root / record['landscape_path']).resolve())
            compact['landscape'] = json.loads(path.read_bytes())
            self.assertEqual(compact, observation)
        for compact, transition in zip(records[1:], self.trajectory['transitions']):
            reconstructed = {key: value for key, value in compact.items()
                             if key not in ('version', 'type', 'step_index', 'training_eligible')}
            self.assertEqual(reconstructed, transition)
        header = records[0]['episode']
        self.assertEqual(header['trajectory_metadata'], record['trajectory_metadata'])
        self.assertEqual(header['group_id'], 'group-01')
        self.assertEqual(header['trajectory_metadata']['seed'], 7)
        for observation in original:
            self.assertNotIn('episode', observation)
            self.assertNotIn('group_id', observation)
            self.assertNotIn('training_eligible', observation)

    def test_copy_isolation_for_inputs_returned_metadata_and_saved_bytes(self):
        original = copy.deepcopy((self.trajectory, self.verification))
        record = self.write()
        saved = self.files()
        self.assertEqual((self.trajectory, self.verification), original)
        record['trajectory_metadata']['objectives'][0]['id'] = 'changed-return'
        self.assertEqual((self.trajectory, self.verification), original)
        self.trajectory['initial_observation']['landscape']['fuel_classes'][0][0] = 9
        self.trajectory['transitions'][0]['action']['requests'].append({'id': 'changed-input'})
        self.verification['evaluator_evidence']['synthetic_private_marker'].append(3)
        self.assertEqual(self.files(), saved)
        self.assertEqual(self.read_json(record['trajectory_path']), original[0])
        self.assertEqual(self.read_json(record['verification_path']), original[1])

    def test_shared_maps_deduplicate_full_content_and_gzip_is_deterministic(self):
        first = self.write()
        map_path = self.root / first['landscape_path']
        timestamp = map_path.stat().st_mtime_ns
        second = self.write(case_id='case-02', policy='passive')
        self.assertEqual(first['landscape_path'], second['landscape_path'])
        self.assertEqual(len(list((self.root / 'shared/maps').iterdir())), 1)
        self.assertEqual(map_path.stat().st_mtime_ns, timestamp)
        self.assertEqual(json.loads(map_path.read_bytes()), self.trajectory['initial_observation']['landscape'])
        other = RunStore(Path(self.directory.name) / 'other')
        repeated = other.write_episode('case-01', 'control', self.trajectory, self.verification,
                                       split='train', group_id='group-01')
        self.assertEqual(first, repeated)
        for name in ('trajectory', 'transitions', 'verification'):
            self.assertEqual((self.root / first[name + '_path']).read_bytes(),
                             (other.root / repeated[name + '_path']).read_bytes())

    def test_private_verification_is_separate_from_all_public_artifacts(self):
        record = self.write()
        self.assertTrue(record['verification_path'].startswith('private/evaluator/'))
        for relative, payload in self.files().items():
            if relative.startswith('private/'):
                continue
            if relative.endswith('.gz'):
                payload = gzip.decompress(payload)
            for marker in (b'evaluator_evidence', b'synthetic_private_marker', b'objective_score'):
                self.assertNotIn(marker, payload)
        self.assertNotIn('evaluator_evidence', record)
        self.assertEqual(len(self.read_records(record)), 3)

    def test_missing_false_or_nonboolean_verification_is_rejected_before_writes(self):
        for verification in (None, {}, {'passed': False, 'suitable_for_training': True},
                             {'passed': 1, 'suitable_for_training': True}, {'passed': True},
                             {'passed': True, 'suitable_for_training': None},
                             {'passed': True, 'suitable_for_training': 1}):
            with self.subTest(verification=verification), self.assertRaises(ValueError):
                self.write(verification=verification)
            self.assertEqual(self.files(), {})

    def test_nonfinite_values_are_rejected_anywhere_before_writes(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            for location in ('reward', 'observation', 'landscape', 'verification'):
                trajectory, verification = fixture()
                if location == 'reward':
                    trajectory['transitions'][0]['objective_reward'] = value
                elif location == 'observation':
                    trajectory['transitions'][1]['observation']['reports'].append({'value': value})
                elif location == 'landscape':
                    trajectory['initial_observation']['landscape']['bad'] = value
                else:
                    verification['evaluator_evidence']['bad'] = value
                with self.subTest(value=value, location=location), self.assertRaises(ValueError):
                    self.write(trajectory=trajectory, verification=verification)
                self.assertEqual(self.files(), {})
        self.trajectory['transitions'][0]['objective_reward'] = 1e308
        self.trajectory['transitions'][1]['objective_reward'] = 1e308
        with self.assertRaises(ValueError):
            self.write()
        self.assertEqual(self.files(), {})

    def test_bad_labels_cannot_escape_or_alias_episode_paths(self):
        bad = ('', '.', '..', '../escape', 'case/child', '/absolute', 'case\\child',
               'white space', 'case.json', 'café', 'line\nbreak', 'null\0byte', 'a' * 129, None, 7)
        for name in ('case_id', 'policy', 'split', 'group_id'):
            for value in bad:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.write(**{name: value})
                self.assertEqual(self.files(), {})

    def test_duplicate_and_partial_episode_artifacts_are_never_overwritten(self):
        record = self.write()
        saved = self.files()
        for overrides in ({}, {'split': 'test'}, {'group_id': 'other'}):
            with self.assertRaises(ValueError):
                self.write(**overrides)
            self.assertEqual(self.files(), saved)
        for name in ('trajectory', 'transitions', 'verification'):
            with self.subTest(name=name):
                for path in self.root.rglob('*.gz'):
                    path.unlink()
                (self.root / record[name + '_path']).write_bytes(b'existing')
                before = self.files()
                with self.assertRaises(ValueError):
                    self.write()
                self.assertEqual(self.files(), before)

    def test_held_out_episodes_are_preserved_but_never_training_eligible(self):
        for split in ('validation', 'test'):
            with self.subTest(split=split):
                record = self.write(case_id='case-' + split, split=split)
                self.assertEqual(record['split'], split)
                self.assertTrue(record['suitable_for_training'])
                self.assertFalse(record['training_eligible'])
                records = self.read_records(record)
                self.assertEqual(len(records), 3)
                self.assertTrue(all(item['training_eligible'] is False for item in records))
                self.assertFalse(records[0]['episode']['training_eligible'])
                self.assertEqual(self.read_json(record['trajectory_path']), self.trajectory)
                self.assertEqual(self.read_json(record['verification_path']), self.verification)

    def test_unknown_splits_are_rejected_before_writes(self):
        for split in ('development', 'held_out', 'Train'):
            with self.subTest(split=split), self.assertRaises(ValueError):
                self.write(split=split)
            self.assertEqual(self.files(), {})

    def test_censored_episodes_are_retained_with_explicit_ineligible_flags(self):
        self.verification.update(boundary_censored=True, suitable_for_training=False)
        self.trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored'] = True
        self.trajectory['transitions'][-1].update(terminated=False, truncated=True)
        record = self.write()
        self.assertFalse(record['training_eligible'])
        self.assertFalse(record['suitable_for_training'])
        self.assertTrue(record['boundary_censored'])
        self.assertTrue(record['truncated'])
        records = self.read_records(record)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(item['training_eligible'] is False for item in records))
        self.assertFalse(records[0]['episode']['training_eligible'])
        self.assertEqual(self.read_json(record['trajectory_path']), self.trajectory)

    def test_public_censoring_overrides_an_inconsistent_training_verdict(self):
        self.trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored'] = True
        record = self.write()
        self.assertFalse(record['training_eligible'])
        self.assertTrue(record['boundary_censored'])
        self.assertTrue(record['suitable_for_training'])

    def test_false_training_verdict_without_censoring_is_also_retained(self):
        self.verification['suitable_for_training'] = False
        record = self.write()
        self.assertFalse(record['training_eligible'])
        self.assertFalse(record['boundary_censored'])
        self.assertEqual(len(self.read_records(record)), 3)

    def test_dynamic_missing_or_colliding_landscapes_are_rejected(self):
        for change in ('dynamic', 'missing', 'ref_collision', 'type_change'):
            trajectory, _ = fixture()
            observation = trajectory['transitions'][0]['observation']
            if change == 'dynamic':
                observation['landscape']['study_mask'][0][0] = False
            elif change == 'missing':
                del observation['landscape']
            elif change == 'type_change':
                observation['landscape']['mapping']['1']['heat'] = 1
            else:
                observation['landscape_ref'] = 'already-present'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.write(trajectory=trajectory)
            self.assertEqual(self.files(), {})

    def test_malformed_public_contract_is_rejected_before_writes(self):
        mutations = (
            lambda trace: trace.pop('initial_observation'),
            lambda trace: trace.pop('interface_version'),
            lambda trace: trace.update(seed=True),
            lambda trace: trace.update(transitions=[]),
            lambda trace: trace['transitions'][0].pop('action'),
            lambda trace: trace['transitions'][0].update(terminated=True),
            lambda trace: trace['transitions'][-1].update(terminated=False),
            lambda trace: trace['transitions'][0].update(objective_reward=True),
            lambda trace: trace['transitions'][0].update(truncated=0),
            lambda trace: trace['transitions'][0].update(type='conflict'),
            lambda trace: trace['initial_observation']['landscape'].update({1: 'non-json-key'}),
            lambda trace: trace['initial_observation'].update(reports=('non-json-tuple',)),
        )
        for mutation in mutations:
            trajectory, _ = fixture()
            mutation(trajectory)
            with self.assertRaises(ValueError):
                self.write(trajectory=trajectory)
            self.assertEqual(self.files(), {})

    def test_existing_corrupt_or_symlinked_maps_are_rejected(self):
        record = self.write()
        path = self.root / record['landscape_path']
        path.write_bytes(b'corrupt')
        saved = self.files()
        with self.assertRaises(ValueError):
            self.write(case_id='case-02')
        self.assertEqual(self.files(), saved)
        path.unlink()
        outside = Path(self.directory.name) / 'outside'
        outside.write_bytes(b'untouched')
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.write(case_id='case-02')
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_symlinked_episode_directory_is_rejected(self):
        outside = Path(self.directory.name) / 'outside'
        outside.mkdir()
        (self.root / 'public/transitions/case-01').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.write()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.files(), {})


if __name__ == '__main__':
    unittest.main()
