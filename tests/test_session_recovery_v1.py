import copy
import json
import os
import stat
import subprocess
import sys

import pytest

from eval.session_recovery_v1 import SessionRecoveryError, SessionRecoveryV1
from tests.test_wait_recovery_v1 import control
from wildfire_control_v3.control import interface_digest
from envs.wildfire_response.incident_day.environment import implementation_digest


def evaluate(state, action, result):
    state['count'] = state.get('count', 0) + 1
    state.setdefault('actions', []).append(action)
    state['last_minute'] = result['observation']['minute']


def session(directory, policy='dispatcher', identity='session', c=None):
    return SessionRecoveryV1(c or control(), directory, run_identity=identity,
                             policy_name=policy, evaluation={'count': 0},
                             evaluator=evaluate, evaluator_identity='test-evaluator-v1')


@pytest.fixture
def private(tmp_path):
    p = tmp_path / 'private'
    p.mkdir(mode=0o700)
    return p


CHILD = r'''
import os, sys
from tests.test_session_recovery_v1 import session
s = session(sys.argv[1], sys.argv[2])
s.initialize()
point = sys.argv[3]
write = s._write
execute = s.control.execute

def fault_write(directory, record):
    if point == 'before_selection_commit' and record['phase'] == 'selected': os._exit(73)
    if point == 'before_commit' and record['phase'] == 'complete' and record['sequence'] == 1:
        os._exit(73)
    write(directory, record)
    if point == 'selected' and record['phase'] == 'selected': os._exit(73)
    if point == 'committed' and record['phase'] == 'complete' and record['sequence'] == 1: os._exit(73)

def fault_execute(name, args):
    if point == 'during' and name in ('advance', 'commit_orders', 'request_status'):
        env = s.control.environment
        tick = env._tick
        def interrupted_tick():
            tick()
            os._exit(73)
        env._tick = interrupted_tick
    return execute(name, args)

s._write = fault_write
s.control.execute = fault_execute
s.step(1)
raise RuntimeError('fault not reached')
'''


