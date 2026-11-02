"""Replay immutable evaluation evidence with an explicitly reviewed observer only.

One CPU worker; wall/signal/call budgets are checked between atomic calls.
Completed cells are resumable. An interrupted cell restarts from its recorded
reset on resume. This never runs a policy or writes to the source evaluation.
"""
import argparse
import copy
import difflib
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from eval import evaluate_packaged_runtime as runner
from eval.hub_runtime import _json, make_environment, verify_package
from eval.incident_day_movement_checks import MovementObserver
from eval.validate_source_runtime_matrix import PublicFacade, checksum
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.outcomes import VERSION as REWARD_VERSION, recompute
from wildfire_core.catalog_lifecycle import registry

VERSION = 'wildfire-evaluation-observer-revalidation-v1'
OBSERVER = 'eval/incident_day_movement_checks.py'
SELF = 'eval/revalidate_evaluation.py'
VALIDATOR_PATHS = ('eval/evaluate_packaged_runtime.py', 'eval/hub_runtime.py',
    'eval/validate_source_runtime_matrix.py', 'eval/incident_day_policies.py',
    'eval/incident_day_action_checks.py', OBSERVER, 'eval/check_incident_day_plausibility.py',
    'eval/run_incident_day.py', 'data/incident_day_v2/plausibility/bounds-v1.json',
    'data/incident_day_v2/plausibility/action-bounds-v1.json')
MAX_BYTES = 128 * 1024 * 1024
FINISHED = ('complete', 'blocked', 'failed')


class Mismatch(ValueError):
    pass


class BudgetStop(Exception):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def same(actual, expected, label):
    if checksum(actual) != checksum(expected):
        raise Mismatch(label + ' differs from frozen evidence')


def read_bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Invalid or oversized evidence file: ' + str(path))
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError('Evidence file exceeds byte limit')
    return raw


def evidence(raw):
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        plain = stream.read(MAX_BYTES + 1)
    if len(plain) > MAX_BYTES:
        raise ValueError('Decompressed evidence exceeds byte limit')
    return _json(plain)


def exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_json(path, value):
    # Only the revalidator's report is replaceable; cell/source evidence is exclusive.
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False).encode() + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def code_identity(freeze, reviewed_observer_sha256):
    commit = git('rev-parse', '--verify', freeze['code_commit'] + '^{commit}').decode().strip()
    if commit != freeze['code_commit']:
        raise ValueError('Source freeze must name the full source Git commit')
    changes = git('diff', '--name-only', commit, '--').decode().splitlines()
    untracked = git('ls-files', '--others', '--exclude-standard').decode().splitlines()
    permitted = lambda p: p in (OBSERVER, SELF) or p.startswith(('tests/', 'docs/'))
    if any(not permitted(path) for path in changes + untracked):
        raise ValueError('Unreviewed change outside observer/revalidator/tests/docs')
    before = git('show', commit + ':' + OBSERVER)
    after = (ROOT / OBSERVER).read_bytes()
    if sha(after) != reviewed_observer_sha256 or before == after:
        raise ValueError('Observer delta must match the explicitly reviewed SHA256')
    current_files = {path: sha((ROOT / path).read_bytes()) for path in VALIDATOR_PATHS}
    if checksum(current_files) != runner.validator_digest():
        raise ValueError('Revalidator validator-path contract is stale')
    source_files = dict(current_files, **{OBSERVER: sha(before)})
    if checksum(source_files) != freeze['validator_sha256']:
        raise ValueError('Source validator does not match the source Git commit/freeze')
    if (implementation_digest() != freeze['implementation_sha256']
            or registry()['sha256'] != freeze['lifecycle_sha256']
            or freeze.get('reward_identity') != REWARD_VERSION):
        raise ValueError('Engine, lifecycle or reward changed from source freeze')
    return dict(source_git_commit=commit, current_git_commit=git('rev-parse', 'HEAD').decode().strip(),
        source_validator_sha256=freeze['validator_sha256'], corrected_validator_sha256=checksum(current_files),
        source_observer_sha256=sha(before), reviewed_observer_sha256=sha(after),
        revalidator_sha256=sha(Path(__file__).read_bytes()),
        observer_patch=''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                                                   fromfile='source/' + OBSERVER, tofile='reviewed/' + OBSERVER)))


