"""Independent 48-cell pre-wait crash-recovery check for active v3 exercises.

This is not the full-day runtime matrix, a restartable matrix runner, or evidence
about an archived trajectory. Each policy selects an actual wait using public
observations. At least two minutes must elapse, and the worker dies after the
first tick. No refill coverage or checkpoint inside the wait is claimed.
Run with immutable sources from before import and a separate private workspace.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

from eval import validate_population_v3_runtime as matrix

VERSION = 'wait-recovery-active-matrix-v2'
FAULTS = ('after_prepare', 'during_wait', 'after_execute', 'after_commit')
CRASH_EXIT = 73
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_reference(path, expected_sha):
    path = Path(path)
    if not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha):
        raise ValueError('Reference SHA-256 is required')
    if path.is_symlink() or not path.is_file():
        raise ValueError('Reference must be a regular file')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('Reference identity differs')
    return json.loads(raw)


def reference_cells(reference, expected):
    if reference.get('version') != matrix.VERSION or reference.get('expected_cells') != expected:
        raise ValueError('Reference domain differs')
    cells = reference.get('cells')
    if not isinstance(cells, list) or len(expected) != 48 or len(cells) != 48:
        raise ValueError('Reference domain is incomplete')
    try:
        keyed = {matrix.cell_identity(cell): cell for cell in cells}
        if (len(keyed) != 48 or set(keyed) != {matrix.cell_identity(cell) for cell in expected}
                or any(c.get('status') not in ('complete', 'blocked', 'failed') for c in cells)):
            raise ValueError('Reference domain differs')
        for cell in cells:
            pins = cell.get('pins')
            if not isinstance(pins, dict) or any(not isinstance(pins.get(k), str)
                    or re.fullmatch(r'[0-9a-f]{64}', pins[k]) is None
                    for k in ('inputs_sha256', 'network_sha256', 'scenario_sha256')):
                raise ValueError('Reference case pins missing')
    except (KeyError, TypeError):
        raise ValueError('Reference domain differs') from None
    return keyed


def control_for(data_dir, runtime):
    from wildfire_control_v3.control import ControlCenterV3
    env, _ = matrix.make_v3(data_dir, runtime)
    return ControlCenterV3(env)


def select_wait(data_dir, cell, reference, check=lambda: None):
    """Selection uses only policy actions and public results, never hidden phases."""
    control = control_for(data_dir, cell['runtime_id'])
    env = control.environment
    observation, _ = env.reset(cell['case_id'])
    pins = reference['pins']
    if (env.inputs['sha256'] != pins['inputs_sha256'] or env.network.sha256 != pins['network_sha256']
            or env._scenario['sha256'] != pins['scenario_sha256']):
        raise ValueError('Case identity differs')
    policy = matrix.make_policy(cell['policy'])
    calls = []
    public = matrix.PublicFacade(env, calls)
    public.get_situation(); public.get_reports(limit=20)
    while not (env.terminated or env.truncated):
        check()
        start = observation['minute']
        action = policy.action(public, observation)
        is_wait = (isinstance(action, dict) and set(action) == {'advance_minutes'}
                   and type(action['advance_minutes']) is int and 2 <= action['advance_minutes'] <= 60)
        checkpoint = control.private_checkpoint() if is_wait else None
        policy_state = policy.checkpoint() if is_wait else None
        result = matrix.commit(public, action)
        if not isinstance(result, dict) or not isinstance(result.get('observation'), dict):
            raise ValueError('Policy action rejected')
        observation = result['observation']
        if is_wait and observation['minute'] - start >= 2:
            return dict(cell=copy.deepcopy(cell), checkpoint=checkpoint, policy_state=policy_state,
                        action=copy.deepcopy(action), public_result=result,
                        engine_checkpoint=env.checkpoint(), start_minute=start)
    raise ValueError('No multi-minute policy wait was observed')


def assert_completed_wait(result, checkpoint, setup):
    """Require a completed wait identical to the contemporaneous control."""
    try:
        observation = result['observation']
        core = checkpoint['engine_checkpoint']['core']['state']
        minute = observation['minute']
        if (not isinstance(result, dict) or result.get('execution') == 'indeterminate'
                or result.get('committed') is False or not isinstance(observation, dict)
                or type(minute) is not int or minute < setup['start_minute'] + 2
                or core['minute'] != minute or not core['_actions']
                or core['_actions'][-1]['action'] != {'advance_minutes': setup['max_minutes']}
                or matrix.checksum(result) != setup['expected_result_sha256']
                or matrix.checksum(checkpoint) != setup['expected_checkpoint_sha256']):
            raise ValueError()
    except (TypeError, ValueError, KeyError):
        raise ValueError('Completed wait evidence differs') from None


def save_completed_wait(directory, result, checkpoint, setup):
    """Write private proof before exiting at the after-execution crash point."""
    assert_completed_wait(result, checkpoint, setup)
    evidence = {'result': result, 'checkpoint': checkpoint}
    fd = os.open(directory / 'completed-wait.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(evidence, file, sort_keys=True, allow_nan=False)
        file.flush(); os.fsync(file.fileno())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def crash_worker(data_dir, directory, fault):
    from eval.wait_recovery_v1 import WaitRecoveryV1
    setup = json.loads((directory / 'setup.json').read_text())
    control = control_for(data_dir, setup['runtime_id'])
    control.restore_private_checkpoint(setup['checkpoint'])
    driver = WaitRecoveryV1(control, directory, run_identity=setup['identity'])
    write = driver._write
    def interrupted_write(fd, record):
        write(fd, record)
        if ((fault == 'after_prepare' and record['phase'] == 'pending') or
                (fault == 'after_commit' and record['phase'] == 'complete')):
            os._exit(CRASH_EXIT)
    driver._write = interrupted_write
    execute = control.execute
    def interrupted_execute(name, arguments):
        if fault == 'during_wait':
            env = control.environment
            tick = env._tick
            def interrupted_tick():
                result = tick()
                if env.minute != setup['start_minute'] + 1:
                    raise ValueError('Unexpected tick')
                fd = os.open(directory / 'tick.marker', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as marker:
                    marker.write(str(env.minute)); marker.flush(); os.fsync(marker.fileno())
                os._exit(CRASH_EXIT)
            env._tick = interrupted_tick
        result = execute(name, arguments)
        if fault == 'after_execute':
            save_completed_wait(directory, result, control.private_checkpoint(), setup)
            os._exit(CRASH_EXIT)
        return result
    control.execute = interrupted_execute
    driver.start(1, setup['max_minutes'], initialize=True)
    raise RuntimeError('Requested crash was not reached')


def verify_crash(directory, fault, setup):
    try:
        record = json.loads((directory / 'wait-recovery-v1.json').read_text())['record']
        expected_phase = 'complete' if fault == 'after_commit' else 'pending'
        if (record['phase'] != expected_phase or type(record['sequence']) is not int
                or record['sequence'] != 1 or record['run_identity'] != setup['identity']
                or record['arguments'] != {'max_minutes': setup['max_minutes']}):
            raise ValueError()
        marker = directory / 'tick.marker'
        if fault == 'during_wait':
            if marker.read_text() != str(setup['start_minute'] + 1):
                raise ValueError()
        elif marker.exists():
            raise ValueError()
        evidence_path = directory / 'completed-wait.json'
        if fault == 'after_execute':
            evidence = json.loads(evidence_path.read_text())
            if not isinstance(evidence, dict) or set(evidence) != {'result', 'checkpoint'}:
                raise ValueError()
            assert_completed_wait(evidence['result'], evidence['checkpoint'], setup)
        elif evidence_path.exists():
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Crash evidence is incomplete') from None


def compare_continuation(baseline, resumed, policy_state, policy_name, result):
    """Restore policy after selection and compare next public decisions, if any."""
    policies = [matrix.make_policy(policy_name), matrix.make_policy(policy_name)]
    for policy in policies:
        policy.restore(copy.deepcopy(policy_state))
        if policy.checkpoint() != policy_state:
            raise ValueError('Policy state differs')
    observations = [copy.deepcopy(result['observation']), copy.deepcopy(result['observation'])]
    controls = (baseline, resumed)
    for index in range(2):
        if baseline.environment.terminated or baseline.environment.truncated:
            return index
        outcomes = []
        for i, control in enumerate(controls):
            public = matrix.PublicFacade(control.environment, [])
            action = policies[i].action(public, observations[i])
            action_result = matrix.commit(public, action)
            if not isinstance(action_result, dict) or 'observation' not in action_result:
                raise ValueError('Continuation action rejected')
            observations[i] = action_result['observation']
            outcomes.append((action, action_result, control.private_checkpoint(), policies[i].checkpoint()))
        if outcomes[0] != outcomes[1]:
            raise ValueError('Policy continuation differs')
    return 2


def exercise_wait(data_dir, selected, private_root, check=lambda: None):
    from eval.wait_recovery_v1 import WaitRecoveryV1
    cell, minutes = selected['cell'], selected['action']['advance_minutes']
    control = control_for(data_dir, cell['runtime_id'])
    control.restore_private_checkpoint(selected['checkpoint'])
    baseline = control.execute('advance', {'max_minutes': minutes})
    baseline_checkpoint = control.private_checkpoint()
    if (not isinstance(baseline, dict) or 'observation' not in baseline
            or baseline_checkpoint['engine_checkpoint'] != selected['engine_checkpoint']
            or any(baseline.get(k) != selected['public_result'].get(k)
                   for k in ('observation', 'reward', 'terminated', 'truncated', 'info'))):
        raise ValueError('Uninterrupted wait differs')
    transitions = baseline_checkpoint['engine_checkpoint']['core']['state']['_actions']
    if not transitions or transitions[-1]['action'] != selected['action']:
        raise ValueError('Action history differs')
    checks = []
    for fault in FAULTS:
        check()
        with tempfile.TemporaryDirectory(prefix='wait-', dir=private_root) as name:
            directory = Path(name)
            setup = dict(checkpoint=selected['checkpoint'], runtime_id=cell['runtime_id'],
                         identity=secrets.token_hex(32), max_minutes=minutes,
                         start_minute=selected['start_minute'],
                         expected_result_sha256=matrix.checksum(baseline),
                         expected_checkpoint_sha256=matrix.checksum(baseline_checkpoint))
            fd = os.open(directory / 'setup.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(setup, file, allow_nan=False)
            process = subprocess.run([sys.executable, '-m', 'eval.validate_wait_recovery_matrix',
                '--worker', fault, '--data-dir', str(data_dir), '--private-dir', str(directory)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if process.returncode != CRASH_EXIT:
                raise ValueError('Crash was not reached')
            verify_crash(directory, fault, setup)
            resumed = control_for(data_dir, cell['runtime_id'])
            scenario = selected['checkpoint']['engine_checkpoint']['core']['state']['_scenario']
            resumed.reset(scenario['id'], scenario['seed'])
            driver = WaitRecoveryV1(resumed, directory, run_identity=setup['identity'])
            recovered = driver.recover()
            if (recovered != baseline or resumed.private_checkpoint() != baseline_checkpoint
                    or driver.start(1, minutes) != baseline
                    or resumed.private_checkpoint() != baseline_checkpoint):
                raise ValueError('Recovered wait differs')
            comparison = control_for(data_dir, cell['runtime_id'])
            comparison.restore_private_checkpoint(baseline_checkpoint)
            steps = compare_continuation(comparison, resumed, selected['policy_state'], cell['policy'], baseline)
            checks.append(dict(fault=fault, passed=True, continuation_transitions=steps))
    return {**cell, 'status': 'passed', 'wait_kind': 'public_multi_minute_wait',
            'refill_coverage': 'not_assessed', 'faults': checks}


def passed_cell(result, cell):
    if not isinstance(result, dict) or any(result.get(k) != cell[k] for k in ('runtime_id', 'case_id', 'policy')):
        return False
    if (result.get('status') != 'passed' or result.get('wait_kind') != 'public_multi_minute_wait'
            or result.get('refill_coverage') != 'not_assessed'):
        return False
    rows = result.get('faults')
    return (isinstance(rows, list) and len(rows) == len(FAULTS) and all(
        isinstance(row, dict) and set(row) == {'fault', 'passed', 'continuation_transitions'}
        and row['fault'] == name and row['passed'] is True
        and type(row['continuation_transitions']) is int and 0 <= row['continuation_transitions'] <= 2
        for row, name in zip(rows, FAULTS)))


def publish(output, report):
    fields = ('version', 'status', 'reference_sha256', 'pins', 'driver_sha256', 'runner_sha256',
              'expected_cells', 'cells', 'limitations', 'reason')
    public = {k: report[k] for k in fields if k in report}
    pin_fields = ('dataset_manifest_sha256', 'dataset_revision', 'core_sha256', 'lifecycle_sha256',
                  'interface_version', 'population_version', 'population_engine_sha256',
                  'v3_sources_sha256', 'validator_sha256', 'source_attestation')
    if 'pins' in public:
        public['pins'] = {k: report['pins'][k] for k in pin_fields if k in report['pins']}
    public['expected_cells'] = [{k: c[k] for k in ('runtime_id', 'case_id', 'policy')} for c in report.get('expected_cells', [])]
    public['cells'] = []
    for cell in report.get('cells', []):
        c = {k: cell[k] for k in ('runtime_id', 'case_id', 'policy', 'status', 'wait_kind', 'refill_coverage') if k in cell}
        if 'faults' in cell:
            c['faults'] = [{k: row[k] for k in ('fault', 'passed', 'continuation_transitions') if k in row} for row in cell['faults']]
        public['cells'].append(c)
    with tempfile.NamedTemporaryFile('w', dir=output, delete=False, encoding='utf-8') as f:
        name = f.name
        try:
            json.dump(public, f, indent=2, allow_nan=False); f.write('\n')
        except BaseException:
            os.unlink(name); raise
    try:
        os.replace(name, output / 'report.json')
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def private_workspace(output, data_dir, reference):
    with tempfile.TemporaryDirectory(prefix='wildfire-matrix-private-', dir='/tmp') as name:
        private = Path(name).resolve()
        forbidden = (Path(output).resolve(), Path(data_dir).resolve(), Path(reference).resolve().parent)
        if any(private.is_relative_to(path) or path.is_relative_to(private) for path in forbidden):
            raise ValueError('Private workspace overlaps input or output')
        yield private


def run(data_dir, reference, reference_sha, output, *, wall_seconds=7200):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    output = Path(output)
    if (output.resolve().is_relative_to(Path(reference).resolve().parent)
            or output.resolve().is_relative_to(Path(data_dir).resolve())):
        raise ValueError('Output overlaps input')
    with matrix.hard_timeout(wall_seconds):
        return _run(data_dir, reference, reference_sha, output, wall_seconds)


def _run(data_dir, reference, reference_sha, output, wall_seconds):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    from eval import wait_recovery_v1 as driver
    from wildfire_control_v3 import _capture_source
    driver_sha = file_sha(driver.__file__)
    report = dict(version=VERSION, status='incomplete', reference_sha256=reference_sha,
        driver_sha256=driver_sha, runner_sha256=SOURCE_SHA, expected_cells=[], cells=[], limitations=[
        'New reconstructed waits; reference trajectory identity and score equality are not established.',
        'Supplementary recovery matrix; it does not alter or supersede the original runtime matrix.',
        'One public-selected multi-minute wait per cell; refill coverage is not assessed.',
        'No checkpoint is taken within an uninterrupted wait; no matrix restart or external effects are tested.',
        'This is not the full-day runtime, operation-coverage, or empirical calibration matrix.',
        'Immutable sources and dataset before import and throughout execution are required.'])
    publish(output, report)
    def check():
        if time.monotonic() - started >= wall_seconds:
            raise matrix.Interrupted('wall_budget')
    def pins():
        check()
        ref = read_reference(reference, reference_sha)
        manifest, current = matrix.pins(data_dir)
        module = sys.modules[__name__]
        module_name = module.__spec__.name if module.__spec__ else module.__name__
        own_hash, own_matches = _capture_source(module_name, __file__, module.__loader__)
        driver_hash, driver_matches = _capture_source(driver.__name__, driver.__file__, driver.__loader__)
        if (ref.get('pins') != current or file_sha(__file__) != SOURCE_SHA or not own_matches
                or own_hash != SOURCE_SHA or file_sha(driver.__file__) != driver_sha
                or not driver_matches or driver_hash != driver_sha or driver._SOURCE_SHA256 != driver_sha):
            raise ValueError('Reference or source identity differs')
        return ref, manifest, current
    try:
        ref, manifest, current = pins()
        expected = matrix.expected_cells(manifest)
        refs = reference_cells(ref, expected)
        report.update(pins=current, expected_cells=expected,
                      cells=[{**cell, 'status': 'not_run'} for cell in expected])
        publish(output, report)
        with private_workspace(output, data_dir, reference) as private:
            for index, cell in enumerate(expected):
                pins()
                selected = select_wait(data_dir, cell, refs[matrix.cell_identity(cell)], check)
                result = exercise_wait(data_dir, selected, private, check)
                pins()
                if not passed_cell(result, cell):
                    raise ValueError('Recovery evidence is incomplete')
                report['cells'][index] = result
                publish(output, report)
        pins()
        if len(report['cells']) != 48 or not all(passed_cell(c, e) for c, e in zip(report['cells'], expected)):
            raise ValueError('Matrix evidence is incomplete')
        report['status'] = 'complete'
        check(); publish(output, report); check()
        return report
    except BaseException as exc:
        report['status'] = 'incomplete'
        report['reason'] = 'wall_budget' if isinstance(exc, matrix.Interrupted) else 'validation_error'
        publish(output, report)
        if isinstance(exc, matrix.Interrupted): return report
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--reference-report')
    parser.add_argument('--reference-sha256')
    parser.add_argument('--output')
    parser.add_argument('--wall-seconds', type=float, default=7200)
    parser.add_argument('--worker', choices=FAULTS, help=argparse.SUPPRESS)
    parser.add_argument('--private-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.private_dir is None: parser.error('worker needs private directory')
        crash_worker(args.data_dir, args.private_dir, args.worker)
        return 2
    if not all((args.reference_report, args.reference_sha256, args.output)):
        parser.error('reference report, SHA-256 and output required')
    try:
        result = run(args.data_dir, args.reference_report, args.reference_sha256, args.output,
                     wall_seconds=args.wall_seconds)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'incomplete', 'reason': 'validation_error'})); return 2
    print(json.dumps({'status': result['status'], 'passed': sum(c.get('status') == 'passed' for c in result['cells']),
                      'expected': len(result['expected_cells'])}))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
