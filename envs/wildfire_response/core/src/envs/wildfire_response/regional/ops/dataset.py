"""Build locked synthetic cases and evaluate train/validation on a single CPU worker."""
import argparse
import json
import resource
import sys
import time
from collections import Counter
from pathlib import Path

from ...eval.run import dump, source_digest
from .generator import (GENERATOR_VERSION, SPLITS, VARIANTS, case_ids, configuration_groups,
                        digest, get_generated_task, lock_manifest, validate_scenario, verify_catalog_lock)
from .run import POLICIES, run_episode
from .objectives import validate_objectives


def audit_catalog():
    lock_sha256 = verify_catalog_lock()
    groups = configuration_groups()
    if len({group['configuration_sha256'] for group in groups}) != 120:
        raise ValueError('Configuration groups overlap')
    seeds = set()
    operational = {}
    families = {}
    for split in SPLITS:
        families[split] = dict(Counter(group['factors']['family'] for group in groups if group['split'] == split))
        if len(families[split]) != 6:
            raise ValueError('Every split must cover all six families')
        for identifier in case_ids(split):
            task = get_generated_task(identifier)
            validate_scenario(task['scenario'])
            validate_objectives(task['objectives'])
            normalized = {field: value for field, value in task['scenario'].items() if field not in ('id', 'seed')}
            normalized['reports'] = [{field: value for field, value in report.items() if field not in ('text', 'source')}
                                     for report in task['scenario']['reports']]
            signature = digest(normalized)
            if signature in operational and operational[signature] != task['group_id']:
                raise ValueError('Operational configurations overlap across incident groups')
            operational[signature] = task['group_id']
            if task['seed'] in seeds:
                raise ValueError('Seeds overlap across case variants')
            seeds.add(task['seed'])
    return dict(passed=True, groups=120, cases=len(seeds), operational_configurations=len(operational), split_groups=SPLITS,
                families=families, lock_sha256=lock_sha256,
                scope='Structural validation only; no test policy outcomes evaluated')


def build(output):
    audit = audit_catalog()
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh dataset directory')
    output.mkdir(parents=True)
    private = output / 'private'
    private.mkdir()
    for split in SPLITS:
        with (output / f'{split}.jsonl').open('w', encoding='utf-8') as public_stream, \
                (private / f'{split}.jsonl').open('w', encoding='utf-8') as private_stream:
            for identifier in case_ids(split):
                task = get_generated_task(identifier)
                public = {field: task[field] for field in (
                    'id', 'split', 'group_id', 'family', 'seed', 'variant', 'generator_version',
                    'configuration_sha256', 'challenge_class')}
                public.update(synthetic=True, task_set='control')
                public_stream.write(json.dumps(public, sort_keys=True) + '\n')
                private_stream.write(json.dumps(task, sort_keys=True) + '\n')
    manifest = dict(**lock_manifest(), lock_sha256=audit['lock_sha256'], structural_audit=audit,
                    test_outcomes_evaluated=False, synthetic=True)
    dump(output / 'manifest.json', manifest)
    return manifest


