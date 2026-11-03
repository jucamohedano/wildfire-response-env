"""Supplementary, single-worker full-day matrix for deterministic local sessions.

Each cell compares a SessionRecoveryV1 session with a separately driven v3
control, policy and tick observer at every decision. A fresh session restores
one completed journal at or after minute 720; this is NOT a process-death test.
No matrix resume or external/model/learner recovery is provided. A successful
report is supplementary and never changes the original runtime matrix status.
Run with immutable sources/data from before import, PYTHONDONTWRITEBYTECODE=1,
and a fresh PYTHONPYCACHEPREFIX. The output is aggregate-only; private journals
live in a separate owner-only temporary workspace. SIGKILL may leave that private
workspace behind. A signal/budget/error makes the report incomplete. Source and
loader checks are consistency checks, not proof of import-time immutability.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import sys
import tempfile
import time

# Import the matrix first: its pin checks reject pre-imported core modules.
from eval import validate_population_v3_runtime as matrix
from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import make_policy
from eval.session_recovery_v1 import SessionRecoveryV1
from eval import session_recovery_v1 as session_driver, wait_recovery_v1 as wait_driver
from wildfire_control_v3.control import ControlCenterV3, interface_digest
from wildfire_control_v3.population_engine_v3 import VERSION as ENGINE_VERSION, population_engine_identity
from envs.wildfire_response.incident_day.scenarios import build_scenario

VERSION = 'session-recovery-active-matrix-v1'
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DRIVER_PATH = ROOT / 'eval/session_recovery_v1.py'
DRIVER_SHA = hashlib.sha256(DRIVER_PATH.read_bytes()).hexdigest()
WAIT_DRIVER_PATH = ROOT / 'eval/wait_recovery_v1.py'
WAIT_DRIVER_SHA = hashlib.sha256(WAIT_DRIVER_PATH.read_bytes()).hexdigest()


def digest(value):
    return matrix.checksum(value)


def json_copy(value):
    return json.loads(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate reference key')
        value[key] = item
    return value


def read_reference(path, sha, expected):
    path = Path(path)
    if (not isinstance(sha, str) or len(sha) != 64
            or any(c not in '0123456789abcdef' for c in sha)
            or path.is_symlink() or not path.is_file()):
        raise ValueError('Invalid reference')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('Reference identity differs')
    ref = json.loads(raw, object_pairs_hook=unique_object,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid reference constant')))
    if (ref.get('version') != matrix.VERSION or ref.get('expected_cells') != expected
            or ref.get('status') not in ('complete', 'incomplete', 'blocked', 'failed')):
        raise ValueError('Reference domain differs')
    cells = ref.get('cells')
    if not isinstance(cells, list) or len(cells) != len(expected) or len(expected) != 48:
        raise ValueError('Reference domain incomplete')
    indexed = {matrix.cell_identity(item): item for item in cells}
    if len(indexed) != 48 or set(indexed) != {matrix.cell_identity(item) for item in expected}:
        raise ValueError('Reference domain differs')
    for cell in indexed.values():
        if cell.get('status') not in ('complete', 'blocked', 'failed'):
            raise ValueError('Reference cell incomplete')
        if any(not isinstance(cell.get('pins', {}).get(k), str) or len(cell['pins'][k]) != 64
               or any(ch not in '0123456789abcdef' for ch in cell['pins'][k])
               for k in ('inputs_sha256', 'network_sha256', 'scenario_sha256')):
            raise ValueError('Reference cell pins missing')
    try:
        assessed_status = matrix.matrix_status(ref)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError('Invalid reference evidence') from error
    # Interruption or validation failure can conservatively leave a report
    # incomplete even when its recorded cells would otherwise assess differently.
    conservative_incomplete = (ref['status'] == 'incomplete'
        and ref.get('reason') in ('signal', 'wall_budget', 'selected_subset', 'validation_error'))
    if ref['status'] != assessed_status and not conservative_incomplete:
        raise ValueError('Reference status contradicts evidence')
    return ref, indexed


def check_recovery_sources():
    """Check both driver modules against captured bytes, loader and session pin."""
    from wildfire_control_v3 import _capture_source
    for module, path, expected_hash in ((sys.modules[__name__], Path(__file__), SOURCE_SHA),
                                        (session_driver, DRIVER_PATH, DRIVER_SHA),
                                        (wait_driver, WAIT_DRIVER_PATH, WAIT_DRIVER_SHA)):
        spec = module.__spec__
        name = spec.name if spec else module.__name__
        h, matches = _capture_source(name, str(path), module.__loader__)
        if (not matches or h != expected_hash or matrix._hash(path) != expected_hash
                or Path(module.__file__).resolve() != path.resolve()):
            raise ValueError('Runner or recovery source changed')
    if (wait_driver._SOURCE_SHA256 != WAIT_DRIVER_SHA
            or session_driver._SOURCE.get('wait_recovery_v1.py') != WAIT_DRIVER_SHA):
        raise ValueError('Wait recovery dependency identity differs')


class Public:
    """Independently record the policy's allowed calls against a v3 control."""
    def __init__(self, control, calls):
        self.control, self.calls = control, calls

    def execute(self, name, arguments):
        env = self.control.environment
        minute, decision = env.minute, env.decision
        result = self.control.execute(name, copy.deepcopy(arguments))
        retained = {k: copy.deepcopy(result[k]) for k in
                    ('info', 'reward', 'terminated', 'truncated', 'error', 'committed', 'valid')
                    if isinstance(result, dict) and k in result}
        self.calls.append(dict(name=name, arguments=copy.deepcopy(arguments), before_minute=minute,
            after_minute=env.minute, before_decision=decision, after_decision=env.decision, result=retained))
        return result

    def get_situation(self): return self.execute('get_situation', {})
    def get_reports(self, **arguments): return self.execute('get_reports', arguments)
    def route_options(self, resource_id, sector_id='incident'):
        return self.execute('route_options', dict(resource_id=resource_id, sector_id=sector_id))
    def evacuation_options(self, group_id, destination_id):
        return self.execute('evacuation_options', dict(group_id=group_id, destination_id=destination_id))
    def check_action(self, action): return self.execute('check_action', dict(action=action))


