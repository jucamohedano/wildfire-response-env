"""Sequential, public-policy benchmark runner with independent grading and grouped statistics."""
import argparse
import gzip
import hashlib
import html
import json
from pathlib import Path
import random
import resource
import statistics
import subprocess
import time

from envs.wildfire_response.regional.benchmark_v1.baselines import POLICIES, PublicDispatcher
from envs.wildfire_response.regional.benchmark_v1.catalog import public_manifest, task_ids
from eval.run_ops_model import dump


def grouped_interval(rows, metric, samples=1000):
    groups = {}
    for row in rows:
        if row['score_eligible']:
            groups.setdefault(row['group_id'], []).append(row[metric])
    means = [statistics.mean(values) for values in groups.values()]
    if not means:
        return None
    rng = random.Random(6101)
    bootstraps = sorted(statistics.mean(rng.choices(means, k=len(means))) for _ in range(samples))
    return [bootstraps[int(samples * .025)], bootstraps[min(samples - 1, int(samples * .975))]]


def summarize(rows, planned=None, *, complete=True, full_split=True):
    board = []
    policies = planned if planned is not None else dict.fromkeys(row['policy'] for row in rows)
    for policy in policies:
        selected = [row for row in rows if row['policy'] == policy]
        eligible = [row for row in selected if row['score_eligible']]
        expected = planned[policy] if planned is not None else len(selected)
        board.append(dict(policy=policy, assigned=expected, attempted=len(selected), scored=len(eligible),
            rank_eligible=complete and full_split and len(eligible) == expected
                and not any(row.get('invalid_abandonment') for row in selected),
            safe_episode_rate=sum(row['safe_success'] for row in eligible) / expected if expected else 0,
            protected_fraction=statistics.mean(row['protected_population_fraction'] for row in eligible) if eligible else None,
            additional_burned_hectares=statistics.mean(row['additional_burned_hectares'] for row in eligible) if eligible else None,
            graded_score=statistics.mean(row['graded_score'] for row in eligible) if eligible else None,
            score_interval=grouped_interval(selected, 'graded_score'),
            invalid_actions=sum(row['invalid_actions'] for row in selected)))
    board.sort(key=lambda row: (row['rank_eligible'], row['safe_episode_rate'],
        row['protected_fraction'] if row['protected_fraction'] is not None else -1,
        -(row['additional_burned_hectares'] if row['additional_burned_hectares'] is not None else float('inf'))), reverse=True)
    return board


def run(output, factory, *, split='development', policies=POLICIES, limit=None,
        communications_ablation=False, seconds=1800, registration=None):
    if not policies or set(policies) - set(POLICIES):
        raise ValueError('Unknown or empty policy selection')
    if type(seconds) is not int or not 1 <= seconds <= 14400:
        raise ValueError('CPU run needs a bounded wall-time budget')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh run directory')
    probe = factory()
    identifiers = task_ids(probe.catalog, split)
    full_count = len(identifiers)
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError('Positive case limit required')
        identifiers = identifiers[:limit]
    experiment = experiment_spec(probe, split, policies, identifiers, communications_ablation)
    if split in ('validation', 'test'):
        if registration is None or json.loads(Path(registration).read_text()) != experiment:
            raise ValueError('Held-out evaluation requires a matching preregistered experiment file')
    output.mkdir(parents=True)
    dump(output/'public'/'manifest.json', public_manifest(probe.catalog))
    dump(output/'private'/'catalog.json', probe.catalog)
    groups = {case['id']: group['id'] for group in probe.catalog['groups'] for case in group['cases']}
    for case in probe.catalog['development']:
        groups[case['id']] = case['configuration_sha256']
    summary = dict(version='wildfire-benchmark-evaluation-v1', source='cpu_reference_evaluation',
        split=split, planned_task_ids=identifiers, catalog_sha256=probe.catalog['sha256'],
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        workers=1, cases=[], status='running', profile=getattr(probe, '_profile', 'central'),
        full_split=len(identifiers) == full_count, experiment=experiment,
        assumptions='Synthetic configuration benchmark on one landscape; no mortality or operational calibration claim')
    choices = [(name, name, True) for name in policies]
    if communications_ablation:
        choices.append(('dispatcher_without_incoming_communications', 'dispatcher', False))
    started = time.monotonic()
    try:
        for identifier in identifiers:
            for label, policy, communications in choices:
                if time.monotonic() - started >= seconds:
                    summary['status'] = 'budget_stopped'
                    return summary
                environment = factory()
                observation, _ = environment.reset(identifier)
                actor = PublicDispatcher(policy, communications=communications)
                invalid = 0
                while True:
                    observation, _, terminated, truncated, info = environment.step(actor(observation))
                    invalid += bool(info.get('error'))
                    if terminated or truncated:
                        break
                    if time.monotonic() - started >= seconds:
                        dump(output/'private'/identifier/label/'partial.json', environment.trajectory())
                        summary['status'] = 'budget_stopped'
                        return summary
                verification = environment.verify_episode()
                if not verification['passed']:
                    raise ValueError('Independent benchmark verification failed')
                outcomes = verification['outcomes']
                path = output/'public'/'trajectories'/identifier/(label+'.json.gz')
                path.parent.mkdir(parents=True, exist_ok=True)
                encoded = gzip.compress(json.dumps(environment.trajectory(), sort_keys=True, allow_nan=False).encode(), mtime=0)
                path.write_bytes(encoded)
                dump(output/'private'/identifier/label/'audit.json', environment.private_audit())
                dump(output/'private'/identifier/label/'verification.json', verification)
                row = dict(task_id=identifier, group_id=groups[identifier], policy=label,
                    decisions=observation['decision'], minute=observation['minute'],
                    invalid_actions=invalid, terminated=terminated, truncated=truncated,
                    trajectory=str(path.relative_to(output)), trajectory_sha256=hashlib.sha256(encoded).hexdigest(),
                    **outcomes)
                summary['cases'].append(row)
                dump(output/'summary.json', summary)
                print(json.dumps({key:row[key] for key in ('task_id','policy','safe_success','graded_score','invalid_actions')}),flush=True)
        summary['status'] = 'complete'
        return summary
    except Exception as error:
        summary.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        summary.update(elapsed_seconds=round(time.monotonic()-started,3),
            peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
            board=summarize(summary['cases'], {label:len(identifiers) for label, _, _ in choices},
                            complete=summary['status']=='complete', full_split=summary['full_split']))
        dump(output/'summary.json',summary)
        render_report(summary,output/'report.html')