def package_identity(data_dir, freeze):
    package = verify_package(data_dir)
    if (checksum(package) != freeze['package_sha256']
            or sha(read_bytes(Path(data_dir) / 'manifest.json')) != freeze['manifest_sha256']):
        raise ValueError('Package differs from source freeze')
    rows = runner.evaluation_rows(data_dir, package)
    frozen_rows = {(row['runtime_id'], row['task_id']): row for row in freeze['tasks']}
    if len(frozen_rows) != len(freeze['tasks']):
        raise ValueError('Duplicate frozen evaluation row')
    same(rows_to_list(rows), rows_to_list(frozen_rows), 'Frozen evaluation rows')
    return package, rows


def rows_to_list(rows):
    return [rows[key] for key in sorted(rows)]


def source_pins(freeze):
    return {key: freeze[key] for key in ('implementation_sha256', 'lifecycle_sha256',
                                        'package_sha256', 'validator_sha256')}


def filename(cell):
    values = [cell[key] for key in ('runtime_id', 'case_id', 'policy')]
    if any(not isinstance(v, str) or not v or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-' for c in v)
           for v in values):
        raise ValueError('Invalid cell file identity')
    return '-'.join(values) + '.json.gz'


def validate_source_report(report, freeze, package):
    if (report.get('version') != runner.VERSION or report.get('split') != 'evaluation'
            or report.get('training_eligible') is not False):
        raise ValueError('Unsupported source evaluation report')
    same(report.get('pins'), source_pins(freeze), 'Source report pins')
    expected = runner.expected_cells(package)
    same(report.get('expected_cells'), expected, 'Source report domain')
    same(freeze['cells'], expected, 'Source freeze domain')
    same([runner._identity(c) for c in report['cells']], [runner._identity(c) for c in expected], 'Source cell identities')
    if report.get('policies') != list(runner.POLICIES):
        raise ValueError('Source policy domain differs')
    return expected


def snapshot_source(output, source_report, source_freeze, data_dir, reviewed_sha, adverse_outcome_policy='withhold'):
    report_raw, freeze_raw = read_bytes(source_report), read_bytes(source_freeze)
    report, freeze = _json(report_raw), _json(freeze_raw)
    if freeze.get('version') != 'evaluation-freeze-v1' or freeze.get('training') is not False:
        raise ValueError('Unsupported source freeze')
    code = code_identity(freeze, reviewed_sha)
    package, rows = package_identity(data_dir, freeze)
    expected = validate_source_report(report, freeze, package)
    # Read finished evidence only. The running report may advance after this
    # atomic JSON snapshot; this generation deliberately does not absorb it.
    artifacts = []
    for cell in report['cells']:
        if cell['status'] in FINISHED:
            name = filename(cell)
            path = Path(source_report).parent / 'private' / name
            raw = read_bytes(path)
            item = evidence(raw)
            validate_saved_cell(cell, item, rows[(cell['runtime_id'], cell['case_id'])], freeze)
            artifacts.append((name, raw))
        elif cell['status'] not in ('not_run', 'incomplete'):
            raise ValueError('Unknown source cell status')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / 'source').mkdir(mode=0o700)
    (output / 'source/private').mkdir(mode=0o700)
    (output / 'cells').mkdir(mode=0o700)
    exclusive(output / 'source/report.json', report_raw)
    exclusive(output / 'source/freeze.json', freeze_raw)
    hashes = {'report.json': sha(report_raw), 'freeze.json': sha(freeze_raw)}
    for name, raw in artifacts:
        exclusive(output / 'source/private' / name, raw)
        hashes['private/' + name] = sha(raw)
    manifest = dict(version=VERSION, adverse_outcome_policy=adverse_outcome_policy, source_report_path=str(Path(source_report).resolve()),
        source_freeze_path=str(Path(source_freeze).resolve()), data_dir=str(Path(data_dir).resolve()),
        source_hashes=hashes, code=code, expected_cells=expected,
        python_version=sys.version, snapshot_finished_cells=len(artifacts))
    manifest['sha256'] = checksum(manifest)
    exclusive(output / 'source_manifest.json', json.dumps(manifest, sort_keys=True).encode() + b'\n')
    return manifest