def apply_action(public, action):
    if set(action) == {'advance_minutes'}:
        return public.execute('advance', {'max_minutes': action['advance_minutes']})
    if set(action) == {'status_requests'} and len(action['status_requests']) == 1:
        return public.execute('request_status', action['status_requests'][0])
    return public.execute('commit_orders', {'action': action})


def evaluate(state, action, result):
    # Only JSON state owned by the driver; no I/O or external side effects.
    state['last_action'] = copy.deepcopy(action)
    state['count'] += 1


def session_state(session):
    return json_copy(dict(control=session.control.private_checkpoint(), policy=session.policy.checkpoint(),
        observer=session.observer.snapshot(), calls=session.calls, observation=session.observation))


def reference_state(control, policy, observer, calls, observation):
    return json_copy(dict(control=control.private_checkpoint(), policy=policy.checkpoint(),
        observer=observer.snapshot(), calls=calls, observation=observation))


def make_control(data_dir, cell):
    env, _ = matrix.make_v3(data_dir, cell['runtime_id'])
    control = ControlCenterV3(env)
    control.reset(cell['case_id'])
    return control


def exercise_cell(factory, cell, pins, private, check, *, step_limit=None):
    """A limited run is explicitly partial and exists for local smoke tests."""
    oracle = factory()
    session_control = factory()
    if (oracle.environment.inputs['sha256'] != pins['inputs_sha256']
            or oracle.environment.network.sha256 != pins['network_sha256']
            or oracle.environment._scenario['sha256'] != pins['scenario_sha256']):
        raise ValueError('Case pins differ')
    # Set trusted expected identity from independently built pinned inputs and
    # the reference, before running either session, not from its trajectory.
    scenario = build_scenario(oracle.environment.inputs, oracle.environment.network, cell['case_id'])
    if scenario['sha256'] != pins['scenario_sha256']:
        raise ValueError('Scenario pin differs')
    identity = dict(interface_version=ENGINE_VERSION, task_id=cell['case_id'], seed=scenario['seed'],
                    inputs_sha256=pins['inputs_sha256'], network_sha256=pins['network_sha256'],
                    scenario_sha256=pins['scenario_sha256'], implementation_sha256=population_engine_identity())
    interface_pin = interface_digest()
    policy = make_policy(cell['policy'])
    observation = oracle.environment.get_situation()
    observer = MovementObserver(oracle.environment)
    calls = []
    public = Public(oracle, calls)
    run_id = secrets.token_hex(32)
    def new_session(control):
        return SessionRecoveryV1(control, private, run_identity=run_id, policy_name=cell['policy'],
            evaluation={'last_action': None, 'count': 0}, evaluator=evaluate,
            evaluator_identity='session-matrix-evaluator-v1')
    session = new_session(session_control)
    session.initialize()
    if session_state(session) != reference_state(oracle, policy, observer, calls, observation):
        raise ValueError('Initial session differs')
    sequence, resumed = 0, False
    while not (oracle.environment.terminated or oracle.environment.truncated):
        check()
        if step_limit is not None and sequence >= step_limit:
            return {'status': 'partial', 'steps': sequence, 'minute': oracle.environment.minute,
                    'restart_checked': resumed}
        sequence += 1
        action = policy.action(public, copy.deepcopy(observation))
        expected = apply_action(public, action)
        if not isinstance(expected, dict) or not isinstance(expected.get('observation'), dict):
            raise ValueError('Oracle action failed')
        observation = expected['observation']
        result = session.step(sequence)
        if (json_copy(action) != session.evaluation['last_action'] or session.evaluation['count'] != sequence
                or json_copy(expected) != result
                or digest(reference_state(oracle, policy, observer, calls, observation)) != digest(session_state(session))):
            raise ValueError('Session continuation differs')
        # Recreate the full session from its most recent completed transaction.
        if not resumed and oracle.environment.minute >= 720 and not (oracle.environment.terminated or oracle.environment.truncated):
            check()
            fresh = new_session(factory())
            previous_engine = fresh.control.environment
            redelivered = fresh.recover()
            if (redelivered != result or fresh.step(sequence) != result
                    or fresh.control.environment is previous_engine
                    or fresh.observer.env is not fresh.control.environment
                    or fresh.observer.ops is not fresh.control.environment.ops
                    or session_state(fresh) != session_state(session)
                    or fresh.evaluation != session.evaluation):
                raise ValueError('Session restoration differs')
            session, resumed = fresh, True
    check()
    observed = observer.snapshot()
    if (not resumed or oracle.environment.minute != 1440 or not oracle.environment.terminated
            or oracle.environment.truncated or observed['coverage'].get('ticks_observed') != 1440
            or observed['coverage'].get('capacity_ticks_checked') != 1440):
        raise ValueError('Day or observer evidence incomplete')
    expected = oracle.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)
    actual = session.control.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)
    if expected != actual or expected.get('status') != 'full_day' or expected.get('verified') is not True:
        raise ValueError('Outcome verification differs')
    check()
    return {'status': 'passed', 'minute': 1440, 'steps': sequence, 'restart_checked': True,
            'observer_ticks': observed['coverage']['ticks_observed'], 'verified_outcome': True}


