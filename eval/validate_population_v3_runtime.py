"""CPU matrix validation for the separately versioned population engine.

Runs all active configurations and policies from the release-locked dataset. A
budget/signal stop or error is incomplete; checkpoints are deliberately not
resumed: start a fresh output directory to repeat the entire matrix. Private
journals and detailed audits are never written to public reports. The final
rename is non-preemptible; a signal arriving after the last pre-rename check
is handled immediately after the rename by replacing success with incomplete.
Run in a fresh process with sources immutable before import and throughout the
run, and an empty bytecode cache, e.g. set PYTHONPYCACHEPREFIX to a new
directory and PYTHONDONTWRITEBYTECODE=1. Source/loader checks compare current
files and loader code; they do not attest to code executed during import.
SIGALRM delivery can be delayed by native code that does not return control.
"""
import argparse
import copy
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
sys.path.insert(0, str(ROOT / 'envs/wildfire_control_v3/src'))

VALIDATOR_MODULES = ('eval.validate_incident_day_runtime', 'eval.incident_day_action_checks',
    'eval.incident_day_movement_checks', 'eval.incident_day_policies',
    'eval.check_incident_day_plausibility', 'eval.hub_runtime', 'eval.run_incident_day')
_PREIMPORTED_VALIDATOR_MODULES = tuple(name for name in VALIDATOR_MODULES if name in sys.modules)
_PREIMPORTED_CORE_MODULES = tuple(name for name in sys.modules if
    name in ('envs', 'envs.wildfire_response', 'wildfire_core', 'simulator') or
    name.startswith(('envs.wildfire_response.', 'wildfire_core.', 'simulator.')))

from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_core.catalog_lifecycle import registry
from eval.hub_runtime import make_environment, verify_package
from eval.incident_day_action_checks import audit_runtime
from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import POLICY_NAMES, make_policy
from eval.check_incident_day_plausibility import static_audit
from eval.validate_incident_day_runtime import (PublicFacade, commit, expected_cells,
    cell_identity, checksum, aggregate_findings, validate_matrix, BOUNDS)

VERSION = 'wildfire-population-v3-runtime-matrix-v3'
LOCK = ROOT / 'data/hub_runtime_v1/release.json'
VALIDATOR_FILES = ('eval/validate_population_v3_runtime.py', 'eval/validate_incident_day_runtime.py',
    'eval/incident_day_action_checks.py', 'eval/incident_day_movement_checks.py',
    'eval/incident_day_policies.py', 'eval/check_incident_day_plausibility.py',
    'eval/hub_runtime.py', 'eval/run_incident_day.py',
    'data/incident_day_v2/plausibility/bounds-v1.json',
    'data/incident_day_v2/plausibility/action-bounds-v1.json', 'data/hub_runtime_v1/release.json')


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Capture current files after imports; this cannot attest to what was executed.
_VALIDATOR_SOURCE_AT_CAPTURE_SHA256 = checksum({p: _hash(ROOT / p) for p in VALIDATOR_FILES})
BOUNDARIES = ('travel', 'on_scene', 'working', 'returning', 'refilling', 'resting', 'hourly', 'midday')


def check_source_loader_consistency():
    """Compare current sources and loader code, not past module execution."""
    from wildfire_control_v3 import _capture_source
    if _PREIMPORTED_VALIDATOR_MODULES or _PREIMPORTED_CORE_MODULES:
        raise ValueError('Validator or core dependencies were preimported; use a fresh process')
    core_root = (ROOT / 'envs/wildfire_response/core/src').resolve()
    core_modules = [module for module in list(sys.modules.values()) if module is not None
        and getattr(module, '__file__', None) and str(module.__file__).endswith('.py')
        and Path(module.__file__).resolve().is_relative_to(core_root)]
    modules = [sys.modules[name] for name in VALIDATOR_MODULES] + [sys.modules[__name__]] + core_modules
    for module in modules:
        spec = module.__spec__
        name = spec.name if spec else module.__name__
        source_hash, matches = _capture_source(name, module.__file__, module.__loader__)
        if not matches or source_hash != _hash(module.__file__):
            raise ValueError('Current source and loader code differ')
    if checksum({p: _hash(ROOT / p) for p in VALIDATOR_FILES}) != _VALIDATOR_SOURCE_AT_CAPTURE_SHA256:
        raise ValueError('Validator source changed since capture')


