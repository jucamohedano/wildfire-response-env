import json
from types import SimpleNamespace

import pytest

from eval import run_source_restricted_passive_v2 as driver


def test_passive_policy_one_60_minute_wait_without_reads():
    calls = []
    host = SimpleNamespace(advance=lambda minutes: calls.append(minutes))
    driver.PassiveWait60(lambda: calls.append('guard')).act(host, {'minute': 0})
    assert calls == ['guard', 60, 'guard']


def test_wait_continues_after_restore_and_preserves_terminal_cases(tmp_path, monkeypatch):
    class Selector:
        def case_ids(self):
            return [f'private-{i}' for i in range(12)]
    class Host:
        def __init__(self, inputs, selector, parent, *, case_id, **kwargs):
            self.minute, self.status, self.case_id = 0, 'active', case_id
        def situation(self):
            return {'minute': self.minute, 'status': self.status}
        def checkpoint(self):
            return {'minute': self.minute, 'status': self.status}
        def restore(self, state, *, expected_checkpoint_sha256):
            self.minute, self.status = state['minute'], state['status']
        def advance(self, minutes):
            for _ in range(minutes):
                self.minute += 1
                if self.case_id == 'private-1' and self.minute == 7:
                    self.status = 'censored'
                    break
                if self.case_id == 'private-2' and self.minute == 8:
                    self.status = 'unsupported'
                    break
    monkeypatch.setattr(driver.base, 'prepare_inputs', lambda: (object(), Selector(), {}))
    monkeypatch.setattr('wildfire_source_restricted_v1.RestrictedSourceIncidentV1', Host)
    args = SimpleNamespace(checkpoint=tmp_path / 'progress', key_file=tmp_path / 'key',
        host_sha256=driver.base.digest_file(driver.base.HOST),
        scorer_sha256=driver.base.digest_file(driver.base.SCORER),
        wall_seconds=100, max_decisions=1, resume=False)
    first = driver.run(args)
    assert first['cases'][0]['minute'] == 60
    assert first['cases'][1]['status'] == 'censored' and first['cases'][1]['minute'] == 7
    assert first['cases'][2]['status'] == 'blocked' and first['cases'][2]['minute'] == 8
    assert not any(row['score'] is not None for row in first['cases'])
    log = [json.loads(line) for line in (tmp_path / 'progress.errors.jsonl').read_text().splitlines()]
    assert len(log) == 1 and log[0]['stage'] == 'unsupported_event'
    assert log[0]['detail'] == 'unsupported scheduled event'
    before = args.checkpoint.read_bytes()
    original_key = args.key_file.read_bytes()
    args.key_file.write_bytes(bytes([original_key[0] ^ 1]) + original_key[1:])
    args.resume = True
    with pytest.raises(ValueError, match='authentication or restore'):
        driver.run(args)
    assert args.checkpoint.read_bytes() == before
    args.key_file.write_bytes(original_key)
    second = driver.run(args)
    assert second['cases'][0]['minute'] == 120
    assert second['cases'][1] == first['cases'][1]
    assert second['cases'][2] == first['cases'][2]


def test_opt_in_required(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['driver', '--checkpoint', str(tmp_path / 'p'),
        '--key-file', str(tmp_path / 'k'), '--host-sha256', 'a' * 64, '--scorer-sha256', 'b' * 64])
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 2
    assert not (tmp_path / 'p').exists()
