"""Run synthetic incident-day CPU acceptance with resumable private checkpoints."""
import argparse
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.scenarios import CASE_IDS


VERSION = 'incident-day-cpu-acceptance-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def atomic_write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path, value):
    atomic_write(path, canonical(value) + b'\n')


def write_gzip(path, value):
    atomic_write(path, gzip.compress(canonical(value) + b'\n', mtime=0))


def write_report(output, report):
    write_json(output / 'report.json', report)
    rows = []
    for result in report['cases']:
        score = result.get('score')
        rows.append('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            html.escape(result['phase']), html.escape(result['case_id']),
            html.escape(result['status']), html.escape(str(score)) if score is not None else '—'))
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Incident-day CPU acceptance</title><body>'
            '<h1>Incident-day CPU acceptance</h1><p>Synthetic fixture only. Real data approval: BLOCKED.</p>'
            '<p>Status: {}</p><table><thead><tr><th>Phase</th><th>Case</th><th>Status</th>'
            '<th>Score</th></tr></thead><tbody>{}</tbody></table></body></html>').format(
                html.escape(report['status']), ''.join(rows))
    atomic_write(output / 'report.html', page.encode('utf-8'))


def peak_memory():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def audit_phenomena(audit):
    """Measure only phenomena present in the private minute-by-minute audit."""
    if (type(audit) is not list or len(audit) != 1441
            or any(record.get('minute') != minute for minute, record in enumerate(audit))):
        raise ValueError('Complete 1,440-minute audit coverage is required')
    initial = audit[0]
    if not initial.get('footprint') or not initial.get('population'):
        raise ValueError('Audit must contain an inherited fire and population')
    initial_exposed = sum(group['exposed'] for group in initial['population'].values())
    final_exposed = sum(group['exposed'] for group in audit[-1]['population'].values())
    new_cells = {cell for record in audit[1:] for cell in record['new_footprint']}
    if new_cells.intersection(initial['footprint']) or final_exposed < initial_exposed:
        raise ValueError('Audit contains inconsistent fire or exposure evidence')
    return dict(inherited_burning_footprint_cells=len(initial['footprint']),
                new_burned_cells=len(new_cells), new_exposed_population=final_exposed - initial_exposed,
                resource_behavior='not_observable_in_audit')


def _new_environment(inputs, network):
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'])


def _save_checkpoint(env, policy, checkpoint, report, index, elapsed, case_elapsed):
    temporary = checkpoint.with_name(checkpoint.name + '.tmp')
    env.save_checkpoint(temporary)
    os.replace(temporary, checkpoint)
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    metadata = dict(version=VERSION, checkpoint_file_sha256=checkpoint_hash,
                    inputs_sha256=report['inputs_sha256'], network_sha256=report['network_sha256'],
                    policy=report['policy'], case_index=index,
                    output_dir=str(checkpoint.parent.parent.resolve()), elapsed_seconds=elapsed,
                    case_elapsed_seconds=case_elapsed,
                    completed_cases_sha256=hashlib.sha256(canonical(report['cases'])).hexdigest(),
                    dispatcher_asked=[list(item) for item in sorted(policy.asked)])
    write_json(Path(str(checkpoint) + '.runner.json'), metadata)


def _load_checkpoint(path, report, output):
    metadata = json.loads(Path(str(path) + '.runner.json').read_text())
    raw = path.read_bytes()
    if (metadata.get('version') != VERSION or metadata.get('checkpoint_file_sha256') != hashlib.sha256(raw).hexdigest()
            or metadata.get('inputs_sha256') != report['inputs_sha256']
            or metadata.get('network_sha256') != report['network_sha256']
            or metadata.get('policy') != report['policy'] or metadata.get('output_dir') != str(output.resolve())
            or metadata.get('completed_cases_sha256') != hashlib.sha256(canonical(report['cases'])).hexdigest()
            or type(metadata.get('case_index')) is not int or metadata['case_index'] != len(report['cases'])):
        raise ValueError('Checkpoint, progress report, policy or fixture identity differs')
    return json.loads(gzip.decompress(raw)), metadata