def load_snapshot(output, data_dir, reviewed_sha):
    manifest = _json(read_bytes(output / 'source_manifest.json'))
    same(manifest['sha256'], checksum({k: v for k, v in manifest.items() if k != 'sha256'}), 'Snapshot manifest checksum')
    if manifest.get('version') != VERSION or manifest['data_dir'] != str(Path(data_dir).resolve()):
        raise ValueError('Revalidation resume identity differs')
    for relative, expected in manifest['source_hashes'].items():
        if relative.startswith('/') or '..' in Path(relative).parts:
            raise ValueError('Invalid snapshot path')
        same(sha(read_bytes(output / 'source' / relative)), expected, 'Snapshot evidence hash')
    freeze = _json(read_bytes(output / 'source/freeze.json'))
    same(code_identity(freeze, reviewed_sha), manifest['code'], 'Revalidation code identity')
    package, rows = package_identity(data_dir, freeze)
    report = _json(read_bytes(output / 'source/report.json'))
    validate_source_report(report, freeze, package)
    return manifest, freeze, report, rows


def validate_saved_cell(cell, saved, row, freeze):
    trace = saved['trajectory']
    for key in ('implementation_sha256', 'inputs_sha256', 'network_sha256', 'scenario_sha256'):
        wanted = freeze[key] if key == 'implementation_sha256' else cell['pins'][key]
        same(trace.get(key), wanted, 'Source trajectory ' + key)
    if (trace['task_id'] != cell['case_id'] or trace['seed'] != row['episode_seed']
            or saved['episode_seed'] != row['episode_seed'] or trace['scenario_sha256'] != row['scenario_sha256']):
        raise Mismatch('Source task, seed or scenario differs from frozen row')
    same(saved['outcome'], cell['outcome'], 'Source report outcome')
    same(saved['resume_checks'], cell['resume_checks'], 'Source continuation evidence')
    if (not cell.get('resume_checks') or any(c.get('passed') is not True for c in cell['resume_checks'])
            or cell.get('query_independence', {}).get('passed') is not True
            or any(saved['verification'].get(k) is not True or cell['verification'].get(k) is not True
                   for k in ('passed', 'exact_replay', 'independent_reward_agreement'))):
        raise Mismatch('Original execution verification/continuation checks did not pass')
    if cell['minute'] != 1440 or len(saved['audit']) != 1441:
        raise Mismatch('Original cell lacks a full day audit')


def reward_agreement(production, independent):
    if set(production) != set(independent):
        raise Mismatch('Independent reward fields differ')
    for key, value in production.items():
        if type(value) is float:
            if not math.isclose(value, independent[key], rel_tol=1e-12, abs_tol=1e-12):
                raise Mismatch('Independent reward differs: ' + key)
        elif value != independent[key]:
            raise Mismatch('Independent outcome differs: ' + key)


class VerifiedReplay:
    """finish_cell's verification provider, created only after exact comparisons."""
    def __init__(self, env, independent):
        self._env, self._independent = env, independent

    def __getattr__(self, key):
        return getattr(self._env, key)

    def verify_episode(self):
        return dict(passed=True, exact_replay=True, independent_reward_agreement=True,
                    outcomes=copy.deepcopy(self._independent))


