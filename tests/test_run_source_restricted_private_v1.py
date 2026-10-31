import hashlib
import json
from types import SimpleNamespace

import pytest

from eval import run_source_restricted_private_v1 as driver


def test_policy_advances_once_and_checks_pins():
    calls = []
    host = SimpleNamespace(advance=lambda minute: calls.append(minute))
    policy = driver.PassivePolicy(lambda: calls.append('guard'))
    policy.act(host, {})
    assert calls == ['guard', 1, 'guard']


def test_approval_requires_private_pinned_complete_manifest(tmp_path):
    path = tmp_path / 'approval.json'
    data = {'version': 'wildfire-restricted-evaluation-approval-v1', 'cases': {
        case: {'case_id': 'private-' + case, 'mask': [1], 'public_population': [], 'identity': {}}
        for case in driver.PUBLIC_IDS}}
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(driver.load_approval(path, pin)) == set(driver.PUBLIC_IDS)
    with pytest.raises(ValueError, match='pin'):
        driver.load_approval(path, '0' * 64)
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private regular'):
        driver.load_approval(path, pin)


def test_unscored_driver_attempts_all_and_records_private_errors(tmp_path, monkeypatch):
    class Selector:
        def case_ids(self):
            return [f'private-{i}' for i in range(12)]
    class FakeHost:
        def __init__(self, inputs, selector, parent, *, case_id, **kwargs):
            if case_id == 'private-2':
                raise ValueError('private detail')
            self.minute = 0
            self.status = 'active'
            self.case_id = case_id
        def situation(self):
            return {'minute': self.minute, 'status': self.status}
        def checkpoint(self):
            return {'minute': self.minute, 'status': self.status}
        def restore(self, state, *, expected_checkpoint_sha256):
            self.minute, self.status = state['minute'], state['status']
        def advance(self, minutes):
            self.minute += minutes
            if self.case_id == 'private-1':
                self.status = 'censored'
    monkeypatch.setattr(driver, 'prepare_inputs', lambda: (object(), Selector(), {}))
    monkeypatch.setattr('wildfire_source_restricted_v1.RestrictedSourceIncidentV1', FakeHost)
    args = SimpleNamespace(checkpoint=tmp_path / 'progress', key_file=tmp_path / 'key', host_sha256=driver.digest_file(driver.HOST),
        scorer_sha256=driver.digest_file(driver.SCORER), approval=None, approval_sha256=None,
        wall_seconds=100, max_decisions=1, resume=False)
    result = driver.run(args)
    assert result['cases'][0]['reason'] == 'decision_limit'
    assert result['cases'][1]['status'] == 'censored'
    assert result['cases'][2]['status'] == 'blocked'
    assert all(row['status'] != 'restricted_scored' for row in result['cases'])
    assert all(row['reason'] != 'not_started' for row in result['cases'])
    assert 'private detail' not in json.dumps(result)
    error_file = tmp_path / 'progress.errors.jsonl'
    assert error_file.stat().st_mode & 0o777 == 0o600
    error = json.loads(error_file.read_text())
    assert error['stage'] == 'factory' and error['detail'] == 'private detail'
    args.resume = True
    saved_key = args.key_file.read_bytes()
    args.key_file.write_bytes(bytes([saved_key[0] ^ 1]) + saved_key[1:])
    before = args.checkpoint.read_bytes()
    with pytest.raises(ValueError, match='authentication or restore'):
        driver.run(args)
    assert args.checkpoint.read_bytes() == before
    args.key_file.write_bytes(saved_key)
    second = driver.run(args)
    assert second['cases'][0]['minute'] == 2
    assert second['cases'][1]['status'] == 'censored'
    assert second['cases'][2]['status'] == 'blocked'
    assert args.key_file.stat().st_mode & 0o777 == 0o600


def test_continuation_key_is_private_and_reused(tmp_path):
    key_file = tmp_path / 'key'
    key = driver.load_key(key_file, resume=False)
    assert len(key) == 32 and key_file.stat().st_mode & 0o777 == 0o600
    assert driver.load_key(key_file, resume=True) == key
    with pytest.raises(FileExistsError):
        driver.load_key(key_file, resume=False)
    key_file.chmod(0o644)
    with pytest.raises(ValueError):
        driver.load_key(key_file, resume=True)