def pins(data_dir):
    # Import is intentionally mandatory: absence of v3 is an error, never a skip.
    from wildfire_control_v3 import population_engine_v3 as engine
    from wildfire_control_v3 import (INTERFACE_VERSION, _LOADED_PACKAGE_CODE_MATCH,
        _LOADED_PACKAGE_SOURCE_SHA256, _source_sha256)
    import wildfire_control_v3 as package
    if (not _LOADED_PACKAGE_CODE_MATCH or
            _source_sha256(package.__file__) != _LOADED_PACKAGE_SOURCE_SHA256 or
            checksum({p: _hash(ROOT / p) for p in VALIDATOR_FILES}) != _VALIDATOR_SOURCE_AT_CAPTURE_SHA256):
        raise ValueError('Package or validator source changed since capture')
    check_source_loader_consistency()
    lock = json.loads(LOCK.read_text())
    manifest = verify_package(data_dir)
    manifest_hash = _hash(Path(data_dir) / 'manifest.json')
    if (manifest_hash != lock['manifest_sha256'] or
        implementation_digest() != lock['engine_implementation_sha256'] or
        registry()['sha256'] != lock['lifecycle_sha256']):
        raise ValueError('Trusted release, core or lifecycle pin mismatch')
    v3_files = sorted((ROOT / 'envs/wildfire_control_v3/src/wildfire_control_v3').glob('*.py'))
    return manifest, dict(dataset_manifest_sha256=manifest_hash,
        dataset_revision=lock['revision'], core_sha256=implementation_digest(),
        lifecycle_sha256=registry()['sha256'], interface_version=INTERFACE_VERSION,
        population_version=engine.VERSION, population_engine_sha256=engine._identity(),
        v3_sources_sha256=checksum({str(p.relative_to(ROOT)): _hash(p) for p in v3_files}),
        validator_sha256=checksum({p: _hash(ROOT / p) for p in VALIDATOR_FILES}),
        source_attestation='current_source_and_loader_only; immutable_sources_from_before_import_required')


def make_v3(data_dir, runtime):
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    base, entry = make_environment(data_dir, runtime, allow_unapproved_data=True)
    return PopulationEngineV3(base.inputs, base.network.document,
        expected_inputs_sha256=base.inputs['sha256'], expected_network_sha256=base.network.sha256,
        allow_unapproved_data=True), entry


def clone(env):
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    other = PopulationEngineV3(env.inputs, env.network.document,
        expected_inputs_sha256=env.inputs['sha256'], expected_network_sha256=env.network.sha256,
        allow_unapproved_data=env.inputs['approval_status'] != 'approved_synthetic_fixture')
    observation = other.restore(copy.deepcopy(env.checkpoint()))
    return other, observation


def query_independence(env):
    baseline, _ = clone(env)
    other, _ = clone(env)
    for _ in range(2):
        other.get_situation(); other.get_reports(); other.check_action({'advance_minutes': 60})
        other.route_options(sorted(other._known_resources)[0])
    for _ in range(3):
        a, b = baseline.advance_time(20), other.advance_time(20)
        if (a != b or baseline.fire.checkpoint() != other.fire.checkpoint()
                or baseline.bus.checkpoint() != other.bus.checkpoint()):
            raise ValueError('Public queries changed continuation')
    return {'passed': True}