def adverse_recovery_evidence(result, findings, env):
    """Narrow reporting eligibility; never changes the runner's strict verdict."""
    known = 'recovery_after_shift_end_egress_unavailable'
    if (result['status'] != 'blocked' or not findings
            or any(f.get('category') != 'unsupported' or f.get('code') != known for f in findings)
            or not result.get('recorded_call_revalidation', {}).get('passed')
            or any(result.get('verification', {}).get(k) is not True for k in
                   ('passed', 'exact_replay', 'independent_reward_agreement'))
            or runner.uninspectable_coverage(result.get('operation_coverage', {}))
            or result.get('outcome', {}).get('score_eligible') is not True):
        return []
    coverage = result.get('movement_evidence', {})
    if (coverage.get('ticks_observed') != 1440 or coverage.get('capacity_ticks_checked') != 1440
            or coverage.get('timed_positions_checked', 0) <= 0
            or any(coverage.get(k, 0) for k in ('journey_unverifiable', 'position_unverifiable',
                   'multi_minute_ticks_unobserved', 'road_geometry_unverifiable'))):
        return []
    specs = {u['id']: u for u in env._scenario.get('resources', [])}
    rows = env._audit
    if len(rows) != 1441 or any(r['minute'] != i for i, r in enumerate(rows)):
        return []
    evidence = []
    for finding in findings:
        rid, minute = finding.get('resource'), finding.get('minute')
        unit = specs.get(rid)
        if unit is None or unit.get('kind') not in ('engine', 'crew') or type(minute) is not int:
            return []
        deadline = unit.get('duty_end_at')
        if type(deadline) is not int or not deadline < minute <= 1440:
            return []
        final = rows[-1]['resources'][rid]
        base = unit['base_position']
        if final['state'] != 'off_duty' or math.dist(final['position'], base) > .01:
            return []
        matching = []
        for report in env.bus.reports:
            observed = report.get('observed_at')
            payload = report.get('payload', {})
            if (report.get('kind') != 'egress_unavailable' or payload.get('resource_id') != rid
                    or type(observed) is not int or not 0 <= observed <= deadline
                    or not isinstance(payload.get('position'), list)):
                continue
            waiting = [r['resources'][rid] for r in rows[observed:minute]]
            position = payload['position']
            if (waiting and waiting[0]['state'] == 'needs_assistance'
                    and all(math.dist(r['position'], position) <= 1e-6 and
                        (r['state'] == 'needs_assistance' or
                         (r['state'] == 'blocked' and r.get('blocked_state') == 'returning'
                          and type(r.get('return_recovery_preflight_at')) is int
                          and r['return_recovery_preflight_at'] == observed + i))
                        for i, r in enumerate(waiting))):
                matching.append(observed)
        if not matching:
            return []
        # No productive work, reassignment, refill or rest after the deadline.
        if (any(r['resources'][rid]['state'] not in ('needs_assistance', 'blocked', 'returning', 'off_duty')
                for r in rows[deadline:])
                or any(w.get('resource_id') == rid for r in rows[deadline:] for w in r.get('applied_work', []))):
            return []
        arrived = next((r['minute'] for r in rows[minute:]
                        if r['resources'][rid]['state'] == 'off_duty'
                        and math.dist(r['resources'][rid]['position'], base) <= .01), None)
        if arrived is None:
            return []
        evidence.append(dict(resource_id=rid, egress_unavailable_at=min(matching),
                             duty_end_at=deadline, late_recovery_at=minute, arrived_off_duty_at=arrived))
    return evidence


def apply_reporting(result, findings, env, source_cell, policy):
    result['source_status'] = source_cell['status']
    result['source_score'] = source_cell.get('score')
    result['adverse_outcome_policy'] = policy
    result['reported_score'] = result.get('score')
    result['operational_status'] = 'no_blocking_findings' if result['status'] == 'complete' else 'unresolved'
    proof = adverse_recovery_evidence(result, findings, env)
    result['adverse_recovery_evidence'] = proof
    if proof:
        result['operational_status'] = 'adverse'
        raw_score = result['outcome'].get('graded_score')
        if policy == 'score' and type(raw_score) in (int, float) and math.isfinite(raw_score):
            result['reported_score'] = raw_score
    # status/score/findings remain exactly the strict finish_cell result.


