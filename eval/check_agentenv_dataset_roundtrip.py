"""Verify a local AgentEnv export's development trajectory against independent replay.

This checks a supplemental wildfire trajectory, not a native AgentEnv runtime run.
No Hub access or uploads are performed.
"""
import argparse
import gzip
import json
import os
import time
from pathlib import Path


def decoded(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def compare_transitions(trajectory, rows):
    expected = trajectory['transitions']
    rows = sorted(rows, key=lambda row: row['step'])
    if len(rows) != len(expected):
        raise ValueError('transition count differs')
    current = trajectory['initial_observation']
    for index, (row, transition) in enumerate(zip(rows, expected)):
        following = transition['observation']
        checks = {
            'step': (row['step'], index),
            'minute': (row['minute'], current.get('minute')),
            'next_minute': (row['next_minute'], following.get('minute')),
            'observation': (decoded(row['observation']), current),
            'action': (decoded(row['action']), transition['action']),
            'next_observation': (decoded(row['next_observation']), following),
            'reward': (row['reward'], float(transition.get('objective_reward', 0.0))),
            'action_valid': (row['action_valid'], transition.get('action_valid')),
            'terminated': (row['terminated'], transition['terminated']),
            'truncated': (row['truncated'], transition['truncated']),
        }
        for field, (actual, wanted) in checks.items():
            if actual != wanted:
                raise ValueError(f'transition {index} differs: {field}')
        current = following
    return len(rows)


def check(dataset_dir, prepared_path, domain_path, episode_id):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_DATASETS_OFFLINE'] = '1'
    from datasets import load_dataset
    from envs.wildfire_response.agentenv.export import _repo_root
    from envs.wildfire_response.regional.benchmark_v1 import verify_benchmark_trajectory
    from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog
    from envs.wildfire_response.regional.prepared.data import load_inputs
    from envs.wildfire_response.regional.topographic.data import load_domain

    started = time.monotonic()
    dataset_dir = Path(dataset_dir).resolve()
    lock = json.loads((_repo_root() / 'data/benchmark_v1/catalog_lock.json').read_text())
    prepared = load_inputs(prepared_path, expected_sha256=lock['prepared_inputs_sha256'])
    domain = load_domain(domain_path, prepared, lock['topographic_inputs_sha256'])
    catalog = build_catalog(prepared, domain)
    if catalog['sha256'] != lock['catalog_sha256']:
        raise ValueError('catalog does not match frozen lock')
    datasets = {}
    native_error = None
    for label, config in [('tasks', 'wildfire-development_tasks'),
                          ('native_episodes', 'wildfire-development_episodes'),
                          ('episodes', 'wildfire_trajectory_episodes'),
                          ('transitions', 'wildfire_trajectory_transitions')]:
        try:
            datasets[label] = load_dataset(str(dataset_dir), config, split='development')
        except Exception as exc:
            if label != 'native_episodes':
                raise
            cause = exc.__cause__ or exc
            native_error = f'{type(exc).__name__}: {type(cause).__name__}: {cause}'
    episodes = [row for row in datasets['episodes'] if row['episode_id'] == episode_id]
    if len(episodes) != 1:
        raise ValueError('expected exactly one matching development episode')
    episode = episodes[0]
    if episode['split'] != 'development' or not episode['task_id'].startswith('bench-dev-') or episode['training_eligible'] is not False:
        raise ValueError('episode is not an eligible development-only supplemental record')
    raw_path = dataset_dir / 'trajectories/raw/wildfire-development.jsonl.gz'
    with gzip.open(raw_path, 'rt', encoding='utf-8') as stream:
        matches = [row for line in stream if (row := json.loads(line))['episode_id'] == episode_id]
    if len(matches) != 1:
        raise ValueError('expected exactly one matching raw trace')
    trajectory = matches[0]['trajectory']
    if trajectory['task_id'] != episode['task_id'] or matches[0]['trajectory_sha256'] != episode['trajectory_sha256']:
        raise ValueError('raw trace identity or source digest differs from episode table')
    rows = [row for row in datasets['transitions'] if row['episode_id'] == episode_id]
    if any(row['task_id'] != episode['task_id'] or row['split'] != 'development' for row in rows):
        raise ValueError('transition task or split differs')
    count = compare_transitions(trajectory, rows)
    if count != episode['decisions']:
        raise ValueError('episode decision count differs')
    replay = verify_benchmark_trajectory(trajectory, prepared, domain, catalog=catalog)
    if not replay.get('passed'):
        raise ValueError('independent replay failed')
    last = trajectory['transitions'][-1]
    if (episode['end_minute'] != last['observation']['minute']
            or episode['terminated'] != last['terminated'] or episode['truncated'] != last['truncated']
            or episode['final_reward'] != sum(float(row.get('objective_reward', 0.0)) for row in trajectory['transitions'])):
        raise ValueError('episode outcome differs from raw trace')
    return {'status': 'partial_native_loader_failed' if native_error else 'passed', 'scope': 'local supplemental trajectory roundtrip, not an AgentEnv run',
            'task_id': episode['task_id'], 'episode_id': episode_id, 'transitions': count,
            'independent_replay_passed': True, 'native_episodes_count': len(datasets['native_episodes']) if 'native_episodes' in datasets else None,
            'native_loader_error': native_error,
            'schemas': {name: str(data.features) for name, data in datasets.items()},
            'rows': {name: len(data) for name, data in datasets.items()},
            'elapsed_seconds': round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', required=True, type=Path)
    parser.add_argument('--prepared-inputs', required=True, type=Path)
    parser.add_argument('--topographic-inputs', required=True, type=Path)
    parser.add_argument('--episode-id', required=True)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    result = check(args.dataset_dir, args.prepared_inputs, args.topographic_inputs, args.episode_id)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(result))
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