def passed_cell(result):
    return (isinstance(result, dict) and set(result) ==
            {'status', 'minute', 'steps', 'restart_checked', 'observer_ticks', 'verified_outcome'}
            and result['status'] == 'passed' and type(result['minute']) is int and result['minute'] == 1440
            and type(result['steps']) is int and result['steps'] > 0
            and result['restart_checked'] is True and type(result['observer_ticks']) is int
            and result['observer_ticks'] == 1440 and result['verified_outcome'] is True)


def publish(output, report):
    # No per-action, checkpoint, observation, exception, run identity, or score output.
    fields = ('version', 'status', 'reason', 'workers', 'elapsed_seconds', 'reference_sha256',
              'reference_status', 'pins', 'driver_sha256', 'wait_recovery_sha256', 'runner_sha256',
              'expected_cells', 'cells', 'limitations')
    result = {k: report[k] for k in fields if k in report}
    pin_fields = ('dataset_manifest_sha256', 'dataset_revision', 'core_sha256', 'lifecycle_sha256',
                  'interface_version', 'population_version', 'population_engine_sha256',
                  'v3_sources_sha256', 'validator_sha256', 'source_attestation')
    if 'pins' in result:
        result['pins'] = {k: report['pins'][k] for k in pin_fields if k in report['pins']}
    allowed = ('runtime_id', 'case_id', 'policy', 'reference_status', 'status', 'minute', 'steps',
               'restart_checked', 'observer_ticks', 'verified_outcome')
    result['cells'] = [{k: cell[k] for k in allowed if k in cell} for cell in report.get('cells', [])]
    result['expected_cells'] = [{k: cell[k] for k in ('runtime_id', 'case_id', 'policy')} for cell in report.get('expected_cells', [])]
    with tempfile.NamedTemporaryFile('w', dir=output, delete=False, encoding='utf-8') as file:
        name = file.name
        try:
            json.dump(result, file, indent=2, allow_nan=False)
            file.write('\n')
        except BaseException:
            os.unlink(name)
            raise
    try:
        os.replace(name, output / 'report.json')
    finally:
        if os.path.exists(name): os.unlink(name)