def render_report(summary,path):
    rows=[]
    for row in summary['board']:
        values=[row['policy']+(' · provisional' if not row['rank_eligible'] else ''),f"{row['scored']}/{row['assigned']}",
                f"{row['safe_episode_rate']:.1%}",row['protected_fraction'],row['additional_burned_hectares'],row['graded_score']]
        rows.append('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in values)+'</tr>')
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Wildfire benchmark evaluation</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:20px;background:#f4f5f0;color:#22362a}'
        'table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #ccd4c6}.scroll{overflow:auto}</style>'
        '<h1>Wildfire benchmark · CPU references</h1><p>Safety-first ordering; exposure is a synthetic outcome, not a death count.</p>'
        '<p>'+html.escape(f"Split: {summary['split']} · Profile: {summary['profile']} · Status: {summary['status']} · {len(summary['cases'])} episodes")+'</p>'
        '<div class="scroll"><table><tr><th>Policy</th><th>Scored / run</th><th>Safety success</th><th>Unexposed fraction</th><th>Additional footprint (ha)</th><th>Graded score</th></tr>'
        +''.join(rows)+'</table></div><p>Rank eligibility requires every attempted episode to be scoreable. '
        'See the JSON record for group-bootstrap intervals, censoring, invalid actions and the planned task list.</p>'
        '<p>One source landscape; held-out configurations do not establish geographic or operational generalization.</p>'
        '<a href="summary.json">Machine-readable results</a></html>')


def experiment_spec(environment, split, policies, identifiers, communications_ablation):
    from envs.wildfire_response.regional.benchmark_v1.environment import implementation_digest
    from envs.wildfire_response.regional.benchmark_v1.calibration import public_profile
    from envs.wildfire_response.regional.ops.generator import digest
    root=Path(__file__).resolve().parents[1]
    sources=('eval/run_benchmark.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/benchmark_v1/baselines.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/benchmark_v1/model.py')
    return dict(version='wildfire-benchmark-experiment-v1', split=split, task_ids=list(identifiers),
        policies=list(policies), communications_ablation=communications_ablation,
        catalog_sha256=environment.catalog['sha256'], environment_implementation_sha256=implementation_digest(),
        profile_sha256=digest(public_profile(environment._profile)),
        policy_and_runner_sha256={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources})


def main():
    from wildfire_core.benchmark_v1 import BenchmarkEnv
    parser=argparse.ArgumentParser(__doc__)
    for field in ('prepared-inputs','prepared-sha256','topographic-inputs','topographic-sha256','output'):
        parser.add_argument('--'+field,required=True)
    parser.add_argument('--split',choices=('development','train','validation','test'),default='development')
    parser.add_argument('--profile',choices=('central','faster_stronger','slower_weaker'),default='central')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--seconds',type=int,default=1800)
    parser.add_argument('--communications-ablation',action='store_true')
    parser.add_argument('--registration')
    parser.add_argument('--register-only',action='store_true')
    arguments=parser.parse_args()
    def factory():
        return BenchmarkEnv(arguments.prepared_inputs,arguments.topographic_inputs,
            expected_prepared_sha256=arguments.prepared_sha256,expected_topographic_sha256=arguments.topographic_sha256,
            profile=arguments.profile,allow_synthetic_conditions=True)
    if arguments.register_only:
        if Path(arguments.output).exists():
            parser.error('Registration output must be fresh')
        environment=factory()
        identifiers=task_ids(environment.catalog,arguments.split)
        if arguments.limit is not None:
            identifiers=identifiers[:arguments.limit]
        dump(Path(arguments.output),experiment_spec(environment,arguments.split,POLICIES,identifiers,
                                                   arguments.communications_ablation))
    else:
        run(arguments.output,factory,split=arguments.split,limit=arguments.limit,seconds=arguments.seconds,
            communications_ablation=arguments.communications_ablation,registration=arguments.registration)


if __name__=='__main__':
    main()
