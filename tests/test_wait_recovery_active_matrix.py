import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from eval import validate_wait_recovery_matrix as runner

REFERENCE = runner.ROOT / 'results/population-v3-runtime-matrix-20261030.json'
DATA = Path(os.environ.get('WILDFIRE_ISSUE60_DATA', '/tmp/wildfire-qwen3-data'))


def reference():
    return json.loads(REFERENCE.read_text())


def passing(cell):
    return {**cell, 'status': 'passed', 'wait_kind': 'public_multi_minute_wait',
            'refill_coverage': 'not_assessed', 'faults': [dict(fault=name, passed=True,
            continuation_transitions=2) for name in runner.FAULTS]}


def test_reference_domain_and_case_pins_fail_closed(tmp_path):
    ref = reference()
    expected = ref['expected_cells']
    assert len(runner.reference_cells(ref, expected)) == 48
    for mutate in (lambda r: r['cells'].pop(), lambda r: r['cells'][0].pop('pins'),
                   lambda r: r['cells'][0]['pins'].pop('scenario_sha256'),
                   lambda r: r['cells'][0].update(status='incomplete'),
                   lambda r: r['cells'][0].update(case_id=r['cells'][1]['case_id'],
                                                  policy=r['cells'][1]['policy'])):
        value = copy.deepcopy(ref); mutate(value)
        with pytest.raises(ValueError): runner.reference_cells(value, expected)
    raw = REFERENCE.read_bytes(); path = tmp_path / 'ref'; path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    assert runner.read_reference(path, sha)['version'] == runner.matrix.VERSION
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError): runner.read_reference(path, sha)


def test_result_gate_requires_real_distinct_faults_and_no_refill_claim():
    cell = reference()['expected_cells'][0]
    result = passing(cell)
    assert runner.passed_cell(result, cell)
    for mutation in (lambda r: r.update(refill_coverage='passed'),
                     lambda r: r.update(policy='wrong'),
                     lambda r: r['faults'].pop(),
                     lambda r: r['faults'][0].update(passed=1),
                     lambda r: r['faults'][0].update(fault='during_wait'),
                     lambda r: r['faults'][0].update(continuation_transitions=-1)):
        value = copy.deepcopy(result); mutation(value)
        assert not runner.passed_cell(value, cell)


@pytest.mark.parametrize('fault', runner.FAULTS)
def test_crash_requires_expected_phase_and_real_tick(tmp_path, fault):
    setup = {'identity': 'i', 'max_minutes': 10, 'start_minute': 5}
    result = {'observation': {'minute': 7}}
    checkpoint = {'engine_checkpoint': {'core': {'state': {
        'minute': 7, '_actions': [{'action': {'advance_minutes': 10}}]}}}}
    setup['expected_result_sha256'] = runner.matrix.checksum(result)
    setup['expected_checkpoint_sha256'] = runner.matrix.checksum(checkpoint)
    phase = 'complete' if fault == 'after_commit' else 'pending'
    record = {'phase': phase, 'sequence': 1, 'run_identity': 'i', 'arguments': {'max_minutes': 10}}
    (tmp_path / 'wait-recovery-v1.json').write_text(json.dumps({'record': record}))
    marker = tmp_path / 'tick.marker'
    if fault == 'during_wait':
        with pytest.raises(ValueError): runner.verify_crash(tmp_path, fault, setup)
        marker.write_text('9')
        with pytest.raises(ValueError): runner.verify_crash(tmp_path, fault, setup)
        marker.write_text('6')
    if fault == 'after_execute':
        with pytest.raises(ValueError): runner.verify_crash(tmp_path, fault, setup)
        (tmp_path / 'completed-wait.json').write_text(json.dumps({'result': result, 'checkpoint': checkpoint}))
    runner.verify_crash(tmp_path, fault, setup)
    (tmp_path / 'wait-recovery-v1.json').write_text(json.dumps({'record': {**record, 'sequence': True}}))
    with pytest.raises(ValueError): runner.verify_crash(tmp_path, fault, setup)