def run(data_dir, reference, reference_sha, output, *, wall_seconds=10800, max_cells=None):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Invalid wall budget')
    if max_cells is not None and (type(max_cells) is not int or max_cells < 1):
        raise ValueError('Invalid cell limit')
    output, data_dir, reference = Path(output), Path(data_dir), Path(reference)
    for source in (data_dir.resolve(), reference.resolve().parent, ROOT.resolve()):
        if output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
            raise ValueError('Output overlaps source')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    started, stopped = time.monotonic(), False
    report = dict(version=VERSION, status='incomplete', workers=1, elapsed_seconds=0,
        reference_sha256=reference_sha, driver_sha256=DRIVER_SHA,
        wait_recovery_sha256=WAIT_DRIVER_SHA, runner_sha256=SOURCE_SHA,
        expected_cells=[], cells=[], limitations=[
            'Supplementary full-day session comparison; original matrix status is unchanged.',
            'A completed transaction is restored once per cell; no process-death campaign or matrix resume.',
            'Deterministic local policies only; external model, training, and output delivery are not recovered.',
            'This does not replace the original operation/phase-coverage matrix or establish field calibration.',
            'Sources and data must be immutable before import and throughout; checks do not attest import-time execution.'])
    def check():
        if stopped or time.monotonic() - started >= wall_seconds:
            raise matrix.Interrupted('signal' if stopped else 'wall_budget')
    def progress():
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        publish(output, report)
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    def pinned():
        check()
        check_recovery_sources()
        manifest, pins = matrix.pins(data_dir)
        expected = matrix.expected_cells(manifest)
        ref, cells = read_reference(reference, reference_sha, expected)
        if ref.get('pins') != pins:
            raise ValueError('Reference pins differ')
        return pins, expected, ref, cells
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        progress()
        with matrix.hard_timeout(wall_seconds):
            pins, expected, ref, references = pinned()
            report.update(pins=pins, reference_status=ref['status'], expected_cells=expected,
                          cells=[{**c, 'reference_status': references[matrix.cell_identity(c)]['status'],
                                  'status': 'not_run'} for c in expected])
            progress()
            with tempfile.TemporaryDirectory(prefix='wildfire-session-matrix-', dir='/tmp') as name:
                private_root = Path(name).resolve()
                if any(private_root.is_relative_to(p) or p.is_relative_to(private_root)
                       for p in (output.resolve(), data_dir.resolve(), reference.resolve().parent)):
                    raise ValueError('Private workspace overlaps input or output')
                for index, cell in enumerate(expected):
                    check()
                    if max_cells is not None and index >= max_cells:
                        report['reason'] = 'selected_subset'
                        progress()
                        return report
                    now, _, _, _ = pinned()
                    if now != pins: raise ValueError('Pins changed')
                    report['cells'][index]['status'] = 'incomplete'
                    progress()
                    with tempfile.TemporaryDirectory(prefix='cell-', dir=private_root) as cell_path:
                        details = exercise_cell(lambda: make_control(data_dir, cell), cell,
                            references[matrix.cell_identity(cell)]['pins'], Path(cell_path), check)
                    check()
                    if not passed_cell(details): raise ValueError('Cell evidence incomplete')
                    report['cells'][index].update(details)
                    progress()
            now, _, _, _ = pinned()
            if now != pins or len(report['cells']) != 48 or any(c['status'] != 'passed' for c in report['cells']):
                raise ValueError('Matrix incomplete')
            check()
            report['status'] = 'complete'
            progress()
            check()
            return report
    except BaseException as error:
        signal.setitimer(signal.ITIMER_REAL, 0)
        report['status'] = 'incomplete'
        if stopped:
            report['reason'] = 'signal'
        elif time.monotonic() - started >= wall_seconds:
            report['reason'] = 'wall_budget'
        else:
            report['reason'] = 'validation_error'
        progress()
        if report['reason'] in ('signal', 'wall_budget'): return report
        raise
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--reference-report', required=True)
    parser.add_argument('--reference-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--wall-seconds', type=float, default=10800)
    parser.add_argument('--max-cells', type=int)
    args = parser.parse_args()
    try:
        report = run(args.data_dir, args.reference_report, args.reference_sha256, args.output,
                     wall_seconds=args.wall_seconds, max_cells=args.max_cells)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'incomplete', 'reason': 'validation_error'}))
        return 2
    print(json.dumps({'status': report['status'], 'reason': report.get('reason'),
                      'passed': sum(c.get('status') == 'passed' for c in report['cells']),
                      'expected': len(report['expected_cells']), 'elapsed_seconds': report['elapsed_seconds']}))
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
