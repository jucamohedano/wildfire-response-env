import json
import hashlib
import io

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from eval import check_control_v3_parity as runner


def test_full_subset_and_incomplete_have_distinct_statuses():
    cases = [{'status': 'passed'}]
    assert runner._suite_status(cases, 1, 6) == 'subset_passed'
    assert runner._suite_status(cases, 1, 1) == 'passed'
    assert runner._suite_status(cases, 2, 6) == 'incomplete'
    assert runner._suite_status([{'status': 'incomplete'}], 1, 1) == 'incomplete'


def test_expired_budget_fails_closed(monkeypatch):
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 10)
    with pytest.raises(runner.BudgetExpired):
        runner._budget_check(10)
    runner._budget_check(11)


def test_reserving_output_never_overwrites_an_existing_file(tmp_path):
    output = tmp_path/'result.json'
    output.write_text('previous result')
    with pytest.raises(FileExistsError):
        runner._reserve_output(output, {'status':'running'})
    assert output.read_text() == 'previous result'


def test_failed_result_publication_is_sanitized(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, '_verified_release', lambda *args: (_ for _ in ()).throw(ValueError('PRIVATE source')))
    monkeypatch.setattr(runner, '_publish', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('PRIVATE path')))
    output = tmp_path/'result.json'
    assert runner.main(['--data-dir', str(tmp_path), '--output', str(output)]) == 1
    assert json.loads(output.read_text())['status'] == 'running'
    captured = capsys.readouterr()
    assert 'PRIVATE' not in captured.out + captured.err + output.read_text()


def test_setup_failure_is_sanitized_and_nonzero(tmp_path, monkeypatch, capsys):
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('PRIVATE evaluator detail')
    monkeypatch.setattr(runner, '_verified_release', lambda *args: ({}, {}))
    monkeypatch.setattr(runner, 'make_environment', fail)
    output = tmp_path/'report.json'
    code = runner.main(['--data-dir', str(tmp_path), '--output', str(output)])
    captured = capsys.readouterr()
    assert calls and code == 1 and json.loads(output.read_text())['status'] == 'failed'
    assert 'PRIVATE' not in captured.out + captured.err + output.read_text()


def test_release_manifest_and_task_bytes_are_pinned(tmp_path):
    (tmp_path/'tasks').mkdir()
    buf = io.BytesIO()
    pq.write_table(pa.table({'task_id':['case'], 'runtime_id':['fixture'], 'scenario_sha256':['s']}), buf)
    task = buf.getvalue()
    (tmp_path/'tasks/development.parquet').write_bytes(task)
    manifest = {'engine_implementation_sha256':'e', 'lifecycle_sha256':'l',
                'files':[{'path':'tasks/development.parquet', 'bytes':len(task),
                          'sha256':hashlib.sha256(task).hexdigest()}]}
    raw = json.dumps(manifest).encode()
    (tmp_path/'manifest.json').write_bytes(raw)
    lock = tmp_path/'lock.json'
    lock.write_text(json.dumps({'manifest_sha256':hashlib.sha256(raw).hexdigest(),
                                'engine_implementation_sha256':'e', 'lifecycle_sha256':'l'}))
    assert runner._verified_release(tmp_path, 'fixture', lock)[1] == {'case':'s'}
    (tmp_path/'tasks/development.parquet').write_bytes(task + b'x')
    with pytest.raises(ValueError, match='task table pin'):
        runner._verified_release(tmp_path, 'fixture', lock)
    (tmp_path/'manifest.json').write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='manifest pin'):
        runner._verified_release(tmp_path, 'fixture', lock)


@pytest.mark.parametrize('mode,status,code', [
    ('normal','subset_passed',0), ('step_late','incomplete',1),
    ('verification_late','incomplete',1), ('candidate_verify_late','incomplete',1),
    ('report_late','incomplete',1), ('final_write_late','incomplete',1), ('truncated','incomplete',1),
    ('reward_mismatch','failed',1), ('identity_mismatch','failed',1),
])
def test_full_runner_failure_and_timeout_paths(tmp_path, monkeypatch, mode, status, code):
    clock = {'now':0, 'created':0}
    monkeypatch.setattr(runner.time, 'monotonic', lambda: clock['now'])
    cases = ['case'] + [f'case-{i}' for i in range(5)]
    entry = {'scenario_ids':cases, 'inputs_sha256':'i', 'network_sha256':'n'}
    manifest = {'engine_implementation_sha256':'e', 'runtimes':{'fixture':entry}}
    pins = {case:'s' for case in cases}
    monkeypatch.setattr(runner, '_verified_release', lambda *args: (manifest, pins))

    class Env:
        def __init__(self, baseline):
            self.baseline = baseline
            self.minute = 0
            self.terminated = self.truncated = False

        def reset(self, case):
            return {'report_count':0}, {}

        def trajectory(self):
            return {'interface_version':'wildfire-incident-day-v2', 'task_id':'case', 'seed':1,
                    'scenario_sha256':'bad' if mode == 'identity_mismatch' else 's',
                    'inputs_sha256':'i', 'network_sha256':'n', 'implementation_sha256':'e'}

        def step(self, action):
            self.minute = 1440 if mode != 'truncated' else 5
            self.terminated = mode != 'truncated'
            self.truncated = mode == 'truncated'
            if self.baseline and mode == 'step_late':
                clock['now'] = 11
            return {'report_count':0}, (0.9 if mode == 'reward_mismatch' else 0.6), self.terminated, self.truncated, {}

        def verify_episode(self):
            if mode == 'verification_late':
                clock['now'] = 11
            return {'passed':True, 'exact_replay':True, 'independent_reward_agreement':True,
                    'outcomes':{'graded_score':0.6}}

    def make_environment(*args, **kwargs):
        clock['created'] += 1
        return Env(clock['created'] == 1), entry

    class Control:
        def __init__(self, env):
            self.env = env

        def reset(self, case):
            return self.env.reset(case)

        def execute(self, name, arguments):
            obs, _, term, trunc, info = self.env.step({'advance_minutes':60})
            return {'observation':obs, 'reward':None, 'terminated':term, 'truncated':trunc,
                    'info':info, 'verification_required':True,
                    'briefing':{'reports':{'delivered_count':0}}}

        def finalize(self, **kwargs):
            if mode == 'candidate_verify_late':
                clock['now'] = 11
            return {'status':'full_day', 'verified':True, 'score':0.6}

    monkeypatch.setattr(runner, 'make_environment', make_environment)
    monkeypatch.setattr(runner, 'ControlCenterV3', Control)
    if mode in ('report_late', 'final_write_late'):
        original_write = runner._write_stage
        def delayed_write(path, report):
            result = original_write(path, report)
            if ((mode == 'report_late' and report.get('status') == 'running' and report.get('cases')
                 and report['cases'][-1].get('status') == 'passed')
                    or (mode == 'final_write_late' and report.get('status') == 'subset_passed')):
                clock['now'] = 11
            return result
        monkeypatch.setattr(runner, '_write_stage', delayed_write)
    output = tmp_path/'result.json'
    actual = runner.main(['--data-dir', str(tmp_path), '--wall-seconds', '10',
                          '--limit', '1', '--output', str(output)])
    assert actual == code and json.loads(output.read_text())['status'] == status


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '0'])
def test_invalid_wall_budget_is_rejected(tmp_path, value):
    with pytest.raises(SystemExit) as error:
        runner.main(['--data-dir', str(tmp_path), '--wall-seconds', value,
                     '--output', str(tmp_path/'result.json')])
    assert error.value.code == 2
