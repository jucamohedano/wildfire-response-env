"""Supplementary crash/restart validation for six previously blocked refill cells.

Run in a fresh process, with immutable source and dataset files, after the
original matrix writer exits. This validates freshly reconstructed trajectories;
it neither modifies nor proves identity with the archived matrix. The private
wait driver persists control state only, not policy or matrix-runner progress.
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
from eval import validate_population_refill_probe as probe

VERSION = 'wait-recovery-validation-v2'
FAULTS = ('after_prepare', 'during_wait', 'after_execute', 'after_commit')
CRASH_EXIT = 73
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def control_for(data_dir):
    from wildfire_control_v3.control import ControlCenterV3
    if data_dir is None:  # Isolated fixture integration tests, not the matrix CLI.
        from wildfire_core.incident_day import fixture_inputs
        from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
        inputs, network = fixture_inputs()
        env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                 expected_network_sha256=network['sha256'])
    else:
        env, _ = matrix.make_v3(data_dir, 'fixture')
    return ControlCenterV3(env)


def select_wait(data_dir, cell, check=lambda: None):
    control = control_for(data_dir)
    env = control.environment
    observation, _ = env.reset(cell['case_id'])
    pins = cell.get('pins')
    fields = ('inputs_sha256', 'network_sha256', 'scenario_sha256')
    if (not isinstance(pins, dict) or any(not isinstance(pins.get(key), str)
            or re.fullmatch(r'[0-9a-f]{64}', pins[key]) is None for key in fields)
            or env.inputs['sha256'] != pins['inputs_sha256']
            or env.network.sha256 != pins['network_sha256']
            or env._scenario['sha256'] != pins['scenario_sha256']):
        raise ValueError('Case identity differs')
    policy = matrix.make_policy(cell['policy'])
    calls = []
    public = matrix.PublicFacade(env, calls)
    public.get_situation(); public.get_reports(limit=20)
    while not (env.terminated or env.truncated):
        check()
        if probe._refilling(env):
            raise ValueError('Refill reached a decision boundary')
        audit_start = len(env._audit)
        action = policy.action(public, observation)
        before = control.private_checkpoint()
        policy_state = policy.checkpoint()
        result = matrix.commit(public, action)
        if not isinstance(result, dict) or 'observation' not in result:
            raise ValueError('Reconstructed action rejected')
        observation = result['observation']
        seen = [row['minute'] for row in env._audit[audit_start:] if
                any(unit['state'] == 'refilling' for unit in row['resources'].values())]
        if seen:
            if set(action) != {'advance_minutes'} or probe._refilling(env):
                raise ValueError('Refill was not transient within a wait')
            return {'checkpoint': before, 'action': action, 'policy_state': policy_state,
                    'unsplit_result': result,
                    'unsplit_checkpoint': env.checkpoint(), 'case_id': cell['case_id'],
                    'policy': cell['policy'], 'refill_minutes': seen}
    raise ValueError('No qualifying refill wait found')


def _crash_worker(data_dir, directory, fault):
    """Internal subprocess: inject a process death at an observed protocol boundary."""
    from eval.wait_recovery_v1 import WaitRecoveryV1
    setup = json.loads((directory / 'setup.json').read_text())
    control = control_for(data_dir)
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
                row = env._audit[-1]
                if (row['minute'] == env.minute and probe._refilling(env)
                        and any(unit['state'] == 'refilling' for unit in row['resources'].values())):
                    fd = os.open(directory / 'refill-tick.marker',
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w') as marker:
                        marker.write(str(env.minute)); marker.flush(); os.fsync(marker.fileno())
                    os._exit(CRASH_EXIT)
                return result
            env._tick = interrupted_tick
        result = execute(name, arguments)
        if fault == 'after_execute':
            os._exit(CRASH_EXIT)
        return result
    control.execute = interrupted_execute
    driver.start(1, setup['max_minutes'], initialize=True)
    raise RuntimeError('Requested crash point was not reached')


def assert_crash_record(directory, fault, identity, minutes, refill_minutes):
    """Confirm the exit occurred after the expected journal transition."""
    try:
        record = json.loads((directory / 'wait-recovery-v1.json').read_text())['record']
        phase = 'complete' if fault == 'after_commit' else 'pending'
        if (fault not in FAULTS or record['phase'] != phase
                or type(record['sequence']) is not int or record['sequence'] != 1
                or record['run_identity'] != identity or record['arguments'] != {'max_minutes': minutes}):
            raise ValueError('Unexpected crash journal')
        marker = directory / 'refill-tick.marker'
        if fault == 'during_wait':
            text = marker.read_text()
            if not text.isascii() or not text.isdecimal() or int(text) not in refill_minutes:
                raise ValueError('Refilling tick was not confirmed')
        elif marker.exists():
            raise ValueError('Unexpected refill marker')
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Unexpected crash journal') from None


def exercise_wait(data_dir, selected, private_root, check=lambda: None):
    from eval.wait_recovery_v1 import WaitRecoveryV1
    control = control_for(data_dir)
    control.restore_private_checkpoint(selected['checkpoint'])
    minutes = selected['action']['advance_minutes']
    baseline = control.execute('advance', {'max_minutes': minutes})
    baseline_checkpoint = control.private_checkpoint()
    if (not isinstance(baseline, dict) or 'observation' not in baseline
            or baseline_checkpoint['engine_checkpoint'] != selected['unsplit_checkpoint']
            or any(baseline.get(k) != selected['unsplit_result'].get(k)
                   for k in ('observation', 'reward', 'terminated', 'truncated', 'info'))):
        raise ValueError('Control differs from reconstructed unsplit wait')
    action_log = baseline_checkpoint['engine_checkpoint']['core']['state']['_actions']
    if not action_log or action_log[-1]['action'] != selected['action']:
        raise ValueError('Selected wait was not recorded')
    checks = []
    for fault in FAULTS:
        check()
        with tempfile.TemporaryDirectory(prefix='wait-', dir=private_root) as tmp:
            directory = Path(tmp)
            setup = {'checkpoint': selected['checkpoint'], 'max_minutes': minutes,
                     'identity': secrets.token_hex(32)}
            fd = os.open(directory / 'setup.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(setup, stream, allow_nan=False)
            cmd = [sys.executable, '-m', 'eval.validate_wait_recovery', '--worker', fault,
                   '--private-dir', str(directory)]
            if data_dir is not None:
                cmd += ['--data-dir', str(data_dir)]
            completed = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, check=False)
            if completed.returncode != CRASH_EXIT:
                raise ValueError('Crash point not reached')
            assert_crash_record(directory, fault, setup['identity'], minutes, selected['refill_minutes'])
            resumed = control_for(data_dir)
            scenario = selected['checkpoint']['engine_checkpoint']['core']['state']['_scenario']
            resumed.reset(scenario['id'], scenario['seed'])
            driver = WaitRecoveryV1(resumed, directory, run_identity=setup['identity'])
            result = driver.recover()
            checkpoint = resumed.private_checkpoint()
            repeated = driver.start(1, minutes)
            restored_policy = matrix.make_policy(selected['policy'])
            restored_policy.restore(copy.deepcopy(selected['policy_state']))
            if (result != baseline or repeated != baseline or checkpoint != baseline_checkpoint
                    or resumed.private_checkpoint() != checkpoint
                    or restored_policy.checkpoint() != selected['policy_state']):
                raise ValueError('Crash recovery differs from uninterrupted wait')
            checks.append({'fault': fault, 'passed': True})
    return {'runtime_id': 'fixture', 'case_id': selected['case_id'], 'policy': selected['policy'],
            'original_cell_status': 'blocked', 'status': 'passed', 'faults': checks}


def passed_case(result, cell):
    if not isinstance(result, dict):
        return False
    if any(result.get(key) != value for key, value in (
            ('runtime_id', cell['runtime_id']), ('case_id', cell['case_id']),
            ('policy', cell['policy']), ('original_cell_status', 'blocked'), ('status', 'passed'))):
        return False
    faults = result.get('faults')
    return (isinstance(faults, list) and len(faults) == len(FAULTS)
            and all(isinstance(item, dict) and set(item) == {'fault', 'passed'}
                    and item['fault'] == name and item['passed'] is True
                    for item, name in zip(faults, FAULTS)))


def publish(output, report):
    # Copy only explicit aggregate fields, never private snapshots, calls or results.
    allowed = ('version', 'status', 'original_report_sha256', 'pins', 'driver_sha256',
               'harness_sha256', 'probe_sha256', 'eligible_cells', 'cases', 'limitations', 'reason')
    public = {k: report[k] for k in allowed if k in report}
    if 'pins' in public:
        pin_fields = ('dataset_manifest_sha256', 'dataset_revision', 'core_sha256',
                      'lifecycle_sha256', 'interface_version', 'population_version',
                      'population_engine_sha256', 'v3_sources_sha256', 'validator_sha256',
                      'source_attestation')
        public['pins'] = {k: public['pins'][k] for k in pin_fields if k in public['pins']}
    public['cases'] = [{k: c[k] for k in ('runtime_id', 'case_id', 'policy',
                        'original_cell_status', 'status', 'faults') if k in c}
                       for c in report.get('cases', [])]
    for c in public['cases']:
        if 'faults' in c:
            c['faults'] = [{k: f[k] for k in ('fault', 'passed') if k in f} for f in c['faults']]
    with tempfile.NamedTemporaryFile('w', dir=output, delete=False, encoding='utf-8') as stream:
        name = stream.name
        try:
            json.dump(public, stream, indent=2, allow_nan=False); stream.write('\n')
        except BaseException:
            os.unlink(name); raise
    try:
        os.replace(name, output / 'report.json')
    finally:
        if os.path.exists(name): os.unlink(name)


def run(data_dir, original_report, original_sha, output, *, wall_seconds=1200):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    output = Path(output)
    if (output.resolve().is_relative_to(Path(original_report).resolve().parent)
            or output.resolve().is_relative_to(Path(data_dir).resolve())):
        raise ValueError('Output must be separate from original report and dataset')
    with matrix.hard_timeout(wall_seconds):
        return _run(data_dir, original_report, original_sha, output, wall_seconds)


@contextlib.contextmanager
def private_workspace(output, data_dir, original_report):
    # The output can be published: a killed parent may leave private files behind.
    # Put them in an independent mode-0700 directory, never below any input/output tree.
    with tempfile.TemporaryDirectory(prefix='wildfire-wait-private-', dir='/tmp') as name:
        private = Path(name).resolve()
        forbidden = (Path(output).resolve(), Path(data_dir).resolve(), Path(original_report).resolve().parent)
        if any(private.is_relative_to(path) or path.is_relative_to(private) for path in forbidden):
            raise ValueError('Private workspace overlaps input or output')
        yield private


def _run(data_dir, original_report, original_sha, output, wall_seconds):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    driver_path = ROOT / 'eval/wait_recovery_v1.py'
    driver_pin = source_hash(driver_path)
    probe_pin = probe.source_pin()
    from eval import wait_recovery_v1 as driver_module
    from wildfire_control_v3 import _capture_source
    report = {'version': VERSION, 'status': 'incomplete', 'cases': [],
              'original_report_sha256': original_sha, 'driver_sha256': driver_pin,
              'harness_sha256': SOURCE_SHA, 'probe_sha256': probe_pin, 'limitations': [
        'Freshly reconstructed unsplit waits; original matrix identity and score equality are not established.',
        'Supplementary evidence only; original blocked cells and matrix status remain unchanged.',
        'The driver persists control waits only; policy and matrix progress recovery are not tested.',
        'No checkpoint is taken inside the original uninterrupted wait.',
        'Sources must remain immutable before import and throughout execution.']}
    publish(output, report)
    def check():
        if time.monotonic() - started >= wall_seconds:
            raise matrix.Interrupted('wall_budget')
    def pins():
        check()
        if probe.source_pin() != probe_pin:
            raise ValueError('Probe identity changed')
        original = probe.read_original(original_report, original_sha)
        manifest, pinned = matrix.pins(data_dir)
        this_module = sys.modules[__name__]
        this_name = this_module.__spec__.name if this_module.__spec__ else this_module.__name__
        harness_loaded, harness_matches = _capture_source(this_name, __file__, this_module.__loader__)
        driver_loaded, driver_matches = _capture_source(driver_module.__name__, driver_module.__file__,
                                                        driver_module.__loader__)
        if (original.get('pins') != pinned or source_hash(driver_path) != driver_pin
                or source_hash(__file__) != SOURCE_SHA or not harness_matches or not driver_matches
                or harness_loaded != SOURCE_SHA or driver_loaded != driver_pin
                or driver_module._SOURCE_SHA256 != driver_pin):
            raise ValueError('Source or original identity changed')
        return original, manifest, pinned
    try:
        original, manifest, pinned = pins()
        targets, eligible = probe.select_targets(original, matrix.expected_cells(manifest))
        if eligible != 6 or len(targets) != 6:
            raise ValueError('Expected six blocked refill case-policy pairs')
        report.update(pins=pinned, eligible_cells=eligible)
        publish(output, report)
        with private_workspace(output, data_dir, original_report) as private:
            for cell in targets:
                pins()
                selected = select_wait(data_dir, cell, check)
                result = exercise_wait(data_dir, selected, private, check)
                pins()
                if not passed_case(result, cell):
                    raise ValueError('Recovery evidence is incomplete')
                report['cases'].append(result)
                publish(output, report)
        pins()
        report['status'] = 'complete'
        publish(output, report)
        check()
        return report
    except BaseException as exc:
        report['status'] = 'incomplete'
        report['reason'] = 'wall_budget' if isinstance(exc, matrix.Interrupted) else 'validation_error'
        publish(output, report)
        if isinstance(exc, matrix.Interrupted):
            return report
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir')
    parser.add_argument('--original-report')
    parser.add_argument('--original-report-sha256')
    parser.add_argument('--output')
    parser.add_argument('--wall-seconds', type=float, default=1200)
    parser.add_argument('--worker', choices=FAULTS, help=argparse.SUPPRESS)
    parser.add_argument('--private-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.private_dir is None: parser.error('worker needs private directory')
        _crash_worker(args.data_dir, args.private_dir, args.worker)
        return 2
    if not all((args.data_dir, args.original_report, args.original_report_sha256, args.output)):
        parser.error('data, original report, its SHA-256, and output are required')
    try:
        result = run(args.data_dir, args.original_report, args.original_report_sha256,
                     args.output, wall_seconds=args.wall_seconds)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'incomplete', 'reason': 'validation_error'}))
        return 2
    print(json.dumps({'status': result['status'], 'eligible_cells': result.get('eligible_cells'),
                      'passed': len(result['cases'])}))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
