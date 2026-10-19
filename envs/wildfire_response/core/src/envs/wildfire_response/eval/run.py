"""Generate datasets and run reproducible baseline evaluations with one CPU worker."""
import argparse
import gzip
import hashlib
import json
import resource
import subprocess
import time
from pathlib import Path

from ..baselines import DeterministicBaseline, RandomValidBaseline, RetrievalBaseline, passive, signature
from ..core.environment import WildfireResponseEnv, SCHEMA_VERSION
from ..core.reward import REWARD_VERSION, WEIGHTS, cost
from ..core.scenarios import DATASET_VERSION, generate_tasks


def dump(path, value):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'wt', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
        stream.write('\n')


def load(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def generate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    tasks = generate_tasks()
    private = output/'private'
    private.mkdir(exist_ok=True)
    for split in ('train', 'validation', 'test'):
        subset = [task for task in tasks if task['split'] == split]
        dump(private/f'{split}.json', subset)
        public = [dict(id=task['id'], split=split, group_id=task['group_id'], seed=task['seed'],
                       family=task['family'], synthetic=True) for task in subset]
        with (output/f'{split}.jsonl').open('w') as stream:
            for task in public:
                stream.write(json.dumps(task)+'\n')
    dump(output/'manifest.json', dict(version=DATASET_VERSION, count=120, splits={'train':80,'validation':20,'test':20},
         reward_version=REWARD_VERSION, weights=WEIGHTS, generator='core.scenarios.generate_tasks',
         sha256=hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()))


def source_digest():
    root = Path(__file__).resolve().parents[3]
    files = sorted((root/'envs'/'wildfire_response').rglob('*.py'))
    files += [root/'simulator'/filename for filename in ('engine.py','terrain.py','policy.py')]
    files += [root/'simulator'/'static'/'maps'/'brunete-illustrated.json']
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_episode(task, policy_name, max_decisions=32, bank=None):
    env = WildfireResponseEnv(max_decisions=max_decisions)
    observation, info = env.reset(scenario=task)
    policies = {'deterministic': DeterministicBaseline, 'random': lambda: RandomValidBaseline(task['seed']),
                'passive': lambda: passive, 'retrieval': lambda: RetrievalBaseline(bank)}
    policy = policies[policy_name]()
    trajectory = dict(version=SCHEMA_VERSION, reward_version=REWARD_VERSION, scenario_id=task['id'],
                      split=task['split'], seed=task['seed'], max_decisions=max_decisions, policy=policy_name,
                      initial_observation=observation, initial_info=info, transitions=[])
    if policy_name == 'retrieval':
        trajectory['retrieval_bank_sha256'] = hashlib.sha256(Path(bank).read_bytes()).hexdigest()
    started = time.perf_counter()
    while True:
        action = policy(observation)
        observation, value, terminated, truncated, info = env.step(action)
        trajectory['transitions'].append(dict(action=action, observation=observation, reward=value,
                                               terminated=terminated, truncated=truncated, info=info,
                                               retrieved_case_ids=getattr(policy, 'retrieved_ids', [])[:]))
        if terminated or truncated:
            break
    private = env.evaluator_state()
    trajectory['elapsed_seconds'] = round(time.perf_counter()-started, 3)
    trajectory['total_reward'] = sum(item['reward'] for item in trajectory['transitions'])
    return trajectory, private


def replay(trajectory):
    env = WildfireResponseEnv(max_decisions=trajectory['max_decisions'])
    observation, _ = env.reset(trajectory['scenario_id'], trajectory['seed'])
    if observation != trajectory['initial_observation']:
        raise ValueError('Initial observation differs')
    total = 0.0
    for transition in trajectory['transitions']:
        observation, value, terminated, truncated, info = env.step(transition['action'])
        if observation != transition['observation'] or abs(value-transition['reward']) > 1e-10:
            raise ValueError('Replay observation or reward differs')
        if (terminated, truncated, info) != (transition['terminated'], transition['truncated'], transition['info']):
            raise ValueError('Replay lifecycle differs')
        total += value
    if abs(total-trajectory['total_reward']) > 1e-10:
        raise ValueError('Total reward differs')
    return total


def make_bank(input_directory, output):
    cases = []
    for path in sorted(Path(input_directory).glob('*.json*')):
        trajectory = load(path)
        if trajectory.get('split') != 'train' or 'transitions' not in trajectory:
            raise ValueError('Only training trajectories can enter the frozen bank')
        first = trajectory['initial_observation']
        reports = [report for report in first['reports'] if report['location']]
        cases.append(dict(split='train', scenario_id=trajectory['scenario_id'], signature=signature(first),
                          cost=-trajectory['total_reward'],
                          investigated_location=reports[0]['location'] if reports else None))
    cases.sort(key=lambda case: case['cost'])
    dump(output, cases)


def transition_rows(input_directory):
    for path in sorted(Path(input_directory).glob('*.json*')):
        trajectory = load(path)
        previous = trajectory['initial_observation']
        for index, transition in enumerate(trajectory['transitions']):
            yield dict(scenario_id=trajectory['scenario_id'], split=trajectory['split'], seed=trajectory['seed'],
                             policy=trajectory['policy'], step=index, schema_version=trajectory['version'],
                             reward_version=trajectory['reward_version'],
                             observation=json.dumps(previous, separators=(',', ':')),
                             action=json.dumps(transition['action'], separators=(',', ':')),
                             next_observation=json.dumps(transition['observation'], separators=(',', ':')),
                             reward=transition['reward'], terminated=transition['terminated'], truncated=transition['truncated'])
            previous = transition['observation']


def export_transitions(input_directory, output, parquet=False):
    rows = transition_rows(input_directory)
    if parquet:
        import pyarrow as arrow
        import pyarrow.parquet as pq
        import itertools
        writer = None
        try:
            while batch := list(itertools.islice(rows, 32)):
                table = arrow.Table.from_pylist(batch)
                if writer is None:
                    writer = pq.ParquetWriter(output, table.schema)
                writer.write_table(table)
        finally:
            if writer:
                writer.close()
    else:
        with gzip.open(output, 'wt', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/wildfire-response')
    parser.add_argument('--split', choices=['train','validation','test','all','smoke'], default='smoke')
    parser.add_argument('--policies', nargs='+', choices=['passive','random','deterministic','retrieval'],
                        default=['passive','random','deterministic'])
    parser.add_argument('--max-decisions', type=int, default=32)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--generate-only', action='store_true')
    parser.add_argument('--bank')
    parser.add_argument('--build-bank')
    parser.add_argument('--replay')
    parser.add_argument('--export-transitions')
    parser.add_argument('--parquet', action='store_true')
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(dict(replayed_reward=replay(load(args.replay)))))
        return
    if args.export_transitions:
        export_transitions(args.export_transitions, args.output, args.parquet)
        return
    if args.build_bank:
        make_bank(args.build_bank, args.output)
        return
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be positive')
    if 'retrieval' in args.policies and not args.bank:
        parser.error('--bank is required for the retrieval baseline')
    if (Path(args.output)/'results.jsonl').exists():
        parser.error('Output already contains an evaluation; choose a fresh directory to avoid mixing runs')
    generate(args.output)
    if args.generate_only:
        return
    tasks = generate_tasks()
    if args.split == 'smoke':
        tasks = tasks[:6]
    elif args.split != 'all':
        tasks = [task for task in tasks if task['split'] == args.split]
    if args.limit:
        tasks = tasks[:args.limit]
    root = Path(args.output)
    trajectory_dir = root/'trajectories'
    trajectory_dir.mkdir(exist_ok=True)
    rows = []
    revision = subprocess.run(['git','rev-parse','HEAD'], capture_output=True, text=True).stdout.strip()
    digest = source_digest()
    started = time.perf_counter()
    for task in tasks:
        for policy in args.policies:
            trajectory, private = run_episode(task, policy, args.max_decisions, args.bank)
            trajectory['engine_revision'] = revision
            trajectory['source_sha256'] = digest
            filename = f"{task['id']}-{policy}.json.gz"
            dump(trajectory_dir/filename, trajectory)
            dump(root/'private'/filename, private)
            dispositions = trajectory['transitions'][-1]['observation']['report_dispositions']
            relevant = {item['report_id'] for item in private['annotations'] if item['event_related']}
            duplicates = {item['report_id'] for item in private['annotations'] if item['duplicate_of']}
            selected = {ident for ident, item in dispositions.items() if item['status'] == 'investigate'}
            dismissed = {ident for ident, item in dispositions.items() if item['status'] == 'dismiss'}
            ack = [item for transition in trajectory['transitions']
                   for item in transition['observation']['delivery_acknowledgements']]
            row = dict(scenario=task['id'], family=task['family'], split=task['split'], policy=policy,
                       total_reward=trajectory['total_reward'], components=private['components'],
                       seconds=trajectory['elapsed_seconds'], decisions=len(trajectory['transitions']),
                       terminated=trajectory['transitions'][-1]['terminated'],
                       triage_coverage=len(selected & relevant)/max(len(relevant),1),
                       duplicates_recognized=len(dismissed & duplicates),
                       first_message_delivery_tick=min((item['delivered_at'] for item in ack if 'recipient' in item), default=None))
            rows.append(row)
            with (root/'results.jsonl').open('w') as stream:
                for result in rows:
                    stream.write(json.dumps(result)+'\n')
            print(f"{task['id']} {policy}: reward={row['total_reward']:.4f}, {row['seconds']:.2f}s", flush=True)
    summary = dict(schema_version=SCHEMA_VERSION, engine_revision=revision, source_sha256=digest, weights=WEIGHTS,
                   episodes=len(rows), elapsed_seconds=round(time.perf_counter()-started,3),
                   peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   policies={policy: dict(episodes=len([row for row in rows if row['policy']==policy]),
                             mean_reward=sum(row['total_reward'] for row in rows if row['policy']==policy)/
                                         max(1,len([row for row in rows if row['policy']==policy])))
                             for policy in args.policies})
    dump(root/'summary.json', summary)


if __name__ == '__main__':
    main()