def replay_cell(cell, saved, row, freeze, data_dir, check_budget, adverse_outcome_policy='withhold'):
    validate_saved_cell(cell, saved, row, freeze)
    check_budget()
    env, _ = make_environment(data_dir, cell['runtime_id'], allow_unapproved_data=True)
    initial, _ = env.reset(cell['case_id'], row['episode_seed'])
    runner.bind_scenario(env, cell, row)
    same(env._scenario.get('evaluation', {}), saved['evaluation_metadata'], 'Evaluation configuration')
    same(initial, saved['trajectory']['initial_observation'], 'Initial observation')
    observer = MovementObserver(env)
    calls = []
    public = PublicFacade(env, calls)
    transitions = saved['trajectory']['transitions']
    position = 0
    for index, original in enumerate(saved['calls']):
        check_budget(True)
        same((env.minute, env.decision), (original['before_minute'], original['before_decision']), 'Call start clock')
        result = public.execute(original['name'], copy.deepcopy(original['arguments']))
        same(calls[-1], original, f'Retained call {index}')
        if 'observation' in result:
            if position >= len(transitions):
                raise Mismatch('Extra committed transition')
            same(env._actions[-1], transitions[position], f'Committed transition {position}')
            same(checksum(result['observation']), transitions[position]['observation_sha256'], 'Observation digest')
            position += 1
    same(position, len(transitions), 'Committed transition count')
    actual_trace = env.trajectory()
    same(actual_trace, saved['trajectory'], 'Complete trajectory, query log and rejected calls')
    for label, actual, original in (
        ('Minute audit', env._audit, saved['audit']), ('Reports', env.bus.reports, saved['reports']),
        ('Orders', list(env.ops.orders.values()), saved['orders']), ('Outcome', env.outcomes(), saved['outcome'])):
        same(actual, original, label)
    if not env.terminated or env.truncated or env.minute != 1440:
        raise Mismatch('Replay did not terminate as a full day')
    independent = recompute(env._audit, terminal=env.terminated, abandoned=env.abandoned, censored=env.censored)
    same(independent, saved['verification']['outcomes'], 'Original independent reward')
    reward_agreement(env.outcomes(), independent)
    check_budget()
    # The proof above replaces verify_episode's redundant second day replay.
    identity = {key: cell[key] for key in ('runtime_id', 'case_id', 'policy')}
    result, detailed = runner.finish_cell(identity, VerifiedReplay(env, independent), observer, calls,
                                         saved['resume_checks'], cell['query_independence'])
    result['recorded_call_revalidation'] = dict(passed=True, calls=len(calls), transitions=position,
        queries=len(actual_trace['queries']), trajectory_sha256=checksum(actual_trace),
        audit_sha256=checksum(env._audit), reports_sha256=checksum(env.bus.reports),
        orders_sha256=checksum(saved['orders']), outcome_sha256=checksum(saved['outcome']),
        independent_reward_sha256=checksum(independent), policy_executed=False,
        observation_comparison='Saved canonical digest per committed observation; initial observation compared in full',
        read_result_limit='Journal retains only selected result fields; route query hashes are checked by exact query-log equality')
    result['source_elapsed_seconds'] = cell.get('elapsed_seconds')
    result['inherited_policy_checks'] = dict(source='immutable source/report.json and cell evidence',
        reexecuted=False, resume_checks_sha256=checksum(saved['resume_checks']),
        query_independence_sha256=checksum(cell['query_independence']))
    apply_reporting(result, detailed['findings'], env, cell, adverse_outcome_policy)
    return dict(result=result, corrected_findings=detailed['findings'],
                corrected_observer=detailed['movement_observer'], original_findings=saved['findings'])



