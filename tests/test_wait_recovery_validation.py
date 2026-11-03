import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from eval import validate_wait_recovery as harness
from eval import validate_population_refill_probe as probe


def sample_wait():
    control = harness.control_for(None)
    env = control.environment
    env.reset('incident-day-egress-v1-000')
    before = control.private_checkpoint()
    policy = harness.matrix.make_policy('passive')
    action = {'advance_minutes': 5}
    result = harness.matrix.commit(harness.matrix.PublicFacade(env, []), action)
    return dict(checkpoint=before, action=action, policy_state=policy.checkpoint(),
                unsplit_result=result, unsplit_checkpoint=env.checkpoint(),
                case_id='incident-day-egress-v1-000', policy='passive', refill_minutes=[])


def test_real_driver_recovers_across_process_deaths(tmp_path):
    cell = next(c for c in blocked_cells() if c['case_id'] == 'incident-day-egress-v1-000'
                and c['policy'] == 'conservative')
    selected = harness.select_wait(None, cell)
    assert selected['refill_minutes']
    result = harness.exercise_wait(None, selected, tmp_path)
    assert result['status'] == 'passed'
    assert result['faults'] == [{'fault': fault, 'passed': True} for fault in harness.FAULTS]
    assert not list(tmp_path.iterdir())


def test_missing_crash_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(harness.subprocess, 'run', lambda *args, **kwargs: type('Done', (), {'returncode': 0})())
    with pytest.raises(ValueError, match='Crash point'):
        harness.exercise_wait(None, sample_wait(), tmp_path)


def test_bad_reconstructed_checkpoint_fails_closed(tmp_path):
    sample = sample_wait()
    sample['unsplit_checkpoint']['sha256'] = 'bad'
    with pytest.raises(ValueError, match='unsplit'):
        harness.exercise_wait(None, sample, tmp_path)


@pytest.mark.parametrize('fault', harness.FAULTS)
def test_crash_journal_must_match_phase_and_command(tmp_path, fault):
    phase = 'complete' if fault == 'after_commit' else 'pending'
    path = tmp_path / 'wait-recovery-v1.json'
    record = {'phase': phase, 'sequence': 1, 'run_identity': 'id', 'arguments': {'max_minutes': 5}}
    path.write_text(json.dumps({'record': record}))
    if fault == 'during_wait':
        (tmp_path / 'refill-tick.marker').write_text('3')
    harness.assert_crash_record(tmp_path, fault, 'id', 5, [3])
    for mutation in (
        {'phase': 'pending' if phase == 'complete' else 'complete'},
        {'sequence': 2}, {'run_identity': 'different'}, {'arguments': {'max_minutes': 6}},
    ):
        path.write_text(json.dumps({'record': {**record, **mutation}}))
        with pytest.raises(ValueError, match='Unexpected crash journal'):
            harness.assert_crash_record(tmp_path, fault, 'id', 5, [3])
    path.write_text('private detail, not JSON')
    with pytest.raises(ValueError, match='Unexpected crash journal') as error:
        harness.assert_crash_record(tmp_path, fault, 'id', 5, [3])
    assert 'private detail' not in str(error.value)


def test_refill_marker_is_required_and_must_match_audited_minute(tmp_path):
    path = tmp_path / 'wait-recovery-v1.json'
    path.write_text(json.dumps({'record': {'phase': 'pending', 'sequence': 1,
                    'run_identity': 'id', 'arguments': {'max_minutes': 5}}}))
    for marker in (None, '2', 'not-a-minute'):
        if marker is not None:
            (tmp_path / 'refill-tick.marker').write_text(marker)
        with pytest.raises(ValueError, match='Unexpected crash journal'):
            harness.assert_crash_record(tmp_path, 'during_wait', 'id', 5, [3])
    (tmp_path / 'refill-tick.marker').write_text('3')
    harness.assert_crash_record(tmp_path, 'during_wait', 'id', 5, [3])


def test_no_refill_does_not_count_as_during_wait_crash(tmp_path):
    with pytest.raises(ValueError, match='Crash point not reached'):
        harness.exercise_wait(None, sample_wait(), tmp_path)


@pytest.mark.parametrize('missing', ('pins', 'inputs_sha256', 'network_sha256', 'scenario_sha256'))
def test_select_wait_requires_all_case_pins(missing):
    import copy
    cell = copy.deepcopy(blocked_cells()[0])
    if missing == 'pins':
        del cell['pins']
    else:
        del cell['pins'][missing]
    with pytest.raises(ValueError, match='Case identity differs'):
        harness.select_wait(None, cell)


