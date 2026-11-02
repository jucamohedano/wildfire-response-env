"""Resumable CPU-only evaluation of 24 configurations with two scripted policies.

Private evidence and checkpoints stay under private/. Evaluation is not training,
independent historical incidents, or evidence of physical calibration.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import signal
import time

from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_core.catalog_lifecycle import registry
from eval.hub_runtime import make_environment, verify_package
from eval.incident_day_action_checks import audit_runtime
from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import make_policy
from eval.run_incident_day import peak_memory, write_gzip, write_json
from eval.validate_source_runtime_matrix import (
    PublicFacade, commit, checksum, aggregate_findings, duration_statistics,
    uninspectable_coverage, uninspectable_findings, _continuation, _query_independence,
)

VERSION = 'wildfire-packaged-evaluation-v1'
CATALOG_ID = 'incident_day_evaluation_v1'
POLICIES = ('passive', 'dispatcher')
ROOT = Path(__file__).resolve().parents[1]


def validator_digest():
    paths = [Path(__file__), *(ROOT / 'eval' / name for name in (
        'hub_runtime.py', 'validate_source_runtime_matrix.py', 'incident_day_policies.py',
        'incident_day_action_checks.py', 'incident_day_movement_checks.py',
        'check_incident_day_plausibility.py', 'run_incident_day.py')),
        ROOT / 'data/incident_day_v2/plausibility/bounds-v1.json',
        ROOT / 'data/incident_day_v2/plausibility/action-bounds-v1.json']
    return checksum({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in paths})


def expected_cells(manifest, runtime_id='es-ma-2021'):
    entry = manifest['runtimes'].get(runtime_id)
    if entry is None:
        raise ValueError('Unknown evaluation runtime')
    catalogs = [catalog for catalog in registry()['catalogs']
                if catalog.get('id') == CATALOG_ID and catalog.get('status') == 'active_evaluation'
                and (catalog.get('inputs_sha256'), catalog.get('network_sha256')) ==
                    (entry['inputs_sha256'], entry['network_sha256'])]
    ids = entry.get('evaluation_scenario_ids')
    if (len(catalogs) != 1 or type(ids) is not list or len(ids) != 24
            or any(type(identifier) is not str for identifier in ids)
            or len(set(ids)) != 24 or set(ids) != set(catalogs[0]['case_ids'])
            or set(ids) & set(entry['scenario_ids'] + entry.get('additional_scenario_ids', []))):
        raise ValueError('Runtime must declare the exact 24-case evaluation catalog')
    return [dict(runtime_id=runtime_id, case_id=identifier, policy=policy)
            for identifier in sorted(ids) for policy in POLICIES]


def _identity(cell):
    return tuple(cell[key] for key in ('runtime_id', 'case_id', 'policy'))


def refresh_report(report):
    cells, expected = report['cells'], report['expected_cells']
    if (len(cells) != 48 or [_identity(cell) for cell in cells] != [_identity(cell) for cell in expected]
            or len(set(map(_identity, expected))) != 48
            or len({cell['case_id'] for cell in expected}) != 24
            or len({cell['runtime_id'] for cell in expected}) != 1
            or {_identity(cell) for cell in expected} != {
                (expected[0]['runtime_id'], case_id, policy)
                for case_id in {cell['case_id'] for cell in expected} for policy in POLICIES}):
        raise ValueError('Evaluation domain is incomplete or duplicated')
    statuses = {cell['status'] for cell in cells}
    if not statuses <= {'not_run', 'incomplete', 'complete', 'blocked', 'failed'}:
        raise ValueError('Unknown evaluation cell status')
    for cell in cells:
        if cell['status'] != 'complete':
            continue
        if (cell.get('minute') != 1440 or cell.get('split') != 'evaluation'
                or cell.get('training_eligible') is not False
                or not all(cell.get('verification', {}).get(key) is True for key in (
                    'passed', 'exact_replay', 'independent_reward_agreement'))
                or cell.get('outcome', {}).get('score_eligible') is not True
                or cell.get('query_independence', {}).get('passed') is not True
                or not cell.get('resume_checks')
                or any(check.get('passed') is not True for check in cell['resume_checks'])
                or any(item.get('category') in ('error', 'blocking', 'contradiction') for item in cell.get('findings', []))):
            statuses.add('failed')
        elif (uninspectable_findings(cell.get('findings', []))
              or uninspectable_coverage(cell.get('operation_coverage', {}))):
            statuses.add('blocked')
    report['status'] = ('failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses
                        else 'incomplete' if statuses & {'not_run', 'incomplete'} else 'complete')
    report['summary'] = dict(expected=48, evaluated=sum(cell['status'] in ('complete', 'blocked', 'failed')
        for cell in cells), statuses={status: sum(cell['status'] == status for cell in cells)
        for status in sorted(statuses)})
    return report


def write_report(output, report):
    refresh_report(report)
    write_json(Path(output) / 'report.json', report)


def _save(output, report, index, live):
    refresh_report(report)
    data = dict(version=VERSION, pins=report['pins'], expected_sha256=checksum(report['expected_cells']),
                next_index=index, live=live, progress_report=copy.deepcopy(report))
    data['sha256'] = checksum(data)
    write_gzip(Path(output) / 'private/checkpoint.json.gz', data)
    write_report(output, report)


def load_progress(output, expected, pins):
    with gzip.open(Path(output) / 'private/checkpoint.json.gz', 'rt') as stream:
        data = json.load(stream)
    if data.get('sha256') != checksum({key: value for key, value in data.items() if key != 'sha256'}):
        raise ValueError('Evaluation checkpoint integrity differs')
    report, index, live = data['progress_report'], data['next_index'], data['live']
    if (data.get('version') != VERSION or data.get('pins') != pins or report.get('pins') != pins
            or report.get('version') != VERSION or report.get('split') != 'evaluation'
            or report.get('training_eligible') is not False
            or data.get('expected_sha256') != checksum(expected) or report['expected_cells'] != expected
            or type(index) is not int or not 0 <= index <= len(expected)):
        raise ValueError('Evaluation resume pins, domain or progress identity differs')
    refresh_report(report)
    if any(cell['status'] not in ('complete', 'blocked', 'failed') for cell in report['cells'][:index]):
        raise ValueError('Resume would skip an unfinished cell')
    if any(cell['status'] != 'not_run' for cell in report['cells'][index + (live is not None):]):
        raise ValueError('Resume would overwrite evaluated cells')
    if live is not None:
        if (index >= len(expected) or live.get('identity') != expected[index]
                or live['policy'].get('policy') != expected[index]['policy']
                or live['environment']['state']['_scenario']['id'] != expected[index]['case_id']
                or report['cells'][index]['status'] != 'incomplete'):
            raise ValueError('Live evaluation checkpoint has a different case or policy')
    return report, index, live


def _live(identity, env, policy, observer, calls, checked, resume_checks, query_check, elapsed_seconds=0):
    return dict(identity=identity, environment=env.checkpoint(), policy=policy.checkpoint(),
                movement_observer=observer.snapshot(), calls=calls, checked=sorted(checked),
                resume_checks=resume_checks, query_independence=query_check, cell_elapsed_seconds=elapsed_seconds)


def finish_cell(identity, env, observer, calls, resume_checks, query_check):
    findings = []
    try:
        verification = env.verify_episode()
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        verification = dict(passed=False, exact_replay=False, independent_reward_agreement=False)
        findings.append(dict(code='verification_failed', category='error', detail=str(error)))
    checks = audit_runtime(env, calls)
    findings.extend(checks['findings'])
    movement = observer.snapshot()
    for code, item in movement['findings'].items():
        findings.append(dict(code=code, category='contradiction', count=item['count'],
                            detail=item['first']['detail'], resource=item['first']['entity']))
    coverage = copy.deepcopy(checks['coverage'])
    unverified = sum(movement['coverage'].get(key, 0) for key in (
        'journey_unverifiable', 'position_unverifiable', 'multi_minute_ticks_unobserved',
        'road_geometry_unverifiable'))
    coverage['movement_paths'] = dict(status='unsupported' if unverified else 'complete' if
        movement['coverage'].get('timed_positions_checked', 0) else 'not_exercised',
        observed=movement['coverage'].get('timed_positions_checked', 0))
    coverage['capacity_each_tick'] = dict(status='complete' if
        movement['coverage'].get('capacity_ticks_checked') == env.minute else 'unsupported',
        observed=movement['coverage'].get('capacity_ticks_checked', 0))
    hard = any(item['category'] in ('contradiction', 'blocking', 'error') for item in findings)
    unsupported = uninspectable_findings(findings)
    verified = all(verification.get(key) is True for key in (
        'passed', 'exact_replay', 'independent_reward_agreement'))
    checks_passed = (bool(resume_checks) and all(check['passed'] is True for check in resume_checks)
                     and query_check.get('passed') is True)
    outcome = env.outcomes()
    status = ('failed' if hard or not verified or not checks_passed else 'blocked' if
        unsupported or unverified or uninspectable_coverage(coverage) or not env.terminated
        or env.truncated or env.minute != 1440 or outcome.get('score_eligible') is not True else 'complete')
    result = dict(identity, status=status, minute=env.minute, split='evaluation', training_eligible=False,
        verification={key: verification.get(key) for key in ('passed', 'exact_replay', 'independent_reward_agreement')},
        outcome=outcome, score=outcome.get('graded_score') if status == 'complete' else None,
        findings=aggregate_findings(findings), operation_coverage=coverage, movement_evidence=movement['coverage'],
        resume_checks=resume_checks, query_independence=query_check, command_count=env.decision,
        tool_call_count=len(calls), durations=duration_statistics(checks['durations']),
        pins=dict(inputs_sha256=env.inputs['sha256'], network_sha256=env.network.sha256,
                  scenario_sha256=env._scenario['sha256']))
    private = dict(calls=calls, audit=env._audit, trajectory=env.trajectory(), reports=env.bus.reports,
        orders=list(env.ops.orders.values()), verification=verification, findings=findings,
        movement_observer=movement, resume_checks=resume_checks, outcome=outcome,
        evaluation_metadata=copy.deepcopy(env._scenario.get('evaluation', {})),
        episode_seed=env._scenario.get('seed'))
    return result, private


def run(data_dir, output, *, runtime_id='es-ma-2021', wall_seconds=1800, max_cells=None,
        max_commands=None, resume=False, expected_implementation_sha256=None,
        expected_lifecycle_sha256=None, expected_package_sha256=None, expected_validator_sha256=None):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    for name, value in (('max_cells', max_cells), ('max_commands', max_commands)):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(name + ' must be positive')
    manifest = verify_package(data_dir)
    expected = expected_cells(manifest, runtime_id)
    pins = dict(implementation_sha256=implementation_digest(), lifecycle_sha256=registry()['sha256'],
                package_sha256=checksum(manifest), validator_sha256=validator_digest())
    requested = dict(implementation_sha256=expected_implementation_sha256,
        lifecycle_sha256=expected_lifecycle_sha256, package_sha256=expected_package_sha256,
        validator_sha256=expected_validator_sha256)
    for key, value in requested.items():
        if value is not None and value != pins[key]:
            raise ValueError('Expected ' + key + ' differs')
    def freeze():
        if (implementation_digest() != pins['implementation_sha256'] or registry()['sha256'] != pins['lifecycle_sha256']
                or validator_digest() != pins['validator_sha256'] or checksum(verify_package(data_dir)) != pins['package_sha256']):
            raise ValueError('Evaluation code, lifecycle or package changed during execution')
    output = Path(output)
    if output.is_symlink():
        raise ValueError('Output must not be a symlink')
    if resume:
        report, index, saved = load_progress(output, expected, pins)
    else:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / 'private').mkdir(mode=0o700)
        report = dict(version=VERSION, split='evaluation', training_eligible=False, pins=pins,
            expected_cells=expected, cells=[dict(cell, status='not_run') for cell in expected],
            policies=list(POLICIES), workers=1, elapsed_seconds=0, peak_rss_bytes=peak_memory(),
            limitations=['Shared development landscape and fire seeds; configurations are not independent incidents.',
                'No training, LLM calls, physical calibration or real operational access validation.',
                'Wall budget and signals are handled between atomic public calls and verification.'])
        index, saved = 0, None
    start, prior_elapsed = time.perf_counter(), report['elapsed_seconds']
    interrupted = False
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    completed, commands = 0, 0
    def persist(live):
        report['elapsed_seconds'] = prior_elapsed + time.perf_counter() - start
        report['peak_rss_bytes'] = peak_memory()
        freeze()
        _save(output, report, index, live)
        if live is not None:
            print(json.dumps(dict(index=index + 1, total=len(expected), **live['identity'],
                status='incomplete', minute=live['environment']['state']['minute'],
                elapsed_seconds=round(live['cell_elapsed_seconds'], 3))), flush=True)
    try:
        if saved is None:
            persist(None)
        while index < len(expected):
            if (interrupted or time.perf_counter() - start >= wall_seconds
                    or (max_commands is not None and commands >= max_commands)):
                persist(saved)
                return report
            freeze()
            identity = expected[index]
            cell_start = time.perf_counter()
            carried_elapsed = saved.get('cell_elapsed_seconds', 0) if saved is not None else 0
            env, entry = make_environment(data_dir, runtime_id, allow_unapproved_data=True)
            if entry != manifest['runtimes'][runtime_id]:
                raise ValueError('Evaluation runtime changed')
            policy = make_policy(identity['policy'])
            if saved is None:
                observation, _ = env.reset(identity['case_id'])
                calls, checked, resume_checks = [], set(), []
                query_check = _query_independence(env)
                observer = MovementObserver(env)
                public = PublicFacade(env, calls)
                public.get_situation(); public.get_reports(limit=20)
            else:
                observation = env.restore(saved['environment'])
                policy.restore(saved['policy'])
                observer = MovementObserver(env); observer.restore(saved['movement_observer'])
                calls, checked = saved['calls'], set(saved['checked'])
                resume_checks, query_check = saved['resume_checks'], saved['query_independence']
                public = PublicFacade(env, calls)
                saved = None
            def live():
                elapsed = carried_elapsed + time.perf_counter() - cell_start
                report['cells'][index]['elapsed_seconds'] = elapsed
                return _live(identity, env, policy, observer, calls, checked, resume_checks, query_check, elapsed)
            report['cells'][index] = dict(identity, status='incomplete', minute=env.minute)
            persist(live())
            last_hour = env.minute // 60
            while not (env.terminated or env.truncated):
                if interrupted or time.perf_counter() - start >= wall_seconds or (max_commands is not None and commands >= max_commands):
                    report['cells'][index]['minute'] = env.minute
                    report['cells'][index]['reason'] = ('signal' if interrupted else 'command_budget' if
                        max_commands is not None and commands >= max_commands else 'wall_budget')
                    persist(live())
                    return report
                phases = {unit['state'] for unit in env.ops.resources.values()}
                boundaries = sorted((phases & {'travel', 'on_scene', 'working', 'returning', 'refilling', 'resting'}) - checked)
                label = boundaries[0] if boundaries else 'initial' if not checked else None
                continuation = _continuation(env, policy) if label else None
                action = policy.action(public, observation)
                result = commit(public, action)
                if 'observation' not in result:
                    raise ValueError('Scripted evaluation tool rejected: ' + str(result))
                observation = result['observation']; commands += 1
                if continuation:
                    passed = (action == continuation['action'] and checksum(result) == continuation['result']
                        and checksum(env._audit) == continuation['audit']
                        and checksum(env.fire.checkpoint()) == continuation['fire']
                        and checksum(policy.checkpoint()) == continuation['policy'])
                    resume_checks.append(dict(boundary=label, minute=env.minute, passed=passed)); checked.add(label)
                    if not passed:
                        raise ValueError('Evaluation checkpoint continuation differs')
                if env.minute // 60 > last_hour:
                    last_hour = env.minute // 60
                    report['cells'][index]['minute'] = env.minute
                    persist(live())
            freeze()
            result, private = finish_cell(identity, env, observer, calls, resume_checks, query_check)
            result['elapsed_seconds'] = carried_elapsed + time.perf_counter() - cell_start
            write_gzip(output / 'private' / f"{runtime_id}-{identity['case_id']}-{identity['policy']}.json.gz", private)
            report['cells'][index] = result
            index += 1; completed += 1
            persist(None)
            print(json.dumps(dict(index=index, total=len(expected), **identity, status=result['status'],
                minute=env.minute, elapsed_seconds=round(result['elapsed_seconds'], 3),
                score=result['score'])), flush=True)
            if max_cells is not None and completed >= max_cells:
                return report
        return report
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--runtime', default='es-ma-2021')
    parser.add_argument('--wall-seconds', type=float, default=1800)
    parser.add_argument('--max-cells', type=int); parser.add_argument('--max-commands', type=int)
    parser.add_argument('--resume', action='store_true')
    for kind in ('implementation', 'lifecycle', 'package', 'validator'):
        parser.add_argument('--expected-' + kind + '-sha256')
    args = parser.parse_args()
    report = run(args.data_dir, args.output, runtime_id=args.runtime, wall_seconds=args.wall_seconds,
        max_cells=args.max_cells, max_commands=args.max_commands, resume=args.resume,
        **{f'expected_{kind}_sha256': getattr(args, f'expected_{kind}_sha256')
           for kind in ('implementation', 'lifecycle', 'package', 'validator')})
    print(json.dumps(dict(status=report['status'], summary=report['summary'], pins=report['pins'])))
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
