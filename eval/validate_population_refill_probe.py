"""Supplementary decision-boundary refill checks for a finished v3 matrix.

Use a fresh process, immutable sources from before import, and an empty bytecode
cache. After the matrix process exits, pass the SHA-256 of its final report.
The report has no process-exit marker: terminal cell statuses alone cannot prove
that its writer has exited. This probe never changes or relabels that report.
The baseline is a freshly reconstructed, unsplit execution of the reported
case and policy under the same pins. The original report lacks a pre-wait
checkpoint/action digest, so this does not establish identity to the original
trajectory or equality of its score. The probe splits a wait in that fresh
baseline into one-minute waits; it does not checkpoint inside the original
uninterrupted action. Pre-wait checkpoint equality refers to the reconstruction.
There is no resume; repeat in a new output directory after an interruption.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time

# The matrix module must import its own dependencies first, in a fresh process.
from eval import validate_population_v3_runtime as matrix

VERSION = 'wildfire-population-v3-refill-probe-v2'
BASELINE_KIND = 'freshly_reconstructed_unsplit'
ROOT = Path(__file__).resolve().parents[1]
PROBE_SOURCE = Path(__file__).resolve()
SOURCE_AT_IMPORT = hashlib.sha256(PROBE_SOURCE.read_bytes()).hexdigest()
VERIFY_KEYS = ('passed', 'exact_replay', 'independent_reward_agreement')


def source_pin():
    """Compare current probe source and loader code, not prior execution."""
    from wildfire_control_v3 import _capture_source
    module = sys.modules[__name__]
    name = module.__spec__.name if module.__spec__ else module.__name__
    digest, matches = _capture_source(name, module.__file__, module.__loader__)
    if not matches or digest != SOURCE_AT_IMPORT:
        raise ValueError('Probe source or loader changed')
    return digest


def read_original(path, expected_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('A SHA-256 of the finished original report is required')
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Original report must be a regular file')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Original report pin differs')
    return json.loads(raw)


def select_targets(report, expected, cases=None):
    if report.get('version') != matrix.VERSION or report.get('expected_cells') != expected:
        raise ValueError('Original matrix version or active domain differs')
    cells = report.get('cells', [])
    if len(expected) != 48 or len(cells) != 48:
        raise ValueError('Original matrix is not finished')
    identities = [matrix.cell_identity(c) for c in cells]
    if len(set(identities)) != 48 or set(identities) != {matrix.cell_identity(c) for c in expected}:
        raise ValueError('Original matrix cell identities differ')
    if any(c.get('status') not in ('complete', 'blocked', 'failed') for c in cells):
        raise ValueError('Original matrix is not finished')
    eligible = []
    for c in cells:
        boundary = c.get('checkpoint_boundaries', {}).get('refilling', {})
        if (c.get('runtime_id') == 'fixture' and c.get('policy') in matrix.POLICY_NAMES
                and c.get('status') == 'blocked' and boundary.get('encountered') is True
                and boundary.get('status') == 'unsupported'):
            if (c.get('minute') != 1440 or not all(c.get('verification', {}).get(k) is True for k in VERIFY_KEYS)
                    or any(f.get('category') in ('blocking', 'contradiction', 'error') for f in c.get('findings', []))):
                raise ValueError('Original cell does not have the required verified result')
            eligible.append(c)
    eligible.sort(key=lambda c: (c['case_id'], c['policy']))
    if not eligible:
        raise ValueError('No eligible blocked fixture refill cells')
    wanted = set(cases) if cases is not None else {c['case_id'] for c in eligible}
    if not wanted or (cases is not None and len(wanted) != len(cases)):
        raise ValueError('Case selection must be nonempty and unique')
    if not wanted <= {c['case_id'] for c in eligible}:
        raise ValueError('Selected case is not an eligible original cell')
    return [c for c in eligible if c['case_id'] in wanted], len(eligible)


def end_comparison(baseline, segmented):
    """Compare the reconstructed baseline with the segmented branch."""
    a, b = baseline.checkpoint(), segmented.checkpoint()
    histories_equal = a['core']['state']['_actions'] == b['core']['state']['_actions']
    decisions_equal = baseline.decision == segmented.decision
    for value in (a, b):
        value.pop('sha256')
        value['core'].pop('sha256')
        value['core']['state'].pop('decision')
        value['core']['state'].pop('_actions')
    observation_a, observation_b = baseline.get_situation(), segmented.get_situation()
    observation_a.pop('decision'); observation_b.pop('decision')
    return dict(
        versioned_state_except_decision_and_actions_equal=matrix.checksum(a) == matrix.checksum(b),
        audit_equal=matrix.checksum(baseline._audit) == matrix.checksum(segmented._audit),
        operations_equal=matrix.checksum(baseline.ops.checkpoint()) == matrix.checksum(segmented.ops.checkpoint()),
        fire_equal=matrix.checksum(baseline.fire.checkpoint()) == matrix.checksum(segmented.fire.checkpoint()),
        communications_equal=matrix.checksum(baseline.bus.checkpoint()) == matrix.checksum(segmented.bus.checkpoint()),
        population_evidence_equal=matrix.checksum(baseline._population_evidence) == matrix.checksum(segmented._population_evidence),
        public_situation_except_decision_equal=matrix.checksum(observation_a) == matrix.checksum(observation_b),
        reconstructed_baseline_decisions=baseline.decision, segmented_decisions=segmented.decision,
        decision_counts_equal=decisions_equal, action_histories_equal=histories_equal)


def _refilling(env):
    return any(unit['state'] == 'refilling' for unit in env.ops.resources.values())


def _restore_before_wait(data_dir, snapshot):
    other, _ = matrix.make_v3(data_dir, 'fixture')
    other.restore(copy.deepcopy(snapshot))
    return other


def probe_cell(data_dir, original_cell, check):
    env, _ = matrix.make_v3(data_dir, 'fixture')
    observation, _ = env.reset(original_cell['case_id'])
    expected_pins = original_cell.get('pins', {})
    if (env.inputs['sha256'] != expected_pins.get('inputs_sha256') or
        env.network.sha256 != expected_pins.get('network_sha256') or
        env._scenario['sha256'] != expected_pins.get('scenario_sha256')):
        raise ValueError('Original case pins differ')
    policy = matrix.make_policy(original_cell['policy'])
    calls = []
    public = matrix.PublicFacade(env, calls)
    public.get_situation(); public.get_reports(limit=20)
    while not (env.terminated or env.truncated):
        check()
        start_minute, audit_start = env.minute, len(env._audit)
        if _refilling(env):
            raise ValueError('Reconstructed phase was at a decision boundary')
        action = policy.action(public, observation)
        # Policy queries and its own state are retained at the point immediately
        # before the reconstructed action; this also supports non-passive policies.
        snapshot = env.checkpoint()
        before_policy = policy.checkpoint()
        before_calls = copy.deepcopy(calls)
        result = matrix.commit(public, action)
        if 'observation' not in result:
            raise ValueError('Reconstructed wait rejected')
        observation = result['observation']
        seen = [row['minute'] for row in env._audit[audit_start:] if
                any(unit['state'] == 'refilling' for unit in row['resources'].values())]
        if seen:
            if set(action) != {'advance_minutes'}:
                raise ValueError('Reconstructed refill did not occur during a wait')
            if _refilling(env):
                raise ValueError('Reconstructed refill reached a decision boundary')
            break
    else:
        raise ValueError('Reconstructed refill phase was not observed')
    check()
    end_minute = env.minute
    segmented = _restore_before_wait(data_dir, snapshot)
    start_equal = segmented.checkpoint() == snapshot
    if not start_equal:
        raise ValueError('Pre-wait full checkpoint differs')
    probe_calls = before_calls
    probe_public = matrix.PublicFacade(segmented, probe_calls)
    while segmented.minute < end_minute and not _refilling(segmented):
        check()
        result = matrix.commit(probe_public, {'advance_minutes': 1})
        if 'observation' not in result:
            raise ValueError('Segmented wait rejected')
    if not _refilling(segmented) or segmented.minute not in seen:
        raise ValueError('Segmented trajectory did not reach reconstructed audited refill')
    phase_minute = segmented.minute
    restored, _ = matrix.clone(segmented)
    phase_equal = restored.checkpoint() == segmented.checkpoint()
    if not phase_equal:
        raise ValueError('Refill checkpoint differs after restore')
    restored_public = matrix.PublicFacade(restored, [])
    comparisons, end = [], None
    def capture_end():
        nonlocal end
        if segmented.minute == end_minute and end is None:
            end = end_comparison(env, segmented)
    capture_end()
    for _ in range(2):
        check()
        if segmented.terminated or segmented.truncated or restored.terminated or restored.truncated:
            raise ValueError('Two refill continuations unavailable')
        live = matrix.commit(probe_public, {'advance_minutes': 1})
        replay = matrix.commit(restored_public, {'advance_minutes': 1})
        matched = dict(public_equal=live == replay, full_checkpoint_equal=segmented.checkpoint() == restored.checkpoint())
        comparisons.append(matched)
        if not all(matched.values()):
            raise ValueError('Refill continuation differs')
        capture_end()
    while segmented.minute < end_minute:
        check()
        result = matrix.commit(probe_public, {'advance_minutes': 1})
        if 'observation' not in result:
            raise ValueError('Segmented wait rejected')
        capture_end()
    if end is None:
        raise ValueError('Reconstructed wait-end comparison unavailable')
    required = ('versioned_state_except_decision_and_actions_equal', 'audit_equal', 'operations_equal',
                'fire_equal', 'communications_equal', 'population_evidence_equal',
                'public_situation_except_decision_equal')
    if (not all(end[k] is True for k in required) or end['decision_counts_equal'] or end['action_histories_equal']):
        raise ValueError('Wait-end equivalence or history distinction failed')
    # Continue from the reconstructed policy state after its wait was selected.
    continued_policy = matrix.make_policy(original_cell['policy'])
    continued_policy.restore(copy.deepcopy(before_policy))
    observation = segmented.get_situation()
    while not (segmented.terminated or segmented.truncated):
        check()
        result = matrix.commit(probe_public, continued_policy.action(probe_public, observation))
        if 'observation' not in result:
            raise ValueError('Targeted continuation rejected')
        observation = result['observation']
    check()
    verification = segmented.verify_episode()
    verified = {k: verification.get(k) is True for k in VERIFY_KEYS}
    if segmented.minute != 1440 or not segmented.terminated or segmented.truncated or not all(verified.values()):
        raise ValueError('Targeted episode did not verify')
    check()
    return dict(runtime_id='fixture', case_id=original_cell['case_id'], policy=original_cell['policy'],
        original_cell_status='blocked', original_refill_status='unsupported',
        baseline_kind=BASELINE_KIND, status='passed', case_pins={k: expected_pins[k] for k in ('inputs_sha256', 'network_sha256', 'scenario_sha256')}, reconstructed_wait_start_minute=start_minute,
        reconstructed_wait_end_minute=end_minute, refill_minute=phase_minute,
        reconstructed_pre_wait_full_checkpoint_equal=start_equal, refill_restore_full_checkpoint_equal=phase_equal,
        continuation=comparisons, reconstructed_wait_end_comparison=end, targeted_final_minute=segmented.minute,
        targeted_verification=verified)


def passed_probe(result, original_cell):
    required = ('versioned_state_except_decision_and_actions_equal', 'audit_equal', 'operations_equal',
                'fire_equal', 'communications_equal', 'population_evidence_equal',
                'public_situation_except_decision_equal')
    end = result.get('reconstructed_wait_end_comparison', {})
    continuation = result.get('continuation', [])
    start, phase, finish = (result.get(k) for k in ('reconstructed_wait_start_minute', 'refill_minute', 'reconstructed_wait_end_minute'))
    return (all(type(n) is int for n in (start, phase, finish)) and 0 <= start < phase < finish <= 1440
        and result.get('status') == 'passed' and result.get('runtime_id') == 'fixture'
        and result.get('case_id') == original_cell['case_id'] and result.get('policy') == original_cell['policy']
        and result.get('original_cell_status') == 'blocked' and result.get('original_refill_status') == 'unsupported'
        and result.get('baseline_kind') == BASELINE_KIND
        and result.get('case_pins') == {k: original_cell['pins'].get(k) for k in
            ('inputs_sha256', 'network_sha256', 'scenario_sha256')}
        and result.get('reconstructed_pre_wait_full_checkpoint_equal') is True
        and result.get('refill_restore_full_checkpoint_equal') is True
        and len(continuation) == 2 and all(item.get('public_equal') is True
            and item.get('full_checkpoint_equal') is True for item in continuation)
        and all(end.get(k) is True for k in required)
        and end.get('decision_counts_equal') is False and end.get('action_histories_equal') is False
        and type(end.get('reconstructed_baseline_decisions')) is int and type(end.get('segmented_decisions')) is int
        and end['segmented_decisions'] > end['reconstructed_baseline_decisions']
        and result.get('targeted_final_minute') == 1440
        and all(result.get('targeted_verification', {}).get(k) is True for k in VERIFY_KEYS))


def publish(output, report, before_replace=None):
    # Assemble only explicit aggregate fields; raw calls, audits and reports are excluded.
    fields = ('version', 'status', 'scope', 'baseline_kind', 'pins', 'eligible_cells', 'selected_cells',
              'cases', 'elapsed_seconds', 'workers', 'limitations', 'reason')
    public = {k: report[k] for k in fields if k in report}
    if 'pins' in public:
        pin_fields = ('dataset_manifest_sha256', 'dataset_revision', 'core_sha256', 'lifecycle_sha256',
            'interface_version', 'population_version', 'population_engine_sha256', 'v3_sources_sha256',
            'validator_sha256', 'source_attestation', 'original_report_sha256', 'probe_sha256')
        public['pins'] = {k: public['pins'][k] for k in pin_fields if k in public['pins']}
    case_fields = ('runtime_id', 'case_id', 'policy', 'original_cell_status', 'original_refill_status',
        'status', 'baseline_kind', 'case_pins', 'reconstructed_wait_start_minute', 'reconstructed_wait_end_minute', 'refill_minute',
        'reconstructed_pre_wait_full_checkpoint_equal', 'refill_restore_full_checkpoint_equal', 'continuation',
        'reconstructed_wait_end_comparison', 'targeted_final_minute', 'targeted_verification')
    public['cases'] = []
    wait_fields = ('versioned_state_except_decision_and_actions_equal', 'audit_equal', 'operations_equal',
        'fire_equal', 'communications_equal', 'population_evidence_equal',
        'public_situation_except_decision_equal', 'reconstructed_baseline_decisions', 'segmented_decisions',
        'decision_counts_equal', 'action_histories_equal')
    for cell in report['cases']:
        item = {k: cell[k] for k in case_fields if k in cell}
        if 'case_pins' in item:
            item['case_pins'] = {k: item['case_pins'][k] for k in
                ('inputs_sha256', 'network_sha256', 'scenario_sha256') if k in item['case_pins']}
        if 'reconstructed_wait_end_comparison' in item:
            value = item['reconstructed_wait_end_comparison']
            item['reconstructed_wait_end_comparison'] = {k: value[k] for k in wait_fields if k in value}
        if 'continuation' in item:
            item['continuation'] = [{k: row[k] for k in ('public_equal', 'full_checkpoint_equal') if k in row}
                                    for row in item['continuation']]
        if 'targeted_verification' in item:
            item['targeted_verification'] = {k: item['targeted_verification'][k] for k in VERIFY_KEYS
                                             if k in item['targeted_verification']}
        public['cases'].append(item)
    with tempfile.NamedTemporaryFile('w', dir=output, delete=False, encoding='utf-8') as stream:
        name = stream.name
        try:
            json.dump(public, stream, indent=2, allow_nan=False); stream.write('\n')
        except BaseException:
            os.unlink(name); raise
    try:
        if before_replace is not None: before_replace()
        os.replace(name, output / 'report.json')
    finally:
        if os.path.exists(name): os.unlink(name)


def run(data_dir, original_report, original_report_sha256, output, *, cases=None, wall_seconds=600):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    output = Path(output)
    if (output.resolve().is_relative_to(Path(original_report).resolve().parent) or
            output.resolve().is_relative_to(Path(data_dir).resolve())):
        raise ValueError('Output must be separate from original report and dataset')
    with matrix.hard_timeout(wall_seconds):
        return _run(data_dir, original_report, original_report_sha256, output, cases, wall_seconds)


def _run(data_dir, original_report, original_sha, output, cases, wall_seconds):
    started = time.monotonic()
    # Require a separately created destination; never write into the original matrix.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = dict(version=VERSION, status='incomplete', scope='fixture_refill', baseline_kind=BASELINE_KIND, cases=[],
        elapsed_seconds=0.0, workers=1, limitations=[
        'Baseline is a freshly reconstructed unsplit run of the case and policy under the same pins.',
        'The original report has no pre-wait checkpoint or action digest; equality is relative to the reconstruction and does not establish original trajectory identity or score equality.',
        'Supplementary segmented trajectory; no checkpoint inside the original uninterrupted action.',
        'Original matrix cells remain unchanged and blocked.',
        'Wait for the matrix process to exit before pinning its report; terminal cells do not prove process exit.',
        'No resume: restart in a new output directory after interruption.',
        'Immutable sources before import and throughout execution are required; loader checks cannot attest prior execution.',
        'SIGALRM may be delayed by native code that does not return control.'])
    publish(output, report)
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    def check():
        if stopped or time.monotonic() - started >= wall_seconds:
            raise matrix.Interrupted('signal' if stopped else 'wall_budget')
    def verify_pins():
        check()
        original = read_original(original_report, original_sha)
        manifest, pinned = matrix.pins(data_dir)
        if original.get('pins') != pinned or source_pin() != probe_pin:
            raise ValueError('Source, dataset or original report pins changed')
        return original, manifest, pinned
    def write(before_replace=None):
        report['elapsed_seconds'] = round(time.monotonic() - started, 4)
        publish(output, report, before_replace=before_replace)
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        check()
        probe_pin = source_pin()
        original, manifest, pinned = verify_pins()
        expected = matrix.expected_cells(manifest)
        targets, eligible = select_targets(original, expected, cases)
        report.update(pins=dict(pinned, original_report_sha256=original_sha, probe_sha256=probe_pin),
            eligible_cells=eligible, selected_cells=len(targets),
            cases=[dict(runtime_id='fixture', case_id=c['case_id'], policy=c['policy'],
                        original_cell_status='blocked', original_refill_status='unsupported', status='not_run') for c in targets])
        write()
        for i, cell in enumerate(targets):
            check(); verify_pins()
            report['cases'][i]['status'] = 'incomplete'; write()
            result = probe_cell(data_dir, cell, check)
            check()
            if not passed_probe(result, cell): raise ValueError('Probe result did not satisfy evidence gate')
            report['cases'][i] = result; write()
        verify_pins(); check()
        report['status'] = 'complete' if len(targets) == eligible else 'subset_complete'
        def final_gate():
            verify_pins(); check()
        write(before_replace=final_gate)
        check()
        return report
    except BaseException as exc:
        signal.setitimer(signal.ITIMER_REAL, 0)
        report['status'] = 'incomplete'
        report['reason'] = str(exc) if isinstance(exc, matrix.Interrupted) and str(exc) in ('signal', 'wall_budget') else 'validation_error'
        write()
        if isinstance(exc, matrix.Interrupted): return report
        raise
    finally:
        for sig, handler in handlers.items(): signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--original-report', required=True)
    parser.add_argument('--original-report-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--case-id', action='append', dest='cases')
    parser.add_argument('--wall-seconds', type=float, default=600)
    args = parser.parse_args()
    try:
        result = run(args.data_dir, args.original_report, args.original_report_sha256, args.output,
                     cases=args.cases, wall_seconds=args.wall_seconds)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'incomplete', 'reason': 'validation_error'}))
        return 2
    print(json.dumps({k: result[k] for k in ('status', 'eligible_cells', 'selected_cells', 'elapsed_seconds', 'workers') if k in result}))
    return 0 if result['status'] in ('complete', 'subset_complete') else 2


if __name__ == '__main__':
    raise SystemExit(main())