def test_evidence_gate_rejects_missing_duplicate_or_wrong_results():
    cell = {'runtime_id': 'fixture', 'case_id': 'case', 'policy': 'passive'}
    result = {**cell, 'original_cell_status': 'blocked', 'status': 'passed',
              'faults': [{'fault': fault, 'passed': True} for fault in harness.FAULTS]}
    assert harness.passed_case(result, cell)
    for bad in (
        {**result, 'status': 'failed'}, {**result, 'case_id': 'other'},
        {**result, 'faults': result['faults'][:-1]},
        {**result, 'faults': [result['faults'][0]] * len(harness.FAULTS)},
        {**result, 'faults': [{**row, 'passed': 1} for row in result['faults']]},
    ):
        assert not harness.passed_case(bad, cell)


def test_original_report_has_exact_six_pairs_and_is_unchanged():
    path = harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json'
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    report = probe.read_original(path, sha)
    targets, count = probe.select_targets(report, report['expected_cells'])
    assert count == 6
    assert {(c['case_id'], c['policy']) for c in targets} == {
        ('incident-day-coverage-v1-002', 'passive'),
        ('incident-day-coverage-v1-004', 'passive'),
        ('incident-day-egress-v1-000', 'passive'),
        ('incident-day-egress-v1-000', 'conservative'),
        ('incident-day-egress-v1-003', 'conservative'),
        ('incident-day-egress-v1-005', 'passive')}
    assert path.read_bytes() == raw


def test_publication_is_allowlisted_at_every_case_level(tmp_path):
    report = {'version': harness.VERSION, 'status': 'incomplete', 'secret': 'private',
              'pins': {'core_sha256': 'hash', 'private': 'private'},
              'cases': [{'case_id': 'case', 'policy': 'passive', 'checkpoint': 'private',
                         'faults': [{'fault': 'during_wait', 'passed': True, 'result': 'private'}]}]}
    harness.publish(tmp_path, report)
    text = (tmp_path / 'report.json').read_text()
    assert 'private' not in text
    assert json.loads(text)['cases'][0]['faults'] == [{'fault': 'during_wait', 'passed': True}]