def test_completed_wait_evidence_requires_exact_result_and_checkpoint(tmp_path):
    result = {'observation': {'minute': 7}}
    checkpoint = {'engine_checkpoint': {'core': {'state': {
        'minute': 7, '_actions': [{'action': {'advance_minutes': 10}}]}}}}
    setup = {'identity': 'i', 'max_minutes': 10, 'start_minute': 5,
             'expected_result_sha256': runner.matrix.checksum(result),
             'expected_checkpoint_sha256': runner.matrix.checksum(checkpoint)}
    record = {'phase': 'pending', 'sequence': 1, 'run_identity': 'i', 'arguments': {'max_minutes': 10}}
    (tmp_path / 'wait-recovery-v1.json').write_text(json.dumps({'record': record}))
    runner.save_completed_wait(tmp_path, result, checkpoint, setup)
    assert (tmp_path / 'completed-wait.json').stat().st_mode & 0o777 == 0o600
    runner.verify_crash(tmp_path, 'after_execute', setup)
    for field in ('result_minute', 'checkpoint_minute', 'result_content', 'checkpoint_content'):
        evidence = {'result': copy.deepcopy(result), 'checkpoint': copy.deepcopy(checkpoint)}
        if field == 'result_minute': evidence['result']['observation']['minute'] = 6
        elif field == 'checkpoint_minute': evidence['checkpoint']['engine_checkpoint']['core']['state']['minute'] = 6
        elif field == 'result_content': evidence['result']['unexpected'] = 'changed'
        else: evidence['checkpoint']['engine_checkpoint']['core']['state']['unexpected'] = 'changed'
        (tmp_path / 'completed-wait.json').write_text(json.dumps(evidence))
        with pytest.raises(ValueError, match='Crash evidence is incomplete'):
            runner.verify_crash(tmp_path, 'after_execute', setup)


def real_worker_setup(tmp_path):
    if not (DATA / 'manifest.json').exists():
        pytest.skip('Pinned dataset not available')
    ref = reference()
    cell = next(c for c in ref['expected_cells'] if c['runtime_id'] == 'fixture' and c['policy'] == 'conservative')
    selected = runner.select_wait(DATA, cell, runner.reference_cells(ref, ref['expected_cells'])[runner.matrix.cell_identity(cell)])
    control = runner.control_for(DATA, cell['runtime_id'])
    control.restore_private_checkpoint(selected['checkpoint'])
    result = control.execute('advance', {'max_minutes': selected['action']['advance_minutes']})
    checkpoint = control.private_checkpoint()
    setup = {'checkpoint': selected['checkpoint'], 'runtime_id': cell['runtime_id'], 'identity': 'test-episode',
             'max_minutes': selected['action']['advance_minutes'], 'start_minute': selected['start_minute'],
             'expected_result_sha256': runner.matrix.checksum(result),
             'expected_checkpoint_sha256': runner.matrix.checksum(checkpoint)}
    (tmp_path / 'setup.json').write_text(json.dumps(setup))
    (tmp_path / 'setup.json').chmod(0o600)
    return setup