def reportable_adverse(cell):
    """Validate the persisted reporting proof, not just its display label."""
    known = 'recovery_after_shift_end_egress_unavailable'
    findings, proof = cell.get('findings', []), cell.get('adverse_recovery_evidence', [])
    outcome = cell.get('outcome', {})
    raw_score = outcome.get('graded_score')
    if (cell.get('status') != 'blocked' or cell.get('score') is not None
            or cell.get('adverse_outcome_policy') != 'score' or cell.get('operational_status') != 'adverse'
            or cell.get('minute') != 1440 or cell.get('split') != 'evaluation'
            or cell.get('training_eligible') is not False
            or cell.get('recorded_call_revalidation', {}).get('passed') is not True
            or cell.get('recorded_call_revalidation', {}).get('policy_executed') is not False
            or outcome.get('score_eligible') is not True or outcome.get('completed_minutes') != 1440
            or type(raw_score) not in (int, float) or not math.isfinite(raw_score)
            or cell.get('reported_score') != raw_score
            or any(cell.get('verification', {}).get(k) is not True for k in
                   ('passed', 'exact_replay', 'independent_reward_agreement'))
            or not cell.get('resume_checks') or any(c.get('passed') is not True for c in cell['resume_checks'])
            or cell.get('query_independence', {}).get('passed') is not True
            or runner.uninspectable_coverage(cell.get('operation_coverage', {}))
            or not isinstance(findings, list) or not findings or not isinstance(proof, list) or not proof):
        return False
    if any(f.get('category') != 'unsupported' or f.get('code') != known
           or type(f.get('count')) is not int or f['count'] <= 0 for f in findings):
        return False
    if sum(f['count'] for f in findings) != len(proof):
        return False
    coverage = cell.get('movement_evidence', {})
    if (coverage.get('ticks_observed') != 1440 or coverage.get('capacity_ticks_checked') != 1440
            or coverage.get('timed_positions_checked', 0) <= 0
            or any(coverage.get(k, 0) for k in ('journey_unverifiable', 'position_unverifiable',
                    'multi_minute_ticks_unobserved', 'road_geometry_unverifiable'))):
        return False
    identities = set()
    for item in proof:
        resource = item.get('resource_id')
        times = [item.get(k) for k in ('egress_unavailable_at', 'duty_end_at',
                                       'late_recovery_at', 'arrived_off_duty_at')]
        if not isinstance(resource, str) or not resource or any(type(t) is not int for t in times):
            return False
        observed, deadline, late, arrived = times
        key = (resource, late)
        if not 0 <= observed <= deadline < late <= arrived <= 1440 or key in identities:
            return False
        identities.add(key)
    return True


def refresh_revalidation_report(report):
    runner.refresh_report(report)  # The strict verdict always has precedence.
    report['reporting_status'] = report['status']
    if (report['status'] != 'blocked' or report.get('adverse_outcome_policy') != 'score'
            or report['baseline_coverage_contract']['unobserved_required']):
        return
    adverse = [c for c in report['cells'] if c.get('status') != 'complete']
    if adverse and all(reportable_adverse(c) for c in adverse):
        report['reporting_status'] = 'complete_with_adverse_outcomes'