def continuation(env, policy):
    original = env.checkpoint()
    copies = [clone(env), clone(env)]
    if any(other.checkpoint() != original for other, _ in copies):
        raise ValueError('Full versioned checkpoint changed on restore')
    policies = [make_policy(policy.name), make_policy(policy.name)]
    for replica in policies:
        replica.restore(copy.deepcopy(policy.checkpoint()))
    observations = [item[1] for item in copies]
    steps = []
    for _ in range(2):
        if copies[0][0].terminated or copies[0][0].truncated:
            break
        states = []
        for i, (other, _) in enumerate(copies):
            public = PublicFacade(other, [])
            action = policies[i].action(public, observations[i])
            result = commit(public, action)
            if 'observation' not in result:
                raise ValueError('Continuation policy call rejected')
            observations[i] = result['observation']
            states.append((action, checksum(result), checksum(other.checkpoint()),
                           checksum(policies[i].checkpoint())))
        if states[0] != states[1]:
            raise ValueError('Full checkpoint continuation differs')
        steps.append(states[0])
    if not steps:
        raise ValueError('No continuation transition available')
    return steps


def advance_continuations(pending, actual):
    """Compare this live transition to each overlapping restored continuation."""
    unfinished = []
    for item in pending:
        record, predicted = item['record'], item['predicted']
        index = record['live_transitions']
        if actual != predicted[index]:
            raise ValueError('Live checkpoint continuation differs')
        record['live_transitions'] += 1
        if record['live_transitions'] == record['clone_transitions']:
            record['passed'] = True
        else:
            unfinished.append(item)
    return unfinished


def boundary_coverage(audit, decision_phases, checks):
    encountered = set(decision_phases)
    for record in audit:
        encountered.update(unit.get('state') for unit in record.get('resources', {}).values())
    tested = {c['boundary'] for c in checks if c.get('passed') is True}
    return {phase: {'encountered': phase in encountered if phase not in ('hourly', 'midday') else phase in decision_phases,
                    'status': 'exercised' if phase in tested else
                              'unsupported' if phase in encountered or phase in decision_phases else 'not_exercised'}
            for phase in BOUNDARIES}


class Interrupted(Exception):
    pass