@pytest.mark.parametrize('fault_result', ('indeterminate', 'no_advance'))
def test_after_execute_rejects_indeterminate_or_no_advance(tmp_path, fault_result):
    setup = real_worker_setup(tmp_path)
    code = '''
import os, pathlib, sys
from eval import validate_wait_recovery_matrix as r
original = r.control_for
def poisoned(data, runtime):
    control = original(data, runtime)
    def invalid(name, arguments):
        if sys.argv[3] == 'indeterminate':
            return {'execution': 'indeterminate'}
        return {'observation': control.environment.get_situation()}
    control.execute = invalid
    return control
r.control_for = poisoned
try:
    r.crash_worker(sys.argv[1], pathlib.Path(sys.argv[2]), 'after_execute')
except Exception:
    os._exit(19)
os._exit(20)
'''
    process = subprocess.run([sys.executable, '-c', code, str(DATA), str(tmp_path), fault_result],
                             cwd=runner.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    assert process.returncode == 19
    assert not (tmp_path / 'completed-wait.json').exists()
    with pytest.raises(ValueError, match='Crash evidence is incomplete'):
        runner.verify_crash(tmp_path, 'after_execute', setup)


def test_after_execute_worker_persists_verified_evidence(tmp_path):
    setup = real_worker_setup(tmp_path)
    process = subprocess.run([sys.executable, '-m', 'eval.validate_wait_recovery_matrix', '--worker',
        'after_execute', '--data-dir', str(DATA), '--private-dir', str(tmp_path)], cwd=runner.ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    assert process.returncode == runner.CRASH_EXIT
    runner.verify_crash(tmp_path, 'after_execute', setup)


def test_report_allowlists_nested_private_fields(tmp_path):
    cell = passing(reference()['expected_cells'][0]); cell['secret'] = 'private secret'
    cell['faults'][0]['checkpoint'] = 'private secret'
    report = {'status': 'incomplete', 'expected_cells': [dict(reference()['expected_cells'][0], secret='private secret')],
              'cells': [cell], 'pins': {'secret': 'private secret'}}
    runner.publish(tmp_path, report)
    assert 'private secret' not in (tmp_path / 'report.json').read_text()


def test_private_workspace_survives_parent_death_outside_published_tree(tmp_path):
    output = tmp_path / 'published'; output.mkdir()
    data = tmp_path / 'data'; data.mkdir()
    ref = tmp_path / 'ref' / 'matrix.json'; ref.parent.mkdir()
    location = tmp_path / 'private-location'
    code = '''
import os, pathlib, sys
from eval.validate_wait_recovery_matrix import private_workspace
with private_workspace(*sys.argv[1:4]) as p:
    (p / 'checkpoint').write_text('private')
    pathlib.Path(sys.argv[4]).write_text(str(p))
    os._exit(91)
'''
    result = subprocess.run([sys.executable, '-c', code, str(output), str(data), str(ref), str(location)],
                            cwd=runner.ROOT, check=False)
    assert result.returncode == 91
    private = Path(location.read_text())
    try:
        assert (private / 'checkpoint').read_text() == 'private'
        assert (private.stat().st_mode & 0o777) == 0o700
        assert all(not private.is_relative_to(path) for path in (output, data, ref.parent))
        assert not list(output.iterdir())
    finally:
        shutil.rmtree(private)


@pytest.mark.parametrize('runtime,policy', [('fixture', 'conservative'), ('es-ma-2021', 'passive')])
def test_real_multiminute_wait_and_crash_recovery(runtime, policy, tmp_path):
    if not (DATA / 'manifest.json').exists():
        pytest.skip('Pinned dataset not available')
    ref = reference()
    cell = next(c for c in ref['expected_cells'] if c['runtime_id'] == runtime and c['policy'] == policy)
    selected = runner.select_wait(DATA, cell, runner.reference_cells(ref, ref['expected_cells'])[runner.matrix.cell_identity(cell)])
    assert selected['public_result']['observation']['minute'] - selected['start_minute'] >= 2
    result = runner.exercise_wait(DATA, selected, tmp_path)
    assert runner.passed_cell(result, cell)
    assert result['refill_coverage'] == 'not_assessed'
    assert not list(tmp_path.iterdir())


def test_matrix_completeness_and_failure_without_private_publication(tmp_path, monkeypatch):
    ref = reference(); raw = json.dumps(ref).encode()
    path = tmp_path / 'reference' / 'report.json'; path.parent.mkdir(); path.write_bytes(raw)
    data = tmp_path / 'data'; data.mkdir()
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(runner.matrix, 'pins', lambda _: ({}, ref['pins']))
    monkeypatch.setattr(runner.matrix, 'expected_cells', lambda _: ref['expected_cells'])
    monkeypatch.setattr(runner, 'select_wait', lambda data, cell, reference, check: cell)
    monkeypatch.setattr(runner, 'exercise_wait', lambda data, cell, private, check: passing(cell))
    report = runner.run(data, path, sha, tmp_path / 'good')
    assert report['status'] == 'complete' and len(report['cells']) == 48
    assert path.read_bytes() == raw
    monkeypatch.setattr(runner, 'exercise_wait', lambda data, cell, private, check: {**passing(cell), 'faults': []})
    with pytest.raises(ValueError, match='evidence is incomplete'):
        runner.run(data, path, sha, tmp_path / 'bad')
    published = json.loads((tmp_path / 'bad' / 'report.json').read_text())
    assert published['status'] == 'incomplete'
    assert all(c['status'] == 'not_run' for c in published['cells'])