def evaluate(output, split='train', policies=('control', 'random_valid'), *, max_groups=None,
             variants=VARIANTS, traces='none', resume=False, consume_test=False):
    lock_sha256 = verify_catalog_lock()
    if split not in SPLITS or (split == 'test' and not consume_test):
        raise ValueError('Test outcomes are sealed; use explicit --split test --consume-test only for final evaluation')
    if (not policies or len(set(policies)) != len(policies) or any(policy not in POLICIES for policy in policies)
            or type(variants) is not int or not 1 <= variants <= VARIANTS
            or traces not in ('none', 'all', 'failures')):
        raise ValueError('Choose valid policies, one to three variants, and a trace mode')
    if max_groups is not None and (type(max_groups) is not int or max_groups < 1):
        raise ValueError('max_groups must be positive')
    groups = [group for group in configuration_groups() if group['split'] == split]
    if max_groups is not None:
        groups = groups[:max_groups]
    identifiers = [f"control-{split}-{group['index']:03d}-v{variant}"
                   for group in groups for variant in range(variants)]
    specification = dict(version=GENERATOR_VERSION, lock_sha256=lock_sha256,
        source_sha256=source_digest(), split=split, cases=identifiers, policies=list(policies),
        variants=variants, traces=traces, workers=1, test_consumed=split == 'test')
    output = Path(output)
    if output.exists():
        if not resume or not (output / 'run.json').is_file():
            raise ValueError('Choose a fresh evaluation directory or explicitly resume a matching run')
        if json.loads((output / 'run.json').read_text()) != specification:
            raise ValueError('Resume specification differs in source, catalog, cases or policies')
    else:
        output.mkdir(parents=True)
        dump(output / 'run.json', specification)
    started = time.perf_counter()
    summaries = []
    reused = 0
    for identifier in identifiers:
        directory = output / 'episodes' / identifier
        directory.mkdir(parents=True, exist_ok=True)
        for policy in policies:
            record_path = directory / f'{policy}.json'
            if resume and record_path.exists():
                record = json.loads(record_path.read_text())
                checksum = record.pop('sha256')
                if (digest(record) != checksum or not record['verification']['passed']
                        or record['summary']['task_id'] != identifier or record['summary']['policy'] != policy):
                    raise ValueError('Cached evaluation record is corrupt or inconsistent')
                summary = record['summary']
                reused += 1
            else:
                trajectory, summary, verification = run_episode(identifier, policy)
                if traces == 'all' or (traces == 'failures' and not summary['all_objectives_met']):
                    dump(directory / f'{policy}.trajectory.json.gz', trajectory)
                record = dict(summary=summary, verification=verification)
                record['sha256'] = digest(record)
                temporary = record_path.with_suffix('.tmp')
                dump(temporary, record)
                temporary.replace(record_path)
                del trajectory
            summaries.append(summary)
            if len(summaries) % 12 == 0:
                print(json.dumps(dict(completed=len(summaries), total=len(identifiers) * len(policies),
                                      split=split, elapsed_seconds=round(time.perf_counter() - started, 2))), flush=True)
    aggregates = {}
    for policy in policies:
        rows = [row for row in summaries if row['policy'] == policy]
        aggregates[policy] = dict(episodes=len(rows), mean_score=sum(row['objective_score'] for row in rows) / len(rows),
            full_control_passes=sum(row['all_objectives_met'] for row in rows),
            exposed_episodes=sum(row['diagnostics']['exposed_population'] > 0 for row in rows),
            unresolved_episodes=sum(row['control_evidence']['active_fire_cells'] > 0 for row in rows),
            invalid_actions=sum(row['invalid_actions'] for row in rows),
            failed_deliveries=sum(row['failed_deliveries'] for row in rows),
            component_passes={kind: sum(row['objective_components'][kind] for row in rows)
                              for kind in rows[0]['objective_components']})
    result = dict(specification=specification, aggregates=aggregates, completed=len(summaries), reused=reused,
        elapsed_seconds=round(time.perf_counter() - started, 6),
        process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == 'darwin' else 1024), all_verified=True)
    dump(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('audit')
    builder = commands.add_parser('build')
    builder.add_argument('--output', required=True)
    runner = commands.add_parser('evaluate')
    runner.add_argument('--output', required=True)
    runner.add_argument('--split', choices=list(SPLITS), default='train')
    runner.add_argument('--policies', nargs='+', choices=POLICIES, default=['control', 'random_valid'])
    runner.add_argument('--max-groups', type=int)
    runner.add_argument('--variants', type=int, default=VARIANTS)
    runner.add_argument('--traces', choices=['none', 'all', 'failures'], default='none')
    runner.add_argument('--resume', action='store_true')
    runner.add_argument('--consume-test', action='store_true')
    args = parser.parse_args()
    if args.command == 'audit':
        result = audit_catalog()
    elif args.command == 'build':
        result = build(args.output)
    else:
        result = evaluate(args.output, args.split, args.policies, max_groups=args.max_groups,
                          variants=args.variants, traces=args.traces, resume=args.resume,
                          consume_test=args.consume_test)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
