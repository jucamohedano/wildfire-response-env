"""Compare v2 and v3 control on pinned, synthetic development cases.

Only aggregate progress is exported. Each simulator call is atomic; the wall
budget is checked after calls and verification, never marking an overrun passed.
"""
import argparse
import hashlib
import io
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

import pyarrow.parquet as pq

from eval.hub_runtime import make_environment
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from wildfire_control_v3.control import ControlCenterV3, interface_digest
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS

RELEASE_LOCK = Path(__file__).resolve().parents[1] / 'data/hub_runtime_v1/release.json'


class BudgetExpired(Exception):
    pass


def _budget_check(deadline):
    if time.monotonic() >= deadline:
        raise BudgetExpired


def _suite_status(cases, requested, total):
    if len(cases) != requested or any(case['status'] != 'passed' for case in cases):
        return 'incomplete'
    return 'passed' if requested == total else 'subset_passed'


def _write_stage(output, report):
    """Write a complete file beside the output without publishing its status."""
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                     prefix='.control-parity-', delete=False) as stream:
        try:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
            return Path(stream.name)
        except BaseException:
            Path(stream.name).unlink(missing_ok=True)
            raise


def _publish(output, report, deadline=None):
    stage = _write_stage(output, report)
    try:
        if deadline is not None:
            _budget_check(deadline)
        os.replace(stage, output)
    finally:
        stage.unlink(missing_ok=True)


def _reserve_output(output, report):
    """Claim a new result path without clobbering an existing file."""
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')


