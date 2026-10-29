import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agentenv_roundtrip', ROOT / 'eval/check_agentenv_dataset_roundtrip.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DatasetRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.trace = {'initial_observation': {'minute': 0}, 'transitions': [
            {'observation': {'minute': 2}, 'action': {'advance': True}, 'objective_reward': 0.5,
             'action_valid': True, 'terminated': False, 'truncated': False},
            {'observation': {'minute': 4}, 'action': {}, 'objective_reward': -1,
             'action_valid': True, 'terminated': True, 'truncated': False}]}
        self.rows = [dict(step=i, minute=2*i, next_minute=2*i+2,
                          observation=json.dumps({'minute': 2*i}), action=json.dumps(t['action']),
                          next_observation=json.dumps(t['observation']), reward=t['objective_reward'],
                          action_valid=t['action_valid'], terminated=t['terminated'], truncated=t['truncated'])
                     for i, t in enumerate(self.trace['transitions'])]

    def test_sorted_full_trace_roundtrip(self):
        self.assertEqual(checker.compare_transitions(self.trace, self.rows[::-1]), 2)

    def test_detects_changed_action_reward_and_terminal(self):
        for key, value in [('action', '{}'), ('reward', 0), ('terminated', True)]:
            rows = copy.deepcopy(self.rows)
            rows[0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                checker.compare_transitions(self.trace, rows)

    def test_rejects_missing_or_duplicate_step(self):
        with self.assertRaisesRegex(ValueError, 'count'):
            checker.compare_transitions(self.trace, self.rows[:1])
        rows = copy.deepcopy(self.rows)
        rows[1]['step'] = 0
        with self.assertRaisesRegex(ValueError, 'step'):
            checker.compare_transitions(self.trace, rows)

class FrameworkLinkTests(unittest.TestCase):
    def test_header_diagnostics_distinguish_list_order_and_content(self):
        spec = importlib.util.spec_from_file_location('native_header_order', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        a = {'groups': [{'id': 'a'}, {'id': 'b'}]}
        b = {'groups': [{'id': 'b'}, {'id': 'a'}]}
        c = {'groups': [{'id': 'b'}, {'id': 'c'}]}
        self.assertEqual(native.classify_header_value(a, a)['classification'], 'identical')
        self.assertEqual(native.classify_header_value(a, b)['classification'], 'equal_only_ignoring_nested_list_order')
        self.assertEqual(native.classify_header_value(a, c)['classification'], 'content_differs_even_ignoring_list_order')

    def test_failure_report_shows_stage_and_static_error_only(self):
        spec = importlib.util.spec_from_file_location('native_fixture_failure', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        try:
            native.verify_loaded_trace({'task': 'x'}, 'incorrect')
        except native.ValidationError as exc:
            report = native.failure_report(exc, {'stage': 'extension_load'})
        self.assertEqual(report['stage'], 'extension_load')
        self.assertEqual(report['error'], 'loaded trace hash differs from framework-recorded trace')
        self.assertIsInstance(report['checker_line'], int)
        try:
            raise ValueError('sensitive external content')
        except ValueError as exc:
            report = native.failure_report(exc, {'stage': 'host_replay'})
        self.assertNotIn('sensitive external content', json.dumps(report))
        self.assertEqual(report['stage'], 'host_replay')

    def test_real_framework_store_matches_plugin_publisher(self):
        import os
        import subprocess
        import sys
        import tempfile
        program = r"""
import importlib.util, json, sys
from pathlib import Path
from agentenv_hf import runs, records, dataset
from agent_env.store.routing import namespace_routing
from agent_env.store.document_store import Filter, UpdateSpec
from agent_env.task.store import get_task_instance_store
root = Path(sys.argv[1]) / 'bundle'
(root / 'tasks').mkdir(parents=True)
(root / 'tasks/smoke.json').write_text('[]')
bundle = runs.locate(str(root))
task = runs.tasks(bundle)[0]
with namespace_routing():
    store = get_task_instance_store()
    instance = store.create_instance(task.id, 1, 3)
    store._doc_store.update('task_instances', Filter.of(instance_id=instance.instance_id),
        UpdateSpec(set={'status': 'completed',
            'context': {'metadata': {'script_results': {'drive': {'model_used': False}}}},
            'completed_steps': [{'step_id': step, 'status': 'success'} for step in ('deploy', 'drive', 'verify')]}))
    built = dataset.build(bundle, name='proof', split='development', which='latest')
    raw = json.loads(built.files['raw/proof.jsonl'])
    import io, pyarrow.parquet as pq
    native_task = pq.read_table(io.BytesIO(built.files['tasks/proof.parquet'])).to_pylist()[0]
    native_episode = pq.read_table(io.BytesIO(built.files['episodes/proof.parquet'])).to_pylist()[0]
    assert native_task['task'] == 'smoke' and native_task['task_id'] == 'proof/smoke'
    instances, unfinished = runs.instances(bundle, 'latest')
    assert built.runs == 1 and not unfinished
    spec = importlib.util.spec_from_file_location('native', Path(sys.argv[2]) / 'eval/check_agentenv_native_fixture.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    match, result = native.choose_run(instances, raw['episode_id'], records.Rewrite(bundle.id_root, 'proof'),
                                      records.record, raw['record'])
    assert match['instance_id'] == instance.instance_id and result['model_used'] is False
    native.verify_task_binding(native_task, native_episode, raw['record'], task,
                               records.Rewrite(bundle.id_root, 'proof'), 'smoke', 'smoke')
    try:
        native.verify_task_binding(native_task, native_episode, raw['record'], task,
                                   records.Rewrite(bundle.id_root, 'proof'), 'other', 'other')
    except ValueError:
        pass
    else:
        raise AssertionError('mismatched trace task was accepted')
outside, _ = runs.instances(bundle, 'latest')
assert outside == []
"""
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['XDG_STATE_HOME'] = str(Path(tmp) / 'state')
            env['XDG_CACHE_HOME'] = str(Path(tmp) / 'cache')
            env['HF_HOME'] = str(Path(tmp) / 'hf')
            result = subprocess.run([sys.executable, '-c', program, tmp, str(ROOT)], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])

    def test_valid_shape_trace_hash_drift_is_rejected(self):
        import hashlib
        spec = importlib.util.spec_from_file_location('native_fixture_hash', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        trace = {'task_id': 'dev', 'transitions': [{'action': {}, 'objective_reward': 0.5}]}
        expected = hashlib.sha256(native.canonical(trace)).hexdigest()
        native.verify_loaded_trace(trace, expected)
        changed = copy.deepcopy(trace)
        changed['transitions'][0]['action'] = {'advance': True}
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            native.verify_loaded_trace(changed, expected)

    def test_native_reward_is_the_replayed_score(self):
        spec = importlib.util.spec_from_file_location('native_fixture_reward', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        native.verify_reward(0.42, 0.42 + 1e-13, 0.42)
        with self.assertRaisesRegex(ValueError, 'differs'):
            native.verify_reward(1.0, 0.42, 0.42)
        with self.assertRaisesRegex(ValueError, 'finite'):
            native.verify_reward(float('nan'), 0.42, 0.42)

    def test_requires_exact_completed_native_episode_and_driver(self):
        from agentenv_hf import records, tables
        spec = importlib.util.spec_from_file_location('native_fixture', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        root = '@local/tmp/fixture'
        task_id = root + '/bench-dev-123'
        instance_id = task_id + '-a1b2c3d4'
        good = {'instance_id': instance_id, 'task_id': task_id, 'task_version': 1, 'status': 'completed',
                'completed_steps': [{'step_id': name, 'status': 'success'} for name in ('deploy', 'drive', 'verify')],
                'context': {'metadata': {'script_results': {'drive': {'model_used': False}}}}}
        rewrite = records.Rewrite(root, 'wildfire-ci')
        published = rewrite(records.record(good))
        episode = tables.episode_row('bench-dev-123', [], published, [], None)['episode_id']
        self.assertEqual(native.choose_run([good], episode, rewrite, records.record, published)[0], good)
        extra_metadata = copy.deepcopy(published)
        extra_metadata['publisher_metadata'] = {'note': 'not an identity field'}
        self.assertEqual(native.choose_run([good], episode, rewrite, records.record, extra_metadata)[0], good)
        for instances, identifier in [([], episode), ([good, good], episode), ([good], episode + '-wrong')]:
            with self.subTest(instances=instances, episode=identifier), self.assertRaises(native.NativeLinkError) as failure:
                native.choose_run(instances, identifier, rewrite, records.record, published)
            self.assertEqual(failure.exception.diagnostics['expected_native_id'], identifier)
            self.assertNotIn('context', json.dumps(failure.exception.diagnostics))
        other = copy.deepcopy(good)
        other['instance_id'] = task_id + '-a1b2c3d5'
        with self.assertRaises(native.NativeLinkError):
            native.choose_run([other], episode, rewrite, records.record, published)
        changed = copy.deepcopy(good)
        changed['task_version'] = 2
        with self.assertRaises(native.NativeLinkError):
            native.choose_run([changed], episode, rewrite, records.record, published)
        missing = copy.deepcopy(good)
        del missing['context']['metadata']['script_results']
        with self.assertRaisesRegex(ValueError, 'driver result'):
            native.choose_run([missing], episode, rewrite, records.record, published)


if __name__ == '__main__':
    unittest.main()