def blocked_cells():
    report = json.loads((harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json').read_text())
    return probe.select_targets(report, report['expected_cells'])[0]


@pytest.mark.parametrize('cell', blocked_cells(), ids=lambda c: c['case_id'] + '-' + c['policy'])
def test_each_blocked_fixture_wait_crash_recovery(cell, tmp_path):
    # Pinned fixture identities are verified, but this does not replace a run
    # with the full release dataset or establish original trajectory identity.
    selected = harness.select_wait(None, cell)
    result = harness.exercise_wait(None, selected, tmp_path)
    assert result['status'] == 'passed'
    assert len(result['faults']) == 4


def test_output_cannot_overlap_input(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    original = tmp_path / 'original' / 'report.json'
    with pytest.raises(ValueError, match='separate'):
        harness.run(data, original, 'a' * 64, original.parent / 'output')
    with pytest.raises(ValueError, match='separate'):
        harness.run(data, original, 'a' * 64, data / 'output')


def test_killed_parent_leaves_private_workspace_outside_public_trees(tmp_path):
    output = tmp_path / 'published'; output.mkdir()
    data = tmp_path / 'dataset'; data.mkdir()
    original = tmp_path / 'original' / 'report.json'; original.parent.mkdir()
    location = tmp_path / 'location'
    code = '''
import os, pathlib, sys
from eval.validate_wait_recovery import private_workspace
with private_workspace(sys.argv[1], sys.argv[2], sys.argv[3]) as private:
    (private / 'private-checkpoint').write_text('secret')
    pathlib.Path(sys.argv[4]).write_text(str(private))
    os._exit(91)
'''
    process = subprocess.run([sys.executable, '-c', code, str(output), str(data), str(original),
                              str(location)], cwd=harness.ROOT, check=False)
    assert process.returncode == 91
    private = Path(location.read_text())
    try:
        assert (private / 'private-checkpoint').read_text() == 'secret'
        assert (os.stat(private).st_mode & 0o777) == 0o700
        assert all(not private.is_relative_to(path) for path in (output, data, original.parent))
        assert not list(output.iterdir())
    finally:
        shutil.rmtree(private)


def test_campaign_requires_all_six_and_preserves_original(tmp_path, monkeypatch):
    source = harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json'
    original = tmp_path / 'original' / 'report.json'
    original.parent.mkdir()
    raw = source.read_bytes(); original.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    report = json.loads(raw)
    data = tmp_path / 'dataset'; data.mkdir()
    monkeypatch.setattr(harness.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(harness.matrix, 'expected_cells', lambda _: report['expected_cells'])
    monkeypatch.setattr(harness, 'select_wait', lambda data, cell, check: cell)
    def exercise(data, cell, private, check):
        assert not Path(private).is_relative_to(tmp_path / 'output')
        assert not Path(private).is_relative_to(data)
        assert not Path(private).is_relative_to(original.parent)
        return {'runtime_id': cell['runtime_id'], 'case_id': cell['case_id'],
                'policy': cell['policy'], 'original_cell_status': 'blocked', 'status': 'passed',
                'faults': [{'fault': fault, 'passed': True} for fault in harness.FAULTS]}
    monkeypatch.setattr(harness, 'exercise_wait', exercise)
    result = harness.run(data, original, sha, tmp_path / 'output')
    assert result['status'] == 'complete' and len(result['cases']) == 6
    assert original.read_bytes() == raw
    published = json.loads((tmp_path / 'output' / 'report.json').read_text())
    assert published['original_report_sha256'] == sha
    assert 'original blocked cells and matrix status remain unchanged' in published['limitations'][1]
    assert published['probe_sha256'] == probe.source_pin()


def test_probe_source_change_prevents_success(tmp_path, monkeypatch):
    report = json.loads((harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json').read_text())
    original = tmp_path / 'original' / 'report.json'; original.parent.mkdir()
    raw = json.dumps(report).encode(); original.write_bytes(raw)
    data = tmp_path / 'dataset'; data.mkdir()
    pin = probe.source_pin()
    calls = iter((pin, 'changed'))
    monkeypatch.setattr(probe, 'source_pin', lambda: next(calls))
    with pytest.raises(ValueError, match='Probe identity changed'):
        harness.run(data, original, hashlib.sha256(raw).hexdigest(), tmp_path / 'output')
    published = json.loads((tmp_path / 'output' / 'report.json').read_text())
    assert published['status'] == 'incomplete' and published['probe_sha256'] == pin
    assert original.read_bytes() == raw


def test_campaign_failure_cannot_publish_complete(tmp_path, monkeypatch):
    report = json.loads((harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json').read_text())
    original = tmp_path / 'original' / 'report.json'; original.parent.mkdir()
    raw = json.dumps(report).encode(); original.write_bytes(raw)
    data = tmp_path / 'dataset'; data.mkdir()
    monkeypatch.setattr(harness.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(harness.matrix, 'expected_cells', lambda _: report['expected_cells'])
    monkeypatch.setattr(harness, 'select_wait', lambda *args: (_ for _ in ()).throw(ValueError('private detail')))
    with pytest.raises(ValueError):
        harness.run(data, original, hashlib.sha256(raw).hexdigest(), tmp_path / 'output')
    published = (tmp_path / 'output' / 'report.json').read_text()
    assert json.loads(published)['status'] == 'incomplete'
    assert 'private detail' not in published


def test_campaign_rejects_incomplete_fault_evidence(tmp_path, monkeypatch):
    report = json.loads((harness.ROOT / 'results/population-v3-runtime-matrix-20261030.json').read_text())
    original = tmp_path / 'original' / 'report.json'; original.parent.mkdir()
    raw = json.dumps(report).encode(); original.write_bytes(raw)
    data = tmp_path / 'dataset'; data.mkdir()
    monkeypatch.setattr(harness.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(harness.matrix, 'expected_cells', lambda _: report['expected_cells'])
    monkeypatch.setattr(harness, 'select_wait', lambda data, cell, check: cell)
    monkeypatch.setattr(harness, 'exercise_wait', lambda data, cell, output, check:
        {'runtime_id': cell['runtime_id'], 'case_id': cell['case_id'], 'policy': cell['policy'],
         'original_cell_status': 'blocked', 'status': 'passed', 'faults': []})
    with pytest.raises(ValueError, match='Recovery evidence is incomplete'):
        harness.run(data, original, hashlib.sha256(raw).hexdigest(), tmp_path / 'output')
    published = json.loads((tmp_path / 'output' / 'report.json').read_text())
    assert published['status'] == 'incomplete' and published['cases'] == []
    assert original.read_bytes() == raw