def run(output, *, policy_name='dispatcher', wall_budget=None, resume=None, clock=time.perf_counter):
    """Run the measured first case, then the six-case single-worker suite."""
    if policy_name not in ('passive', 'dispatcher'):
        raise ValueError('Invalid policy')
    if wall_budget is not None and (not isinstance(wall_budget, (int, float)) or isinstance(wall_budget, bool)
                                    or not 0 <= wall_budget < float('inf')):
        raise ValueError('Wall budget must be a finite nonnegative number of seconds')
    started = clock()
    output = Path(output).absolute()
    inputs, network = fixture_inputs()
    if inputs.get('approval_status') != 'approved_synthetic_fixture':
        raise ValueError('Only an approved synthetic fixture can run; real data gate is blocked')
    plan = [('measurement', CASE_IDS[0])] + [('batch', identifier) for identifier in CASE_IDS]
    checkpoint = output / 'private' / 'checkpoint.json.gz'
    if resume is None:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / 'private').mkdir(mode=0o700)
        report = dict(version=VERSION, status='incomplete', data_scope='synthetic_fixture_only',
                      real_source_pack='BLOCKED', workers=1, policy=policy_name,
                      inputs_sha256=inputs['sha256'], network_sha256=network['sha256'],
                      implementation_sha256=implementation_digest(),
                      audit_coverage=dict(cases_with_fire_growth=0, cases_with_new_exposure=0,
                                          resource_behavior='not_observable_in_audit'),
                      cases=[], elapsed_seconds=0., peak_rss_bytes=peak_memory())
        write_report(output, report)
        restored = None
        prior_elapsed = 0.
    else:
        resume = Path(resume).absolute()
        if resume != checkpoint.absolute():
            raise ValueError('Resume checkpoint must belong to this output directory')
        report = json.loads((output / 'report.json').read_text())
        if (report.get('version') != VERSION or report.get('status') != 'incomplete'
                or report.get('inputs_sha256') != inputs['sha256']
                or report.get('network_sha256') != network['sha256'] or report.get('policy') != policy_name
                or report.get('implementation_sha256') != implementation_digest()):
            raise ValueError('Progress report does not match this fixture or policy')
        restored, metadata = _load_checkpoint(resume, report, output)
        prior_elapsed = metadata['elapsed_seconds']
    interrupted = False

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True

    old_handlers = {name: signal.signal(name, interrupt) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        for index in range(len(report['cases']), len(plan)):
            phase, case_id = plan[index]
            env = _new_environment(inputs, network)
            dispatcher = DayDispatcher(policy=policy_name)
            case_started = clock()
            previous_case_elapsed = 0.
            if restored is not None:
                observation = env.restore(restored)
                dispatcher.asked = {tuple(item) for item in metadata['dispatcher_asked']}
                previous_case_elapsed = metadata['case_elapsed_seconds']
                if env.trajectory()['task_id'] != case_id:
                    raise ValueError('Checkpoint scenario differs from the planned case')
                restored = None
            else:
                observation, unused_info = env.reset(case_id)
            while not env.terminated and not env.truncated:
                if interrupted or (wall_budget is not None and clock() - started >= wall_budget):
                    break
                action = dispatcher.action(env, observation)
                observation, unused_reward, unused_terminated, unused_truncated, unused_info = env.step(action)
            elapsed = prior_elapsed + clock() - started
            if interrupted or (wall_budget is not None and clock() - started >= wall_budget):
                _save_checkpoint(env, dispatcher, checkpoint, report, index, elapsed,
                                 previous_case_elapsed + clock() - case_started)
                report.update(status='incomplete', elapsed_seconds=elapsed, peak_rss_bytes=peak_memory(),
                              interruption='signal' if interrupted else 'wall_budget',
                              current_case=dict(phase=phase, case_id=case_id, minute=env.minute),
                              checkpoint=str(checkpoint))
                write_report(output, report)
                return report
            simulation_seconds = previous_case_elapsed + clock() - case_started
            result = dict(phase=phase, case_id=case_id, status='incomplete', minute=env.minute,
                          simulation_seconds=simulation_seconds, peak_rss_bytes=peak_memory())
            case_dir = output / 'private' / f'{index:02d}-{case_id}'
            case_dir.mkdir(mode=0o700, exist_ok=True)
            write_gzip(case_dir / 'trajectory.json.gz', env.trajectory())
            write_gzip(case_dir / 'audit.json.gz', env._audit)
            verification_started = clock()
            try:
                verification = env.verify_episode()
                if (verification.get('passed') is not True or verification.get('exact_replay') is not True
                        or verification.get('independent_reward_agreement') is not True):
                    raise ValueError('Exact replay and independent reward verification failed')
                if report['implementation_sha256'] != implementation_digest():
                    raise ValueError('Incident-day implementation changed during acceptance')
            except (ValueError, RuntimeError, KeyError) as error:
                result.update(status='failed', verification='failed', error=str(error),
                              verification_seconds=clock() - verification_started)
                report['cases'].append(result)
                report.update(status='failed', elapsed_seconds=prior_elapsed + clock() - started,
                              peak_rss_bytes=peak_memory())
                write_report(output, report)
                return report
            result['verification_seconds'] = clock() - verification_started
            result['elapsed_seconds'] = simulation_seconds + result['verification_seconds']
            result['peak_rss_bytes'] = peak_memory()
            outcome = verification['outcomes']
            if (env.terminated and not env.truncated and env.minute == 1440
                    and outcome.get('completed_minutes') == 1440 and outcome.get('score_eligible') is True):
                try:
                    phenomena = audit_phenomena(env._audit)
                except (ValueError, KeyError, TypeError) as error:
                    result.update(status='failed', verification='failed', error=str(error))
                else:
                    write_gzip(case_dir / 'phenomena.json.gz', phenomena)
                    if phase == 'batch':
                        report['audit_coverage']['cases_with_fire_growth'] += int(phenomena['new_burned_cells'] > 0)
                        report['audit_coverage']['cases_with_new_exposure'] += int(phenomena['new_exposed_population'] > 0)
                    result.update(status='complete', score=outcome['graded_score'], verification='passed')
            else:
                result['verification'] = 'passed_incomplete_episode'
            report['cases'].append(result)
            report.update(elapsed_seconds=prior_elapsed + clock() - started, peak_rss_bytes=peak_memory())
            report.pop('current_case', None)
            report.pop('interruption', None)
            report.pop('checkpoint', None)
            write_report(output, report)
            if result['status'] != 'complete':
                report['status'] = 'failed'
                write_report(output, report)
                return report
        coverage = report['audit_coverage']
        report['coverage_checks'] = dict(fire_growth=coverage['cases_with_fire_growth'] > 0,
                                         passive_population_exposure=(coverage['cases_with_new_exposure'] > 0
                                                                      if policy_name == 'passive' else None),
                                         resource_behavior='unverified')
        coverage_passed = (report['coverage_checks']['fire_growth']
                           and report['coverage_checks']['passive_population_exposure'] is not False)
        report['status'] = ('complete' if coverage_passed
                            and report['implementation_sha256'] == implementation_digest() else 'failed')
        write_report(output, report)
        return report
    finally:
        for name, handler in old_handlers.items():
            signal.signal(name, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policy', choices=('passive', 'dispatcher'), default='dispatcher')
    parser.add_argument('--wall-budget', type=float, metavar='SECONDS')
    parser.add_argument('--resume', type=Path, help='Private checkpoint from this output directory')
    options = parser.parse_args()
    try:
        report = run(options.output, policy_name=options.policy, wall_budget=options.wall_budget,
                     resume=options.resume)
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(2, f'Acceptance failed: {error}\n')
    print(json.dumps(dict(status=report['status'], completed_cases=len(report['cases']),
                          elapsed_seconds=report['elapsed_seconds'], checkpoint=report.get('checkpoint'))))
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