def run(source_report, source_freeze, data_dir, output, *, reviewed_observer_sha256,
        resume=False, wall_seconds=1800, max_cells=None, max_calls=None, adverse_outcome_policy='withhold'):
    if adverse_outcome_policy not in ('withhold', 'score'):
        raise ValueError('Unknown adverse outcome policy')
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    for value in (max_cells, max_calls):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError('Positive integer cell/call budgets required')
    output = Path(output).absolute()
    for protected in (Path(source_report).resolve().parent, Path(data_dir).resolve()):
        if output.resolve() == protected or protected in output.resolve().parents or output.resolve() in protected.parents:
            raise ValueError('Output must be separate from source report and package')
    if output.is_symlink():
        raise ValueError('Output must not be a symlink')
    if not resume:
        snapshot_source(output, source_report, source_freeze, data_dir, reviewed_observer_sha256, adverse_outcome_policy)
    manifest, freeze, report, rows = load_snapshot(output, data_dir, reviewed_observer_sha256)
    if (manifest['source_report_path'] != str(Path(source_report).resolve())
            or manifest['source_freeze_path'] != str(Path(source_freeze).resolve())):
        raise ValueError('Resume source paths differ')
    if manifest['adverse_outcome_policy'] != adverse_outcome_policy:
        raise ValueError('Resume adverse outcome policy differs; choose a new output')
    original_cells = copy.deepcopy(report['cells'])
    report['source_report_status'] = report.get('status')
    report['source_report_summary'] = copy.deepcopy(report.get('summary'))
    report['source_execution_elapsed_seconds'] = report.get('elapsed_seconds')
    report['version'] = VERSION
    report['source_pins'] = report.pop('pins')
    report['pins'] = dict(report['source_pins'], validator_sha256=manifest['code']['corrected_validator_sha256'])
    report['source_snapshot_sha256'] = manifest['sha256']
    report['code'] = manifest['code']
    report['cells'] = [dict(c, status='not_run') for c in report['expected_cells']]
    report['revalidation'] = True
    report['adverse_outcome_policy'] = adverse_outcome_policy
    report['workers'] = 1
    report['elapsed_seconds'] = 0
    cell_hashes = {}
    for index, cell in enumerate(report['cells']):
        path = output / 'cells' / (filename(cell) + '.json')
        if path.exists():
            raw = read_bytes(path)
            stored = _json(raw)
            same(stored['sha256'], checksum({k: v for k, v in stored.items() if k != 'sha256'}), 'Saved cell checksum')
            same(stored['source_snapshot_sha256'], manifest['sha256'], 'Saved cell snapshot')
            same(runner._identity(stored['result']), runner._identity(cell), 'Saved cell identity')
            report['cells'][index] = stored['result']
            cell_hashes[path.name] = sha(raw)
    started, completed, call_count = time.perf_counter(), 0, 0
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    def budget(count=False):
        nonlocal call_count
        if stopped or time.perf_counter() - started >= wall_seconds or (count and max_calls is not None and call_count >= max_calls):
            raise BudgetStop()
        if count:
            call_count += 1
    def persist():
        refresh_revalidation_report(report)
        report['elapsed_seconds'] = time.perf_counter() - started
        report['cell_artifact_sha256'] = cell_hashes
        report['source_snapshot_finished_cells'] = manifest['snapshot_finished_cells']
        atomic_json(output / 'report.json', report)
    try:
        persist()
        for index, cell in enumerate(original_cells):
            if cell['status'] not in FINISHED or report['cells'][index]['status'] != 'not_run':
                continue
            if max_cells is not None and completed >= max_cells:
                break
            # Recheck source snapshot, package and code before each cell.
            load_snapshot(output, data_dir, reviewed_observer_sha256)
            name = filename(cell)
            saved = evidence(read_bytes(output / 'source/private' / name))
            try:
                cell_start = time.perf_counter()
                corrected = replay_cell(cell, saved, rows[(cell['runtime_id'], cell['case_id'])],
                                        freeze, data_dir, budget, adverse_outcome_policy)
                corrected['result']['elapsed_seconds'] = time.perf_counter() - cell_start
                load_snapshot(output, data_dir, reviewed_observer_sha256)
            except BudgetStop:
                report['last_stop'] = 'budget_or_signal; unfinished cell will restart from reset'
                break
            except Mismatch as error:
                report['cells'][index] = dict(cell, status='failed', score=None,
                    revalidation_error=str(error), recorded_call_revalidation={'passed': False})
                persist()
                raise
            corrected['source_snapshot_sha256'] = manifest['sha256']
            corrected['source_cell_sha256'] = manifest['source_hashes']['private/' + name]
            corrected['sha256'] = checksum(corrected)
            path = output / 'cells' / (name + '.json')
            raw = json.dumps(corrected, sort_keys=True, allow_nan=False).encode() + b'\n'
            exclusive(path, raw)
            cell_hashes[path.name] = sha(raw)
            report['cells'][index] = corrected['result']
            completed += 1
            persist()
        persist()
        return report
    finally:
        for s, handler in handlers.items():
            signal.signal(s, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path, required=True)
    parser.add_argument('--source-freeze', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reviewed-observer-sha256', required=True)
    parser.add_argument('--adverse-outcome-policy', choices=('withhold', 'score'), default='withhold')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--wall-seconds', type=float, default=1800)
    parser.add_argument('--max-cells', type=int)
    parser.add_argument('--max-calls', type=int)
    options = vars(parser.parse_args(argv))
    report = run(**options)
    print(json.dumps({'status': report['status'], 'summary': report['summary']}))
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