def _verified_release(data_dir, runtime, lock_path=RELEASE_LOCK):
    """Read immutable copies of the release-locked manifest and task table."""
    root = Path(data_dir)
    lock = json.loads(lock_path.read_text())
    manifest_bytes = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != lock['manifest_sha256']:
        raise ValueError('release manifest pin mismatch')
    manifest = json.loads(manifest_bytes)
    if (manifest['engine_implementation_sha256'] != lock['engine_implementation_sha256']
            or manifest['lifecycle_sha256'] != lock['lifecycle_sha256']):
        raise ValueError('release engine or lifecycle pin mismatch')
    records = [item for item in manifest['files'] if item.get('path') == 'tasks/development.parquet']
    if len(records) != 1:
        raise ValueError('release task table missing')
    task_bytes = (root / 'tasks/development.parquet').read_bytes()
    record = records[0]
    if len(task_bytes) != record['bytes'] or hashlib.sha256(task_bytes).hexdigest() != record['sha256']:
        raise ValueError('release task table pin mismatch')
    table = pq.read_table(io.BytesIO(task_bytes), columns=['task_id', 'runtime_id', 'scenario_sha256'])
    rows = [row for row in table.to_pylist() if row['runtime_id'] == runtime]
    pins = {row['task_id']: row['scenario_sha256'] for row in rows}
    if len(pins) != len(rows):
        raise ValueError('duplicate release task identities')
    return manifest, pins


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--runtime', choices=('fixture', 'es-ma-2021'), default='fixture')
    parser.add_argument('--allow-unapproved-data', action='store_true')
    parser.add_argument('--policy', choices=('passive', 'dispatcher'), default='passive')
    parser.add_argument('--limit', type=int, default=6)
    parser.add_argument('--wall-seconds', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 6 or not math.isfinite(args.wall_seconds) or args.wall_seconds <= 0 or args.output.exists():
        parser.error('Use a new output path, a finite positive budget and a limit between 1 and 6')
    start = time.monotonic()
    deadline = start + args.wall_seconds
    report = {'version': 'wildfire-control-v3-parity-v1', 'status': 'running',
              'runtime': args.runtime, 'policy': args.policy, 'workers': 1,
              'requested_cases': args.limit, 'total_active_cases': None, 'cases': []}

    def write():
        _publish(args.output, report)

    try:
        _reserve_output(args.output, report)
    except Exception:
        print('Could not reserve a new parity result path.', file=sys.stderr)
        return 1
    try:
        manifest, task_pins = _verified_release(args.data_dir, args.runtime)
        baseline, entry = make_environment(args.data_dir, args.runtime,
                                            allow_unapproved_data=args.allow_unapproved_data)
        candidate, other = make_environment(args.data_dir, args.runtime,
                                             allow_unapproved_data=args.allow_unapproved_data)
        _budget_check(deadline)
        if entry != other:
            raise ValueError('runtime identity mismatch')
        if set(task_pins) != set(entry['scenario_ids']) or entry != manifest['runtimes'][args.runtime]:
            raise ValueError('task identities differ from the verified package')
        _budget_check(deadline)
        control = ControlCenterV3(candidate)
        # Capture the v3 interface identity before any episode begins; the
        # verifier compares it again at finalization.
        interface_pin = interface_digest()
        report['control_interface_sha256'] = interface_pin
        report['total_active_cases'] = len(entry['scenario_ids'])

        class PublicRoutes:
            def route_options(self, resource_id, sector_id='incident'):
                return control.execute('route_options', {'resource_id': resource_id, 'sector_id': sector_id})

            def evacuation_options(self, group_id, destination_id):
                return control.execute('evacuation_options', {'group_id': group_id, 'destination_id': destination_id})

        for case in entry['scenario_ids'][:args.limit]:
            case_start, steps = time.monotonic(), 0
            try:
                _budget_check(deadline)
                initial, _ = baseline.reset(case)
                actual, _ = control.reset(case)
                _budget_check(deadline)
                if initial != actual:
                    raise AssertionError('initial observation mismatch')
                baseline_identity = baseline.trajectory()
                # The package pins the scenario hash, which includes the default
                # seed. The seed below is accepted only after that match.
                trusted = {'interface_version': 'wildfire-incident-day-v2', 'task_id': case,
                           'seed': baseline_identity['seed'], 'scenario_sha256': task_pins[case],
                           'inputs_sha256': entry['inputs_sha256'], 'network_sha256': entry['network_sha256'],
                           'implementation_sha256': manifest['engine_implementation_sha256']}
                if set(trusted) != IDENTITY_FIELDS or any(baseline_identity.get(k) != v for k, v in trusted.items()):
                    raise AssertionError('baseline identity mismatch')
                baseline_policy, candidate_policy = DayDispatcher(args.policy), DayDispatcher(args.policy)
                route_client = PublicRoutes()
                baseline_observation, candidate_observation = initial, actual
                while not baseline.terminated and not baseline.truncated:
                    _budget_check(deadline)
                    action = baseline_policy.action(baseline, baseline_observation)
                    candidate_action = candidate_policy.action(route_client, candidate_observation)
                    _budget_check(deadline)
                    if action != candidate_action:
                        raise AssertionError('reference policy actions differ')
                    observation, reward, terminated, truncated, info = baseline.step(action)
                    _budget_check(deadline)
                    if 'advance_minutes' in candidate_action:
                        result = control.execute('advance', {'max_minutes': candidate_action['advance_minutes']})
                    else:
                        result = control.execute('commit_orders', {'action': candidate_action})
                    _budget_check(deadline)
                    candidate_info = {k: v for k, v in result.get('info', {}).items() if k != 'validation'}
                    expected_info = {k: v for k, v in info.items() if k != 'outcomes'}
                    if 'validation' in result.get('info', {}):
                        candidate_info.pop('error', None)
                        expected_info.pop('error', None)
                    if (result.get('observation') != observation or result.get('terminated') != terminated
                            or result.get('truncated') != truncated or candidate_info != expected_info
                            or result.get('briefing', {}).get('reports', {}).get('delivered_count') != observation['report_count']):
                        raise AssertionError('public state, timing or briefing differs')
                    if not (terminated or truncated) and result.get('reward') != reward:
                        raise AssertionError('intermediate reward differs')
                    baseline_observation, candidate_observation = observation, result['observation']
                    steps += 1
                if not baseline.terminated or baseline.truncated:
                    report['cases'].append({'case': case, 'status': 'incomplete', 'reason': 'episode_not_completed',
                                            'minute': baseline.minute, 'steps': steps})
                    break
                if result.get('reward') is not None or result.get('verification_required') is not True:
                    raise AssertionError('terminal reward was not withheld')
                _budget_check(deadline)
                baseline_check = baseline.verify_episode()
                _budget_check(deadline)
                gate = control.finalize(expected_identity=trusted, expected_interface_sha256=interface_pin)
                _budget_check(deadline)
                if (not all(baseline_check.get(k) is True for k in
                            ('passed', 'exact_replay', 'independent_reward_agreement'))
                        or gate.get('status') != 'full_day' or gate.get('verified') is not True
                        or gate.get('score') != baseline_check['outcomes']['graded_score']
                        or reward != baseline_check['outcomes']['graded_score']):
                    raise AssertionError('independent terminal verification differs')
                report['cases'].append({'case': case, 'status': 'passed', 'minute': baseline.minute,
                                        'steps': steps, 'elapsed_seconds': round(time.monotonic() - case_start, 3)})
                write()
                _budget_check(deadline)
            except BudgetExpired:
                incomplete = {'case': case, 'status': 'incomplete', 'reason': 'wall_budget',
                              'minute': getattr(baseline, 'minute', None), 'steps': steps}
                if report['cases'] and report['cases'][-1]['case'] == case:
                    report['cases'][-1] = incomplete
                else:
                    report['cases'].append(incomplete)
                break
        _budget_check(deadline)
        report['status'] = _suite_status(report['cases'], args.limit, report['total_active_cases'])
    except BudgetExpired:
        report['status'] = 'incomplete'
    except KeyboardInterrupt:
        report['status'] = 'incomplete'
    except Exception:
        # Exceptions can contain private evaluator or source details; keep them
        # out of public result files and the command's external output.
        report['status'] = 'failed'
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    try:
        if report['status'] in ('passed', 'subset_passed'):
            try:
                # Keep the visible report at running/incomplete until after the
                # final staged file has been written and the budget rechecked.
                _publish(args.output, report, deadline=deadline)
            except BudgetExpired:
                report['status'] = 'incomplete'
                report['reason'] = 'wall_budget'
                report['elapsed_seconds'] = round(time.monotonic() - start, 3)
                write()
        else:
            write()
    except Exception:
        print('Could not publish a parity result.', file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0 if report['status'] in ('passed', 'subset_passed') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('Parity check failed; no successful result was recorded.', file=sys.stderr)
        raise SystemExit(1)