@pytest.mark.parametrize('policy', ['passive', 'dispatcher'])
@pytest.mark.parametrize('point', ['before_selection_commit', 'selected', 'during', 'before_commit', 'committed'])
def test_process_restart_and_continuation(private, policy, point):
    baseline_dir = private / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    baseline = session(baseline_dir, policy)
    baseline.initialize()
    expected = baseline.step(1)
    expected_state = baseline._snapshot()
    proc = subprocess.run([sys.executable, '-c', CHILD, str(private), policy, point],
                          capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert proc.returncode == 73, proc.stderr
    record = json.loads((private / 'session-recovery-v1.json').read_text())['record']
    expected_phase = 'complete' if point in ('committed', 'before_selection_commit') else 'selected'
    assert record['phase'] == expected_phase
    if point != 'before_selection_commit':
        assert record['action'] == expected_state['evaluation']['actions'][0]
    resumed = session(private, policy)
    if point == 'before_selection_commit':
        assert resumed.recover() is None
        assert resumed.step(1) == expected
    else:
        assert resumed.recover() == expected
    assert resumed._snapshot() == expected_state
    assert resumed.step(1) == expected
    assert resumed._snapshot() == expected_state
    for sequence in (2, 3):
        assert resumed.step(sequence) == baseline.step(sequence)
        assert resumed._snapshot() == baseline._snapshot()
    assert resumed.calls[0]['before_minute'] == 0
    names = [c['name'] for c in resumed.calls]
    assert ('advance' if policy == 'passive' else 'commit_orders') in names
    assert resumed.observer.snapshot()['coverage'].get('ticks_observed', 0) > 0


def test_lost_response_restart_and_stale_writer(private):
    first = session(private)
    first.initialize()
    result = first.step(1)
    replacement = session(private)
    assert replacement.recover() == result
    assert replacement.step(1) == result
    second = replacement.step(2)
    assert second['observation']['minute'] > result['observation']['minute']
    state = first._snapshot()
    for _ in range(2):
        with pytest.raises(SessionRecoveryError):
            first.recover()
    assert first._snapshot() == state
    with pytest.raises(SessionRecoveryError):
        first.step(3)


def test_corruption_identity_and_older_journal_fail_closed(private):
    live = session(private)
    live.initialize()
    path = private / 'session-recovery-v1.json'
    initial = path.read_bytes()
    live.step(1)
    before = live._snapshot()
    valid = path.read_bytes()
    for data in (b'{', valid.replace(b'"sequence":1', b'"sequence":2'),
                 b'{"record":{},"record":{},"sha256":"x"}'):
        path.write_bytes(data)
        with pytest.raises(SessionRecoveryError):
            live.recover()
        assert live._snapshot() == before
    path.write_bytes(valid)
    with pytest.raises(SessionRecoveryError):
        session(private, identity='other').recover()
    path.write_bytes(initial)
    with pytest.raises(SessionRecoveryError):
        live.recover()
    assert live._snapshot() == before
    # No local journal can detect rollback to a valid older file after restart.
    rolled_back = session(private)
    assert rolled_back.recover() is None
    assert rolled_back._snapshot() != before


def test_selection_failure_rollback_and_changed_state_rejected(private, monkeypatch):
    live = session(private, 'dispatcher')
    live.initialize()
    initial = live._snapshot()
    original = live.policy.action
    def fail(public, observation):
        original(public, observation)
        raise RuntimeError('private')
    monkeypatch.setattr(live.policy, 'action', fail)
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    assert live._snapshot() != initial
    assert live.recover() is None
    assert live._snapshot() == initial
    expected_dir = private / 'baseline'
    expected_dir.mkdir(mode=0o700)
    baseline = session(expected_dir)
    baseline.initialize()
    assert live.step(1) == baseline.step(1)
    assert live._snapshot() == baseline._snapshot()


def test_pending_failure_cannot_discard_later_mutation(private, monkeypatch):
    live = session(private, 'passive')
    live.initialize()
    execute = live.control.execute
    def fail(name, arguments):
        if name == 'advance':
            execute(name, {'max_minutes': 1})
            raise RuntimeError('partial')
        return execute(name, arguments)
    monkeypatch.setattr(live.control, 'execute', fail)
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    monkeypatch.setattr(live.control, 'execute', execute)
    live.control.execute('check_action', {'action': {'releases': ['unknown-unit']}})
    changed = live._snapshot()
    with pytest.raises(SessionRecoveryError):
        live.recover()
    assert live._snapshot() == changed
    with pytest.raises(SessionRecoveryError):
        live.recover()
    assert live._snapshot() == changed


def test_restore_failure_rolls_back_entire_session(private, monkeypatch):
    live = session(private)
    live.initialize()
    live.step(1)
    fresh = session(private)
    before = fresh._snapshot()
    from eval import session_recovery_v1 as module
    original = module.MovementObserver.restore
    def fail(self, value):
        raise ValueError('invalid observer')
    monkeypatch.setattr(module.MovementObserver, 'restore', fail)
    with pytest.raises(SessionRecoveryError):
        fresh.recover()
    assert fresh._snapshot() == before
    monkeypatch.setattr(module.MovementObserver, 'restore', original)
    assert fresh.recover() == live.step(1)
    assert fresh._snapshot() == live._snapshot()


@pytest.mark.parametrize('phase', ['selected', 'complete'])
def test_sync_failure_requires_later_durable_recovery(private, monkeypatch, phase):
    live = session(private, 'passive')
    live.initialize()
    real = os.fsync
    def fail(fd):
        path = private / 'session-recovery-v1.json'
        if stat.S_ISDIR(os.fstat(fd).st_mode) and path.exists():
            record = json.loads(path.read_text())['record']
            if record['sequence'] == 1 and record['phase'] == phase:
                raise OSError('disk')
        return real(fd)
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    state = live._snapshot()
    with pytest.raises(SessionRecoveryError):
        live.recover()
    assert live._snapshot() == state
    monkeypatch.setattr(os, 'fsync', real)
    assert live.recover()['observation']['minute'] > 0
    assert live.step(1)['observation']['minute'] == live.observation['minute']


def test_private_storage_and_preserved_packages(private):
    core, interface = implementation_digest(), interface_digest()
    live = session(private, 'passive')
    live.initialize()
    live.step(1)
    assert all(os.stat(p).st_mode & 0o077 == 0 for p in private.iterdir())
    assert implementation_digest() == core and interface_digest() == interface
    private.chmod(0o755)
    with pytest.raises(SessionRecoveryError):
        live.recover()


class LocalPolicy:
    def __init__(self):
        self.counter = 0

    def checkpoint(self):
        return {'version': 1, 'counter': self.counter}

    def restore(self, state):
        if set(state) != {'version', 'counter'} or state['version'] != 1 or type(state['counter']) is not int:
            raise ValueError('invalid policy state')
        self.counter = state['counter']

    def action(self, public, observation):
        public.check_action({'releases': ['unknown-unit']})
        self.counter += 1
        return {'advance_minutes': 3 + self.counter}


def test_injected_local_policy_identity_and_state(private, monkeypatch):
    def local(directory, identity='local-v1-config-3'):
        return SessionRecoveryV1(control(), directory, run_identity='local', policy_name='custom',
                                 policy_factory=LocalPolicy, policy_identity=identity)
    baseline_dir = private / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    baseline = local(baseline_dir)
    baseline.initialize()
    expected = baseline.step(1)
    live = local(private)
    live.initialize()
    write = live._write
    def fail_after_selected(directory, record):
        write(directory, record)
        if record['phase'] == 'selected':
            raise OSError('interrupted')
    monkeypatch.setattr(live, '_write', fail_after_selected)
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    journal = json.loads((private / 'session-recovery-v1.json').read_text())['record']
    assert journal['state']['policy']['counter'] == 1
    assert len(journal['state']['calls']) == 1
    with pytest.raises(SessionRecoveryError):
        local(private, identity='other-config').recover()
    resumed = local(private)
    assert resumed.recover() == expected
    assert resumed._snapshot() == baseline._snapshot()
    assert resumed.step(2) == baseline.step(2)
    assert resumed._snapshot() == baseline._snapshot()
    with pytest.raises(ValueError):
        SessionRecoveryV1(control(), private, run_identity='local', policy_name='custom', policy_factory=LocalPolicy)


def test_fresh_recovery_failure_can_retry_without_process_restart(private, monkeypatch):
    original = session(private, 'passive')
    original.initialize()
    write = original._write
    def interrupt(directory, record):
        write(directory, record)
        if record['phase'] == 'selected':
            raise OSError('interrupted')
    monkeypatch.setattr(original, '_write', interrupt)
    with pytest.raises(SessionRecoveryError):
        original.step(1)
    resumed = session(private, 'passive')
    execute = resumed.control.execute
    def fail_once(name, arguments):
        if name == 'advance':
            execute(name, {'max_minutes': 1})
            raise OSError('partial')
        return execute(name, arguments)
    monkeypatch.setattr(resumed.control, 'execute', fail_once)
    with pytest.raises(SessionRecoveryError):
        resumed.recover()
    monkeypatch.setattr(resumed.control, 'execute', execute)
    result = resumed.recover()
    assert resumed.step(1) == result
    baseline_dir = private / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    baseline = session(baseline_dir, 'passive')
    baseline.initialize()
    assert baseline.step(1) == result
    assert resumed._snapshot() == baseline._snapshot()


def test_advisory_lock_blocks_other_process(private):
    import fcntl
    live = session(private, 'passive')
    live.initialize()
    live.step(1)
    fd = os.open(private / 'session-recovery-v1.lock', os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    script = "from tests.test_session_recovery_v1 import session; import sys; session(sys.argv[1], 'passive').recover()"
    child = subprocess.Popen([sys.executable, '-c', script, str(private)],
                             env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=1)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    out, err = child.communicate(timeout=60)
    assert child.returncode == 0, (out, err)


def test_evaluator_failure_does_not_commit_and_cannot_rollback(private, monkeypatch):
    live = session(private, 'passive')
    live.initialize()
    journal = private / 'session-recovery-v1.json'
    previous = journal.read_bytes()
    def fail_evaluator(state, action, result):
        evaluate(state, action, result)
        raise RuntimeError('injected evaluator failure')
    monkeypatch.setattr(live, 'evaluator', fail_evaluator)
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    selected = journal.read_bytes()
    assert json.loads(selected)['record']['phase'] == 'selected'
    partial = live._snapshot()
    journal.write_bytes(previous)
    with pytest.raises(SessionRecoveryError):
        live.recover()
    assert live._snapshot() == partial
    journal.write_bytes(selected)
    monkeypatch.setattr(live, 'evaluator', evaluate)
    result = live.recover()
    baseline_dir = private / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    baseline = session(baseline_dir, 'passive')
    baseline.initialize()
    assert result == baseline.step(1)
    assert live._snapshot() == baseline._snapshot()


@pytest.mark.parametrize('name,args', [
    ('advance', {'max_minutes': 1}),
    ('advance_time', {'max_minutes': 1}),
    ('commit_orders', {'action': {}}),
    ('request_status', {'center_id': 'unknown', 'subject': 'situation'}),
    ('unknown_tool', {}),
])
@pytest.mark.parametrize('catch', [False, True])
def test_policy_selection_rejects_mutating_or_unknown_tools(private, name, args, catch):
    class AttemptPolicy(LocalPolicy):
        def action(self, public, observation):
            self.counter += 1
            try:
                public.execute(name, args)
            except ValueError:
                if not catch:
                    raise
            return {'advance_minutes': 1}
    live = SessionRecoveryV1(control(), private, run_identity='forbidden-tool', policy_name='custom',
                             policy_factory=AttemptPolicy, policy_identity='attempt-v1')
    live.initialize()
    before = live._snapshot()
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    assert live.control.private_checkpoint() == before['control']
    assert live.calls == []
    assert live.control.environment.minute == 0
    assert json.loads((private / 'session-recovery-v1.json').read_text())['record']['sequence'] == 0
    assert live.recover() is None
    assert live._snapshot() == before


def test_nonserializable_failed_evaluator_poisons_instance_and_fresh_restart_recovers(private):
    live = session(private, 'passive')
    live.initialize()
    def broken(state, action, result):
        state['invalid'] = float('nan')
        raise ValueError('private evaluator failure')
    live.evaluator = broken
    with pytest.raises(SessionRecoveryError) as error:
        live.step(1)
    assert str(error.value) == 'Session recovery failed'
    assert live._poisoned is True
    assert json.loads((private / 'session-recovery-v1.json').read_text())['record']['phase'] == 'selected'
    # Even removing the invalid value does not authorize rollback of this instance.
    del live.evaluation['invalid']
    state = live._snapshot()
    with pytest.raises(SessionRecoveryError):
        live.recover()
    with pytest.raises(SessionRecoveryError):
        live.step(1)
    assert live._snapshot() == state
    fresh = session(private, 'passive')
    result = fresh.recover()
    baseline_dir = private / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    baseline = session(baseline_dir, 'passive')
    baseline.initialize()
    assert result == baseline.step(1)
    assert fresh._snapshot() == baseline._snapshot()


def test_nonserializable_failure_during_restart_also_poisons(private, monkeypatch):
    initial = session(private, 'passive')
    initial.initialize()
    original_write = initial._write
    def interrupt(directory, record):
        original_write(directory, record)
        if record['phase'] == 'selected':
            raise OSError('interrupted')
    monkeypatch.setattr(initial, '_write', interrupt)
    with pytest.raises(SessionRecoveryError):
        initial.step(1)
    restarted = session(private, 'passive')
    def broken(state, action, result):
        state['invalid'] = float('nan')
        raise ValueError('private')
    restarted.evaluator = broken
    with pytest.raises(SessionRecoveryError):
        restarted.recover()
    assert restarted._poisoned is True
    with pytest.raises(SessionRecoveryError):
        restarted.recover()
    recovered = session(private, 'passive')
    assert recovered.recover()['observation']['minute'] > 0
    assert recovered.evaluation['count'] == 1