def run_cell(env, identity, check_budget):
    policy = make_policy(identity['policy'])
    observation, _ = env.reset(identity['case_id'])
    calls, checked, resumes, decision_phases, pending = [], set(), [], set(), []
    query = query_independence(env)
    observer = MovementObserver(env)
    public = PublicFacade(env, calls)
    public.get_situation(); public.get_reports(limit=20)
    while not (env.terminated or env.truncated):
        check_budget()
        phases = {u['state'] for u in env.ops.resources.values()}
        decision_phases.update(phases)
        candidates = sorted((phases & set(BOUNDARIES)) - checked)
        if env.minute >= 60: decision_phases.add('hourly')
        if env.minute >= 720: decision_phases.add('midday')
        if env.minute >= 60 and 'hourly' not in checked: candidates.append('hourly')
        if env.minute >= 720 and 'midday' not in checked: candidates.append('midday')
        label = candidates[0] if candidates else None
        if label:
            predicted = continuation(env, policy)
            record = {'boundary': label, 'passed': False, 'clone_transitions': len(predicted),
                      'live_transitions': 0}
            resumes.append(record)
            pending.append({'record': record, 'predicted': predicted})
            checked.add(label)
        action = policy.action(public, observation)
        result = commit(public, action)
        if 'observation' not in result:
            raise ValueError('Policy call rejected')
        observation = result['observation']
        if pending:
            actual = (action, checksum(result), checksum(env.checkpoint()), checksum(policy.checkpoint()))
            pending = advance_continuations(pending, actual)
    check_budget()
    verification = env.verify_episode()
    check_budget()
    checks = audit_runtime(env, calls)
    movement = observer.snapshot()
    findings = list(checks['findings'])
    for code, entry in movement['findings'].items():
        findings.append(dict(code=code, category='contradiction', count=entry['count']))
    coverage = checks['coverage']
    coverage.pop('actual_routes_and_clearance', None)
    unverified = sum(movement['coverage'].get(k, 0) for k in ('journey_unverifiable',
        'position_unverifiable', 'multi_minute_ticks_unobserved', 'road_geometry_unverifiable'))
    capacity = movement['coverage'].get('capacity_ticks_checked', 0)
    hard = any(f['category'] in ('blocking', 'contradiction', 'error') for f in findings)
    verified = {k: verification.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement')}
    boundaries = boundary_coverage(env._audit, decision_phases, resumes)
    unsupported_boundaries = any(item['status'] == 'unsupported' for item in boundaries.values())
    complete = (env.terminated and not env.truncated and env.minute == 1440 and all(verified.values())
                and not hard and not unverified and capacity == env.minute and bool(resumes)
                and all(c['passed'] for c in resumes) and query['passed'] and not unsupported_boundaries)
    check_budget()
    return dict(identity, status='complete' if complete else 'blocked' if unsupported_boundaries and not hard else 'failed', minute=env.minute,
        verification=verified, pins=dict(inputs_sha256=env.inputs['sha256'],
        network_sha256=env.network.sha256, scenario_sha256=env._scenario['sha256']),
        findings=aggregate_findings(findings), operation_coverage=coverage,
        movement_evidence=movement['coverage'], resume_checks=resumes, query_independence=query,
        checkpoint_boundaries=boundaries)


def matrix_status(report):
    expected = report['expected_cells']
    pairs = {(c['runtime_id'], c['case_id']) for c in expected}
    domain = {(runtime, case, policy) for runtime, case in pairs for policy in POLICY_NAMES}
    if len(pairs) != 12 or {cell_identity(c) for c in expected} != domain:
        return 'failed'
    static = report.get('static_cases', [])
    identities = [(c.get('runtime_id'), c.get('case_id')) for c in static]
    if len(identities) != 12 or len(set(identities)) != 12 or set(identities) != pairs:
        return 'incomplete'
    if not report.get('pins'):
        return 'incomplete'
    for cell in report['cells']:
        if cell.get('status') != 'complete':
            continue
        boundaries = cell.get('checkpoint_boundaries', {})
        checks = {c.get('boundary'): c for c in cell.get('resume_checks', []) if c.get('passed') is True}
        if set(boundaries) != set(BOUNDARIES):
            return 'blocked'
        for name, evidence in boundaries.items():
            status = evidence.get('status')
            if (type(evidence.get('encountered')) is not bool or
                status not in ('exercised', 'not_exercised') or
                (evidence['encountered'] and status != 'exercised') or
                (status == 'exercised' and (name not in checks or
                    checks[name].get('live_transitions') not in (1, 2) or
                    checks[name].get('clone_transitions') != checks[name].get('live_transitions')))):
                return 'blocked'
    return validate_matrix(report)


def publish(output, report, before_replace=None):
    # Explicit allowlist prevents accidental publication of journals or raw reports.
    allowed = ('version', 'status', 'pins', 'expected_cells', 'cells', 'static_cases',
               'runtime_findings', 'limitations', 'reason', 'elapsed_seconds', 'workers')
    public = {k: report[k] for k in allowed if k in report}
    cell_fields = ('runtime_id', 'case_id', 'policy', 'status', 'minute', 'verification',
                   'pins', 'findings', 'operation_coverage', 'movement_evidence',
                   'resume_checks', 'query_independence', 'checkpoint_boundaries')
    public['cells'] = [{k: c[k] for k in cell_fields if k in c} for c in report['cells']]
    with tempfile.NamedTemporaryFile('w', dir=output, delete=False, encoding='utf-8') as f:
        name = f.name
        try:
            json.dump(public, f, indent=2, allow_nan=False); f.write('\n')
        except BaseException:
            os.unlink(name); raise
    try:
        if before_replace is not None:
            before_replace()
        os.replace(name, output / 'report.json')
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def hard_timeout(seconds):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Hard wall timeout requires the main thread')
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expire(signum, frame):
        raise Interrupted('wall_budget')
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


def run(data_dir, output, *, wall_seconds=3600, max_cells=None):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    with hard_timeout(wall_seconds):
        return _run(data_dir, output, wall_seconds=wall_seconds, max_cells=max_cells)


def _run(data_dir, output, *, wall_seconds=3600, max_cells=None):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    if max_cells is not None and (type(max_cells) is not int or max_cells < 1):
        raise ValueError('max_cells must be positive')
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    report = dict(version=VERSION, status='incomplete', cells=[], expected_cells=[], static_cases=[],
        elapsed_seconds=0.0, workers=1,
        runtime_findings=[], limitations=['No checkpoint resume: repeat the complete matrix in a new output directory.',
        'Development exercises and modeled bounds are not empirical calibration.',
        'Private journals, reports and audits are excluded.',
        'Sources must be immutable before imports and throughout the run; current loader/source comparisons cannot prove what executed during import or detect in-memory changes.',
        'SIGALRM interrupts Python when the interpreter regains control; native code may delay delivery.',
        'Transient resource phases without a decision-boundary checkpoint are unsupported.'])
    publish(output, report)
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    def check_budget():
        if stopped or time.monotonic() - started >= wall_seconds:
            raise Interrupted('signal' if stopped else 'wall_budget')
    def publish_progress(before_replace=None):
        report['elapsed_seconds'] = round(time.monotonic() - started, 4)
        publish(output, report, before_replace=before_replace)
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        check_budget()
        manifest, pinned = pins(data_dir)
        expected = expected_cells(manifest)
        report.update(pins=pinned, expected_cells=expected,
                      cells=[dict(c, status='not_run') for c in expected])
        publish_progress()
        bounds = json.loads(BOUNDS.read_text())
        for runtime_id in sorted(manifest['runtimes']):
            check_budget()
            env, entry = make_v3(data_dir, runtime_id)
            screened = static_audit(env.inputs, env.network.document, bounds, None,
                                    entry['scenario_ids'], entry['allow_unapproved_data'])
            report['static_cases'].extend(dict(runtime_id=runtime_id, case_id=s['case'],
                findings=aggregate_findings(s['findings'])) for s in screened)
        for index, identity in enumerate(expected):
            check_budget()
            if max_cells is not None and index >= max_cells:
                report['reason'] = 'selected_subset'; break
            if pins(data_dir)[1] != pinned: raise ValueError('Source or dataset pins changed')
            report['cells'][index] = dict(identity, status='incomplete')
            publish_progress()
            env, _ = make_v3(data_dir, identity['runtime_id'])
            cell = run_cell(env, identity, check_budget)
            check_budget()
            report['cells'][index] = cell
            publish_progress()
        check_budget()
        if pins(data_dir)[1] != pinned: raise ValueError('Source or dataset pins changed')
        check_budget()
        if len(report['static_cases']) != 12: raise ValueError('Static coverage incomplete')
        report['status'] = matrix_status(report)
        if max_cells is not None and max_cells < len(expected): report['status'] = 'incomplete'
        # Staging may be slow: gate success immediately before the atomic rename.
        publish_progress(before_replace=check_budget if report['status'] == 'complete' else None)
        check_budget()
        return report
    except BaseException as exc:
        # Disarm before writing the incomplete report; the enclosing guard restores the prior timer.
        signal.setitimer(signal.ITIMER_REAL, 0)
        report['status'] = 'incomplete'
        report['reason'] = str(exc) if isinstance(exc, Interrupted) and str(exc) in ('signal', 'wall_budget') else 'validation_error'
        publish_progress()
        if isinstance(exc, Interrupted): return report
        raise
    finally:
        for sig, handler in handlers.items(): signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--wall-seconds', type=float, default=3600)
    parser.add_argument('--max-cells', type=int)
    args = parser.parse_args()
    try:
        result = run(args.data_dir, args.output, wall_seconds=args.wall_seconds, max_cells=args.max_cells)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'incomplete', 'reason': 'validation_error'}))
        return 2
    print(json.dumps({'status': result['status'], 'reason': result.get('reason'),
                      'elapsed_seconds': result['elapsed_seconds'], 'workers': result['workers']}))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
