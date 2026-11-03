"""Focused checks for the supplementary session matrix; no full matrix is run."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from eval import validate_session_recovery_matrix as runner
from wildfire_control_v3.control import create_population_control
from wildfire_core.incident_day import fixture_inputs


CASE = 'incident-day-egress-v1-000'


def fixture_control():
    inputs, network = fixture_inputs()
    control = create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                        expected_network_sha256=network['sha256'])
    control.reset(CASE, seed=3866400)
    return control


def test_real_session_matches_independent_policy_and_observer(tmp_path):
    c = fixture_control()
    pins = dict(inputs_sha256=c.environment.inputs['sha256'], network_sha256=c.environment.network.sha256,
                scenario_sha256=c.environment._scenario['sha256'])
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    result = runner.exercise_cell(fixture_control, {'policy': 'conservative', 'case_id': CASE}, pins, directory,
                                  lambda: None, step_limit=8)
    assert result['status'] == 'partial'
    assert result['steps'] == 8 and result['minute'] > 3
    assert result['restart_checked'] is False
    journal = json.loads((directory / 'session-recovery-v1.json').read_text())['record']
    assert journal['state']['observer']['coverage']['ticks_observed'] == result['minute']
    assert journal['state']['policy']['status_sent'] is True


def test_observer_disagreement_fails_closed(tmp_path, monkeypatch):
    c = fixture_control()
    pins = dict(inputs_sha256=c.environment.inputs['sha256'], network_sha256=c.environment.network.sha256,
                scenario_sha256=c.environment._scenario['sha256'])
    directory = tmp_path / 'private'; directory.mkdir(mode=0o700)
    original = runner.SessionRecoveryV1.step
    def corrupted(session, sequence):
        result = original(session, sequence)
        session.observer.coverage['ticks_observed'] = 999
        return result
    monkeypatch.setattr(runner.SessionRecoveryV1, 'step', corrupted)
    with pytest.raises(ValueError, match='continuation differs'):
        runner.exercise_cell(fixture_control, {'policy': 'conservative', 'case_id': CASE}, pins, directory,
                             lambda: None, step_limit=2)


def test_public_report_is_aggregate_allowlisted(tmp_path):
    secret = 'private-canary-42'
    cell = dict(runtime_id='r', case_id='c', policy='p', status='passed', minute=1440,
                observer_ticks=1440, checkpoint=secret, action=secret, observer=secret)
    report = dict(version=runner.VERSION, status='incomplete', workers=1, expected_cells=[cell],
                  wait_recovery_sha256=runner.WAIT_DRIVER_SHA,
                  cells=[cell], pins={'dataset_revision': 'rev', 'secret': secret}, secret=secret)
    runner.publish(tmp_path, report)
    text = (tmp_path / 'report.json').read_text()
    assert secret not in text
    value = json.loads(text)
    assert value['cells'] == [dict(runtime_id='r', case_id='c', policy='p', status='passed',
                                   minute=1440, observer_ticks=1440)]
    assert value['expected_cells'] == [dict(runtime_id='r', case_id='c', policy='p')]
    assert value['pins'] == {'dataset_revision': 'rev'}
    assert value['wait_recovery_sha256'] == runner.WAIT_DRIVER_SHA


def test_completed_cell_requires_all_evidence():
    cell = dict(status='passed', minute=1440, steps=40, restart_checked=True,
                observer_ticks=1440, verified_outcome=True)
    assert runner.passed_cell(cell)
    for bad in ({'minute': 1439}, {'steps': 0}, {'steps': True}, {'restart_checked': False},
                {'observer_ticks': 1439}, {'verified_outcome': False}, {'unexpected': 'secret'}):
        assert not runner.passed_cell({**cell, **bad})
    assert not runner.passed_cell({k: v for k, v in cell.items() if k != 'observer_ticks'})


def test_reference_integrity_and_matrix_membership_fail_closed(tmp_path):
    original = runner.ROOT / 'results/population-v3-runtime-matrix-20261030.json'
    reference = json.loads(original.read_text())
    expected = reference['expected_cells']
    path = tmp_path / 'reference.json'
    path.write_text(json.dumps(reference))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    value, indexed = runner.read_reference(path, sha, expected)
    assert value['status'] == 'incomplete' and len(indexed) == 48
    assert sum(c['status'] == 'complete' for c in indexed.values()) == 42
    assert sum(c['status'] == 'blocked' for c in indexed.values()) == 6
    with pytest.raises(ValueError): runner.read_reference(path, '0' * 64, expected)
    reference['cells'][0] = copy.deepcopy(reference['cells'][1])
    path.write_text(json.dumps(reference))
    with pytest.raises(ValueError):
        runner.read_reference(path, hashlib.sha256(path.read_bytes()).hexdigest(), expected)


def test_reference_status_must_match_recorded_evidence(tmp_path):
    original = runner.ROOT / 'results/population-v3-runtime-matrix-20261030.json'
    reference = json.loads(original.read_text())
    expected = reference['expected_cells']
    path = tmp_path / 'reference.json'
    def read(value):
        path.write_text(json.dumps(value))
        return runner.read_reference(path, hashlib.sha256(path.read_bytes()).hexdigest(), expected)
    assert read(reference)[0]['status'] == 'incomplete'
    for status in ('complete', 'blocked', 'failed'):
        contradictory = copy.deepcopy(reference)
        contradictory['status'] = status
        with pytest.raises(ValueError, match='status contradicts'):
            read(contradictory)
    contradictory = copy.deepcopy(reference)
    contradictory['status'] = 'complete'
    for cell in contradictory['cells']:
        cell['status'] = 'failed'
    with pytest.raises(ValueError, match='status contradicts'):
        read(contradictory)
    # An explicit interrupted run may conservatively remain incomplete.
    conservative = copy.deepcopy(contradictory)
    conservative.update(status='incomplete', reason='wall_budget')
    assert read(conservative)[0]['status'] == 'incomplete'


@pytest.mark.parametrize('position', ['root', 'nested'])
def test_reference_rejects_duplicate_keys_even_with_correct_hash(tmp_path, position):
    reference = json.loads((runner.ROOT / 'results/population-v3-runtime-matrix-20261030.json').read_text())
    encoded = json.dumps(reference)
    if position == 'root':
        encoded = '{"version": "duplicate", ' + encoded[1:]
    else:
        needle = '"runtime_id": '
        assert needle in encoded
        encoded = encoded.replace(needle, '"runtime_id": "duplicate", ' + needle, 1)
    path = tmp_path / 'reference.json'
    path.write_text(encoded)
    with pytest.raises(ValueError, match='Duplicate reference key'):
        runner.read_reference(path, hashlib.sha256(path.read_bytes()).hexdigest(), reference['expected_cells'])


@pytest.mark.parametrize('fault', ['file_changed', 'loader_changed', 'module_pin', 'session_pin'])
def test_wait_recovery_dependency_pin_fails_closed(monkeypatch, fault):
    runner.check_recovery_sources()
    if fault == 'file_changed':
        original = runner.matrix._hash
        monkeypatch.setattr(runner.matrix, '_hash', lambda path: '0' * 64
                            if Path(path) == runner.WAIT_DRIVER_PATH else original(path))
    elif fault == 'loader_changed':
        import wildfire_control_v3
        original = wildfire_control_v3._capture_source
        monkeypatch.setattr(wildfire_control_v3, '_capture_source',
                            lambda name, path, loader: ('0' * 64, False)
                            if name == runner.wait_driver.__name__ else original(name, path, loader))
    elif fault == 'module_pin':
        monkeypatch.setattr(runner.wait_driver, '_SOURCE_SHA256', '0' * 64)
    else:
        monkeypatch.setitem(runner.session_driver._SOURCE, 'wait_recovery_v1.py', '0' * 64)
    with pytest.raises(ValueError):
        runner.check_recovery_sources()


def test_wait_dependency_mismatch_marks_matrix_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.wait_driver, '_SOURCE_SHA256', '0' * 64)
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='dependency identity differs'):
        runner.run(tmp_path / 'data', tmp_path / 'reference' / 'report.json', 'a' * 64,
                   output, wall_seconds=30)
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'incomplete' and report['reason'] == 'validation_error'
    assert report['wait_recovery_sha256'] == runner.WAIT_DRIVER_SHA
    assert report['cells'] == []


def test_budget_stop_publishes_incomplete_and_exits_nonzero(tmp_path):
    # No data pack or reference is required because this budget expires before reads.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPYCACHEPREFIX=str(tmp_path / 'cache'))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-m', 'eval.validate_session_recovery_matrix',
        '--data-dir', str(tmp_path / 'data'), '--reference-report', str(tmp_path / 'reference' / 'report.json'),
        '--reference-sha256', 'a' * 64, '--output', str(tmp_path / 'output'), '--wall-seconds', '0.000001'],
        cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    report = json.loads((tmp_path / 'output' / 'report.json').read_text())
    assert report['status'] == 'incomplete' and report['reason'] == 'wall_budget'
    assert report['cells'] == []
    assert report['wait_recovery_sha256'] == runner.WAIT_DRIVER_SHA
    assert 'checkpoint' not in json.dumps(report)
