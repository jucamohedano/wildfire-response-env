"""Export grouped tasks and audit weak-policy difficulty, with training-only defaults."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import time

from ..eval.run import dump, load
from .benchmark import verify_lock
from .data import PACK
from .run import replay, run
from .environment import REWARD_VERSION, WEIGHTS
from .scenarios import DATASET_VERSION, generate_tasks


def export_tasks(output):
    benchmark = verify_lock()
    output = Path(output)
    output.mkdir(parents=True)
    private = output / 'private'
    private.mkdir()
    tasks = generate_tasks()
    for split in ('train', 'validation', 'test'):
        subset = [task for task in tasks if task['split'] == split]
        dump(private / f'{split}.json', subset)
        public = [{key: task[key] for key in ('id','split','group_id','seed','synthetic','configuration_sha256')} for task in subset]
        (output / f'{split}.jsonl').write_text(''.join(json.dumps(task)+'\n' for task in public), encoding='utf-8')
    manifest = dict(version=DATASET_VERSION, benchmark=benchmark, count=len(tasks), splits=dict(train=24, validation=6, test=6),
        reward_version=REWARD_VERSION, weights=WEIGHTS,
        pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest(),
        tasks_sha256=hashlib.sha256(json.dumps(tasks,sort_keys=True).encode()).hexdigest(),
        grouping='Disjoint spatial/configuration groups and seeds; not held-out real incidents or maps',
        status='frozen synthetic benchmark inputs; no model-training or publication readiness claim')
    dump(output/'manifest.json', manifest)
    return tasks


def evaluate(output, split='train', limit=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh evaluation directory')
    if split not in ('train', 'validation', 'test') or (limit is not None and (type(limit) is not int or limit < 1)):
        raise ValueError('Invalid split or limit')
    tasks = export_tasks(output)
    selected = [task for task in tasks if task['split'] == split]
    if limit is not None:
        selected = selected[:limit]
    started = time.perf_counter()
    rows, flags = [], []
    for task in selected:
        outcomes = {}
        for policy in ('passive', 'random', 'blanket_warning', 'cooperative'):
            directory = output / f'{task["id"]}-{policy}'
            result = run(directory, task['id'], policy)
            verified = load(directory/'verification.json')
            result['independent_verification'] = verified['passed']
            replay(load(directory/'trajectory.json.gz'))
            result.update(family=task['family'], split=split, replay_passed=True)
            outcomes[policy] = result
            rows.append(result)
            print(json.dumps(dict(scenario=task['id'], policy=policy, reward=result['total_reward'])), flush=True)
        reference = outcomes['cooperative']['total_reward']
        if task['family'] != 'false_alarm':
            for policy in ('passive', 'random', 'blanket_warning'):
                if outcomes[policy]['total_reward'] >= reference-1e-9:
                    flags.append(dict(scenario=task['id'], family=task['family'],
                                      issue='weak_policy_competitive', policy=policy,
                                      both_unresolved=bool(outcomes[policy]['components']['unresolved_incident']
                                                           and outcomes['cooperative']['components']['unresolved_incident'])))
        elif outcomes['blanket_warning']['total_reward'] >= outcomes['passive']['total_reward']:
            flags.append(dict(scenario=task['id'], family=task['family'], issue='false_alarm_reward_ordering'))
    invalid = sum(row['components']['invalid_actions'] for row in rows)
    if any(task['family']!='false_alarm' for task in selected) and not any(
            row['components']['exposed_population']>0 for row in rows if row['policy']=='passive'):
        flags.append(dict(issue='missing_population_protection_signal', scope=split))
    result = dict(version=DATASET_VERSION, benchmark=verify_lock(), reward_version=REWARD_VERSION, evaluated_split=split, scenarios=len(selected), episodes=len(rows),
        independently_verified=sum(row['independent_verification'] for row in rows), exact_replays=sum(row['replay_passed'] for row in rows),
        invalid_actions=invalid, difficulty_flags=flags, requires_calibration_review=bool(flags) or bool(invalid),
        ready_for_model_training=False,
        policies={policy: dict(mean_reward=sum(row['total_reward'] for row in rows if row['policy']==policy)/len(selected),
            mean_exposure=sum(row['components']['exposed_population'] for row in rows if row['policy']==policy)/len(selected),
            natural_completions=sum(row['terminated'] for row in rows if row['policy']==policy))
            for policy in ('passive','random','blanket_warning','cooperative')},
        elapsed_seconds=round(time.perf_counter()-started,3), peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        limitation='Flags diagnose task/objective/baseline issues; no tasks silently filtered, no proven optimum claimed')
    dump(output/'scores.json', rows)
    dump(output/'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--split', choices=['train','validation','test'], default='train')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--generate-only', action='store_true')
    args = parser.parse_args()
    if args.generate_only:
        export_tasks(args.output)
    else:
        result = evaluate(args.output,args.split,args.limit)
        print(json.dumps({key:value for key,value in result.items() if key!='difficulty_flags'}))


if __name__ == '__main__':
    main()
