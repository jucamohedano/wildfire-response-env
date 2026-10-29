"""Link a framework-recorded fixture trace to its native episode and replay it."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class NativeLinkError(ValueError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def safe_id(value):
    if not isinstance(value, str):
        return '<missing>'
    allowed = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@/._-~')
    if len(value) > 512 or any(char not in allowed for char in value):
        return '<redacted-invalid-id>'
    return value


def link_diagnostics(instances, episode_id, rewrite, record_builder):
    return {'expected_native_id': safe_id(episode_id),
                   'candidates': [{'instance_id': safe_id(instance.get('instance_id')),
                                   'rewritten_id': safe_id(rewrite(record_builder(instance)).get('instance_id')),
                                   'status': safe_id(instance.get('status'))}
                                  for instance in instances[:20]],
                   'candidate_count': len(instances)}


def choose_run(instances, episode_id, rewrite, record_builder, published_record):
    diagnostics = link_diagnostics(instances, episode_id, rewrite, record_builder)
    matches = [instance for instance in instances
               if rewrite(record_builder(instance)).get('instance_id') == episode_id
               and rewrite(record_builder(instance)).get('task_id') == published_record.get('task_id')
               and rewrite(record_builder(instance)).get('task_version') == published_record.get('task_version')]
    if len(matches) != 1 or matches[0]['status'] != 'completed':
        raise NativeLinkError('native episode must match exactly one completed framework record', diagnostics)
    record = matches[0]
    steps = {step['step_id']: step['status'] for step in record.get('completed_steps', [])}
    if any(steps.get(step) != 'success' for step in ('deploy', 'drive', 'verify')):
        raise ValueError('framework run did not complete the expected steps')
    result = (record.get('context') or {}).get('metadata', {}).get('script_results', {}).get('drive')
    if not isinstance(result, dict) or result.get('model_used') is not False:
        raise ValueError('no-model driver result is absent from the native framework record')
    return record, result


def verify_task_binding(task, episode, published_record, task_entry, rewrite, trace_task_id, driver_task_id):
    if (task['task_id'] != rewrite(task_entry.id) or task['task'] != rewrite(task_entry.name)
            or episode['task'] != task['task'] or published_record.get('task_id') != task['task_id']):
        raise ValueError('published task and native record do not match the framework task')
    if driver_task_id != trace_task_id or rewrite(trace_task_id) != rewrite(task_entry.name):
        raise ValueError('framework task, trace and native task disagree')


def verify_reward(native_reward, recorded_reward, replayed_reward):
    values = (native_reward, recorded_reward, replayed_reward)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError('reward must be a finite number')
    if not all(math.isclose(native_reward, value, rel_tol=0.0, abs_tol=1e-12) for value in values[1:]):
        raise ValueError('native episode reward differs from simulator replay')


def verify_loaded_trace(loaded, framework_trace_hash):
    if hashlib.sha256(canonical(loaded)).hexdigest() != framework_trace_hash:
        raise ValueError('loaded trace hash differs from framework-recorded trace')


def export_and_check(dataset_dir, bundle_dir, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_DATASETS_OFFLINE'] = '1'
    from datasets import load_dataset
    from huggingface_hub import DatasetCard
    from agentenv_hf import records, runs
    from agent_env.store.routing import namespace_routing
    from envs.wildfire_response.agentenv.export import (
        TRAJECTORY_EPISODES, TRAJECTORY_TRANSITIONS, _contains_private_key, _write_parquet)
    from envs.wildfire_response.regional.benchmark_v1 import verify_benchmark_trajectory
    from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog, task_ids
    from envs.wildfire_response.regional.prepared.data import load_inputs
    from envs.wildfire_response.regional.topographic.data import load_domain
    from check_agentenv_dataset_roundtrip import compare_transitions

    root, bundle_dir = Path(dataset_dir).resolve(), Path(bundle_dir).resolve()
    tasks = load_dataset(str(root), 'wildfire-ci_tasks', split='development')
    episodes = load_dataset(str(root), 'wildfire-ci_episodes', split='development')
    if len(tasks) != 1 or len(episodes) != 1:
        raise ValueError('expected exactly one fixture task and one native episode')
    task, episode = tasks[0], episodes[0]
    diagnostics.update(native_task=safe_id(task.get('task')), native_task_id=safe_id(task.get('task_id')),
                       native_episode_id=safe_id(episode.get('episode_id')),
                       native_episode_task=safe_id(episode.get('task')), native_status=safe_id(episode.get('status')),
                       native_reward=episode.get('reward') if type(episode.get('reward')) in (int, float)
                       and math.isfinite(episode['reward']) else None)
    if task['agents'] or episode['model'] is not None or episode['agent'] is not None:
        raise ValueError('fixture must not deploy or use a model agent')
    if episode['status'] != 'completed' or episode['failed_step']:
        raise ValueError('native fixture task or verifier did not complete successfully')
    native_paths = [root / 'tasks/wildfire-ci.parquet', root / 'episodes/wildfire-ci.parquet',
                    root / 'raw/wildfire-ci.jsonl']
    native_hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in native_paths}
    native_raw = [json.loads(line) for line in native_paths[2].read_text().splitlines()]
    if (len(native_raw) != 1 or native_raw[0]['episode_id'] != episode['episode_id']
            or native_raw[0].get('trajectories') or episode['messages']):
        raise ValueError('native record identity differs or contains unexpected chat trajectory')
    bundle = runs.locate(str(bundle_dir))
    rewrite = records.Rewrite(bundle.id_root, 'wildfire-ci')
    with namespace_routing():
        instances, unfinished = runs.instances(bundle, 'latest')
        if unfinished:
            raise ValueError('unfinished framework runs exist')
        diagnostics['framework_lookup'] = link_diagnostics(instances, episode['episode_id'], rewrite, records.record)
        framework, result = choose_run(instances, episode['episode_id'], rewrite, records.record, native_raw[0]['record'])
    matching_tasks = [entry for entry in runs.tasks(bundle) if entry.id == framework['task_id']]
    if len(matching_tasks) != 1:
        raise ValueError('matched framework run does not belong to exactly one bundle task')
    task_entry = matching_tasks[0]
    diagnostics.update(framework_task_id=safe_id(framework['task_id']),
                       rewritten_framework_task_id=safe_id(rewrite(task_entry.id)),
                       bundle_task_name=safe_id(task_entry.name))
    trajectory, verification = result.get('trajectory'), result.get('verification')
    if not isinstance(trajectory, dict) or not isinstance(verification, dict) or verification.get('passed') is not True:
        raise ValueError('framework record lacks verified public trajectory')
    if _contains_private_key(trajectory):
        raise ValueError('framework trajectory contains private or future scenario fields')
    path = bundle_dir / 'envs/wildfire/data'
    lock = json.loads((path / 'catalog_lock.json').read_text())
    if lock.get('version') != 'synthetic-agentenv-fixture-v1':
        raise ValueError('expected separately versioned synthetic fixture')
    prepared = load_inputs(path / 'prepared_inputs.json', expected_sha256=lock['prepared_inputs_sha256'])
    domain = load_domain(path / 'topographic_inputs.json', prepared, lock['topographic_inputs_sha256'])
    catalog = build_catalog(prepared, domain)
    if catalog['sha256'] != lock['catalog_sha256'] or trajectory['task_id'] not in task_ids(catalog, 'development'):
        raise ValueError('fixture catalog or development task mismatch')
    diagnostics['trace_task_id'] = safe_id(trajectory.get('task_id'))
    verify_task_binding(task, episode, native_raw[0]['record'], task_entry, rewrite,
                        trajectory['task_id'], result.get('task_id'))
    transitions = trajectory['transitions']
    if (len(transitions) != 60 or transitions[-1]['observation']['minute'] != 120
            or not transitions[-1]['terminated'] or transitions[-1]['truncated']):
        raise ValueError('trace did not complete full 120-minute horizon')
    replay = verify_benchmark_trajectory(trajectory, prepared, domain, catalog=catalog)
    if not replay['passed']:
        raise ValueError('framework trace failed independent replay')
    verify_reward(episode['reward'], verification['reward'], replay['objective_score'])
    native_verifications = native_raw[0]['record'].get('metadata', {}).get('verifications', {})
    native_verifier = native_verifications.get('wildfire_reward', {})
    if not native_verifier.get('results') or not all(
            criterion.get('result') is True for criterion in native_verifier['results']):
        raise ValueError('native record does not contain successful technical verification')
    trace_hash = hashlib.sha256(canonical(trajectory)).hexdigest()
    episode_id = episode['episode_id']
    episode_row = dict(episode_id=episode_id, task_id=trajectory['task_id'], split='development',
        run_id=framework['instance_id'], model=None, trajectory_sha256=trace_hash, decisions=len(transitions),
        end_minute=120, terminated=True, truncated=False, reward_version=trajectory['reward_version'],
        final_reward=sum(float(item.get('objective_reward', 0)) for item in transitions),
        replay_verified=True, training_eligible=False)
    rows, previous = [], trajectory['initial_observation']
    for index, item in enumerate(transitions):
        current = item['observation']
        rows.append(dict(episode_id=episode_id, task_id=trajectory['task_id'], split='development', step=index,
            minute=previous['minute'], next_minute=current['minute'], observation=previous,
            action=item['action'], next_observation=current, reward=float(item.get('objective_reward', 0)),
            action_valid=item.get('action_valid'), terminated=item['terminated'], truncated=item['truncated']))
        previous = current
    extension = root / 'simulator_extension'
    if extension.exists():
        raise ValueError('extension already exists')
    extension.mkdir()
    _write_parquet(extension / 'episodes.parquet', [episode_row], TRAJECTORY_EPISODES)
    _write_parquet(extension / 'transitions.parquet', rows, TRAJECTORY_TRANSITIONS)
    (extension / 'raw.jsonl').write_bytes(canonical(dict(episode_id=episode_id, trajectory_sha256=trace_hash,
        trajectory=trajectory, verification=verification)) + b'\n')
    card = DatasetCard.load(str(root / 'README.md'))
    configurations = list(card.data.get('configs') or [])
    configs = [('wildfire-ci_simulator_episodes', 'episodes.parquet'),
               ('wildfire-ci_simulator_transitions', 'transitions.parquet')]
    for name, filename in configs:
        if any(item.get('config_name') == name for item in configurations):
            raise ValueError('extension config already exists')
        configurations.append({'config_name': name, 'data_files': [{'split': 'development',
                               'path': f'simulator_extension/{filename}'}]})
    card.data.configs = configurations
    card.text += ('\n\nSimulator extension rows reference the AgentEnv native episode_id. '
                  'They contain a public simulator trace, not a model conversation.\n')
    (root / 'README.md').write_text(str(card))
    loaded_episodes = load_dataset(str(root), configs[0][0], split='development')
    loaded_transitions = load_dataset(str(root), configs[1][0], split='development')
    raw_extension = json.loads((extension / 'raw.jsonl').read_text())
    if (len(loaded_episodes) != 1 or loaded_episodes[0]['episode_id'] != episode_id
            or loaded_episodes[0]['trajectory_sha256'] != trace_hash
            or raw_extension['episode_id'] != episode_id
            or raw_extension['trajectory_sha256'] != trace_hash):
        raise ValueError('extension episode, foreign key, digest or raw trace differs')
    verify_loaded_trace(raw_extension['trajectory'], trace_hash)
    if any(row['episode_id'] != episode_id or row['task_id'] != trajectory['task_id']
           or row['split'] != 'development' for row in loaded_transitions):
        raise ValueError('extension transition foreign key differs')
    compare_transitions(raw_extension['trajectory'], list(loaded_transitions))
    second_replay = verify_benchmark_trajectory(raw_extension['trajectory'], prepared, domain, catalog=catalog)
    if not second_replay['passed']:
        raise ValueError('loaded trace failed independent replay')
    verify_reward(episode['reward'], loaded_episodes[0]['final_reward'], second_replay['objective_score'])
    verify_reward(episode['reward'], verification['reward'], second_replay['objective_score'])
    if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != sha for name, sha in native_hashes.items()):
        raise ValueError('native files changed while adding simulator extension')
    return {'status': 'passed', 'scope': 'synthetic no-model AgentEnv framework full-horizon fixture',
            'episode_id': episode_id, 'task_id': trajectory['task_id'], 'decisions': len(transitions),
            'end_minute': 120, 'native_episode_count': len(episodes), 'native_chat_trajectories': 0,
            'model': episode['model'], 'native_reward': episode['reward'],
            'replayed_reward': second_replay['objective_score'], 'trajectory_sha256_canonical_json': trace_hash,
            'independent_replay_after_dataset_load': True, 'native_files_unchanged': True,
            'schemas': {'native_tasks': str(tasks.features), 'native_episodes': str(episodes.features),
                        'simulator_episodes': str(loaded_episodes.features),
                        'simulator_transitions': str(loaded_transitions.features)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, required=True)
    parser.add_argument('--bundle-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    diagnostics = {}
    try:
        result = export_and_check(args.dataset_dir, args.bundle_dir, diagnostics)
    except Exception as exc:
        result = {'status': 'failed', 'error_type': type(exc).__name__,
                  'error': str(exc) if isinstance(exc, NativeLinkError) else 'validation failed; diagnostic details withheld'}
        if isinstance(exc, NativeLinkError):
            diagnostics['framework_lookup'] = exc.diagnostics
        result['id_diagnostics'] = diagnostics
        args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        raise SystemExit(1) from None
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
