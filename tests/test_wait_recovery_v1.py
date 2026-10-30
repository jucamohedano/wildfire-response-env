import fcntl
import json
import os
from pathlib import Path
import subprocess
import stat
import sys

import pytest

from eval.wait_recovery_v1 import RecoveryError, WaitRecoveryV1, _digest
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_control_v3.control import ControlCenterV3, interface_digest, tool_definitions_v3
from envs.wildfire_response.incident_day.environment import implementation_digest


def control():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset('incident-day-egress-v1-000', seed=3866400)
    return ControlCenterV3(env)


def driver(c, directory, identity='episode-42'):
    return WaitRecoveryV1(c, directory, run_identity=identity)


@pytest.fixture
def private(tmp_path):
    path = tmp_path / 'private'
    path.mkdir(mode=0o700)
    return path


# Kill the actual process at four points. The during-wait fault deliberately
# advances one minute before dying to model a partially mutated in-memory engine.
CHILD = r'''
import os, sys
from eval.wait_recovery_v1 import WaitRecoveryV1
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_control_v3.control import ControlCenterV3
inputs, network = fixture_inputs()
env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                     expected_network_sha256=network['sha256'])
env.reset('incident-day-egress-v1-000', seed=3866400)
c = ControlCenterV3(env)
d = WaitRecoveryV1(c, sys.argv[1], run_identity='episode-42')
point = sys.argv[2]
write = d._write
execute = c.execute

def fault_write(directory, record):
    if point == 'after_execution' and record['phase'] == 'complete': os._exit(73)
    write(directory, record)
    if point == 'before' and record['phase'] == 'pending': os._exit(73)
    if point == 'after_commit' and record['phase'] == 'complete': os._exit(73)

def fault_execute(name, args):
    if point == 'during':
        execute('advance', {'max_minutes': 1})
        os._exit(73)
    return execute(name, args)

d._write = fault_write
c.execute = fault_execute
d.start(1, 5, initialize=True)
raise AssertionError('crash not injected')
'''


@pytest.mark.parametrize('point', ['before', 'during', 'after_execution', 'after_commit'])
def test_process_crash_recovery_and_deterministic_continuation(private, point):
    baseline = control()
    expected = baseline.execute('advance', {'max_minutes': 5})
    expected_checkpoint = baseline.private_checkpoint()
    child = subprocess.run([sys.executable, '-c', CHILD, str(private), point],
                           env=os.environ.copy(), capture_output=True, text=True, timeout=60)
    assert child.returncode == 73, child.stderr
    journal = json.loads((private / 'wait-recovery-v1.json').read_text())
    assert journal['record']['phase'] == ('complete' if point == 'after_commit' else 'pending')
    recovered = control()
    d = driver(recovered, private)
    result = d.recover()
    assert result == expected
    assert recovered.private_checkpoint() == expected_checkpoint
    # An identical retry redelivers the stored result and never advances twice.
    assert d.start(1, 5) == expected
    assert recovered.private_checkpoint() == expected_checkpoint
    # Later outputs and all private RNG state agree with an uninterrupted run.
    assert d.start(2, 5) == baseline.execute('advance', {'max_minutes': 5})
    assert recovered.private_checkpoint() == baseline.private_checkpoint()


def test_actions_between_waits_and_fresh_driver_must_recover(private):
    original = control()
    d = driver(original, private)
    d.start(1, 3, initialize=True)
    replacement = driver(control(), private)
    with pytest.raises(RecoveryError):
        replacement.start(2, 3)
    assert replacement.recover() == d.start(1, 3)
    draft = {'action': {'releases': ['unknown-unit']}}
    assert replacement.control.execute('check_action', draft) == original.execute('check_action', draft)
    assert replacement.start(2, 3) == original.execute('advance', {'max_minutes': 3})
    assert replacement.control.private_checkpoint() == original.private_checkpoint()


def test_command_sequence_and_identity_fail_closed(private):
    c = control()
    d = driver(c, private)
    with pytest.raises(RecoveryError):
        d.recover()
    with pytest.raises(RecoveryError):
        d.start(1, 5)
    d.start(1, 5, initialize=True)
    state = c.private_checkpoint()
    for sequence, minutes, initial in [(1, 6, False), (3, 5, False), (0, 5, False),
                                       (2, True, False), (2, 61, False), (1, 5, True)]:
        with pytest.raises(RecoveryError):
            d.start(sequence, minutes, initialize=initial)
        assert c.private_checkpoint() == state
    mismatch = driver(control(), private, identity='other-secret-identity')
    before = mismatch.control.private_checkpoint()
    with pytest.raises(RecoveryError) as exc:
        mismatch.recover()
    assert 'other-secret-identity' not in str(exc.value)
    assert mismatch.control.private_checkpoint() == before
    d.start(2, 5)
    with pytest.raises(RecoveryError):
        d.start(1, 5)


@pytest.mark.parametrize('corruption', ['truncated', 'checksum', 'driver_pin', 'checkpoint', 'duplicate'])
def test_corrupt_or_mismatched_journal_never_advances(private, corruption):
    c = control()
    d = driver(c, private)
    d.start(1, 5, initialize=True)
    path = private / 'wait-recovery-v1.json'
    value = json.loads(path.read_text())
    if corruption == 'truncated':
        path.write_text('{')
    elif corruption == 'duplicate':
        path.write_text('{"record": {}, "record": {}, "sha256": "x"}')
    else:
        if corruption == 'checksum':
            value['sha256'] = '0' * 64
        elif corruption == 'driver_pin':
            value['record']['driver_sha256'] = '0' * 64
            value['sha256'] = _digest(value['record'])
        else:
            value['record']['checkpoint']['interface_sha256'] = '0' * 64
            value['sha256'] = _digest(value['record'])
        path.write_text(json.dumps(value))
    target = control()
    before = target.private_checkpoint()
    with pytest.raises(RecoveryError):
        driver(target, private).recover()
    assert target.private_checkpoint() == before


def test_partial_exception_restores_pre_wait_and_keeps_journal(private, monkeypatch):
    c = control()
    baseline = control()
    d = driver(c, private)
    execute = c.execute
    def fail(name, arguments):
        execute('advance', {'max_minutes': 1})
        raise RuntimeError('PRIVATE-SENTINEL')
    monkeypatch.setattr(c, 'execute', fail)
    with pytest.raises(RecoveryError) as exc:
        d.start(1, 5, initialize=True)
    assert 'PRIVATE-SENTINEL' not in str(exc.value)
    monkeypatch.setattr(c, 'execute', execute)
    assert d.recover() == baseline.execute('advance', {'max_minutes': 5})
    assert c.private_checkpoint() == baseline.private_checkpoint()


def test_journal_is_private_and_never_part_of_actor_output(private):
    c = control()
    d = driver(c, private, identity='PRIVATE-IDENTITY-SENTINEL')
    result = d.start(1, 1, initialize=True)
    assert 'PRIVATE-IDENTITY-SENTINEL' not in json.dumps(result)
    assert 'engine_checkpoint' not in json.dumps(result)
    assert not {'recover', 'start', 'private_checkpoint'} & {
        item['function']['name'] for item in tool_definitions_v3()}
    assert all(os.stat(p).st_mode & 0o077 == 0 for p in private.iterdir())
    private.chmod(0o755)
    with pytest.raises(RecoveryError):
        d.recover()


def test_no_active_artifact_dir_or_unexpected_files(private):
    c = control()
    c.environment.artifact_dir = private
    with pytest.raises(RecoveryError):
        driver(c, private).start(1, 1, initialize=True)
    assert not (private / 'wait-recovery-v1.json').exists()


def test_existing_engine_and_interface_digest_unchanged(private):
    interface_before = interface_digest()
    core_before = implementation_digest()
    release = json.loads((Path(__file__).parents[1] / 'data/hub_runtime_v1/release.json').read_text())
    assert core_before == release['engine_implementation_sha256']
    d = driver(control(), private)
    d.start(1, 1, initialize=True)
    assert interface_digest() == interface_before
    assert implementation_digest() == core_before


def test_episode_mismatch_fails_before_restore(private):
    d = driver(control(), private)
    d.start(1, 1, initialize=True)
    other = control()
    other.reset('incident-day-egress-v1-001')
    before = other.private_checkpoint()
    with pytest.raises(RecoveryError):
        driver(other, private).recover()
    assert other.private_checkpoint() == before


def test_writer_lock_serializes_processes(private):
    c = control()
    d = driver(c, private)
    d.start(1, 1, initialize=True)
    lock = os.open(private / 'wait-recovery-v1.lock', os.O_RDWR)
    fcntl.flock(lock, fcntl.LOCK_EX)
    code = CHILD.replace("point = sys.argv[2]", "point = sys.argv[2]")
    # The child cannot write the pending transaction while the owner holds the lock.
    child = subprocess.Popen([sys.executable, '-c', code, str(private), 'before'],
                             env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=1)
        assert json.loads((private / 'wait-recovery-v1.json').read_text())['record']['phase'] == 'complete'
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    # initialize=True is rejected after the lock is released; the journal stays intact.
    _, stderr = child.communicate(timeout=30)
    assert child.returncode != 0 and child.returncode != 73, stderr.decode()
    assert d.recover()['observation']['minute'] == c.environment.minute


def test_completed_retry_and_recovery_do_not_overwrite_newer_actions(private):
    c = control()
    d = driver(c, private)
    d.start(1, 3, initialize=True)
    c.execute('check_action', {'action': {'releases': ['unknown-unit']}})
    later = c.private_checkpoint()
    with pytest.raises(RecoveryError):
        d.start(1, 3)
    with pytest.raises(RecoveryError):
        d.recover()
    assert c.private_checkpoint() == later
    # A freshly attached wrapper must not overwrite this changed state either.
    with pytest.raises(RecoveryError):
        driver(c, private).recover()
    assert c.private_checkpoint() == later


def test_stale_driver_cannot_replay_newer_journal_position(private):
    c = control()
    d = driver(c, private)
    d.start(1, 1, initialize=True)
    stale = driver(control(), private)
    stale.recover()
    d.start(2, 1)
    before = stale.control.private_checkpoint()
    with pytest.raises(RecoveryError):
        stale.start(2, 1)
    with pytest.raises(RecoveryError):
        stale.recover()
    with pytest.raises(RecoveryError):
        stale.start(3, 1)
    assert stale.control.private_checkpoint() == before


def test_population_v3_engine_restores_checkpoint_and_replays(private):
    from wildfire_control_v3.control import create_population_control
    def population():
        inputs, network = fixture_inputs()
        c = create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                      expected_network_sha256=network['sha256'])
        c.reset('incident-day-egress-v1-000')
        return c
    baseline = population()
    live = population()
    expected = baseline.execute('advance', {'max_minutes': 5})
    assert driver(live, private).start(1, 5, initialize=True) == expected
    restored = population()
    d = driver(restored, private)
    assert d.recover() == expected
    assert restored.private_checkpoint() == baseline.private_checkpoint()
    assert d.start(2, 5) == baseline.execute('advance', {'max_minutes': 5})
    assert restored.private_checkpoint() == baseline.private_checkpoint()


def test_pending_second_wait_restores_prior_nonwait_control_state(private, monkeypatch):
    baseline = control()
    live = control()
    d = driver(live, private)
    assert d.start(1, 2, initialize=True) == baseline.execute('advance', {'max_minutes': 2})
    action = {'action': {'releases': ['unknown-unit']}}
    assert live.execute('check_action', action) == baseline.execute('check_action', action)
    expected = baseline.execute('advance', {'max_minutes': 4})
    write = d._write
    def interrupt(directory, record):
        write(directory, record)
        if record['sequence'] == 2 and record['phase'] == 'pending':
            raise OSError('injected interruption')
    monkeypatch.setattr(d, '_write', interrupt)
    with pytest.raises(RecoveryError):
        d.start(2, 4)
    restarted = control()
    assert driver(restarted, private).recover() == expected
    assert restarted.private_checkpoint() == baseline.private_checkpoint()


@pytest.mark.parametrize('phase', ['pending', 'complete'])
def test_directory_sync_failure_blocks_recovery_until_durable(private, monkeypatch, phase):
    c = control()
    d = driver(c, private)
    original_fsync = os.fsync
    original_execute = c.execute
    calls = []
    def execute(name, args):
        calls.append((name, args))
        return original_execute(name, args)
    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            path = private / 'wait-recovery-v1.json'
            if path.exists() and json.loads(path.read_text())['record']['phase'] == phase:
                raise OSError('PRIVATE-DISK-ERROR')
        return original_fsync(fd)
    monkeypatch.setattr(c, 'execute', execute)
    monkeypatch.setattr(os, 'fsync', fail_directory_sync)
    with pytest.raises(RecoveryError) as error:
        d.start(1, 5, initialize=True)
    assert error.value.__context__ is None and error.value.__cause__ is None
    before = c.private_checkpoint()
    count = len(calls)
    with pytest.raises(RecoveryError) as error:
        d.recover()
    assert error.value.__context__ is None
    assert c.private_checkpoint() == before and len(calls) == count
    monkeypatch.setattr(os, 'fsync', original_fsync)
    baseline = control()
    assert d.recover() == baseline.execute('advance', {'max_minutes': 5})
    assert c.private_checkpoint() == baseline.private_checkpoint()
    assert len(calls) == 1


@pytest.mark.parametrize('action', ['guard', 'commit'])
def test_pending_failure_cannot_discard_later_actions(private, monkeypatch, action):
    c = control()
    d = driver(c, private)
    execute = c.execute
    def fail(name, args):
        execute('advance', {'max_minutes': 1})
        raise RuntimeError('PRIVATE-PARTIAL-ERROR')
    monkeypatch.setattr(c, 'execute', fail)
    with pytest.raises(RecoveryError) as error:
        d.start(1, 5, initialize=True)
    assert error.value.__context__ is None and error.value.__cause__ is None
    monkeypatch.setattr(c, 'execute', execute)
    if action == 'guard':
        c.execute('check_action', {'action': {'releases': ['unknown-unit']}})
    else:
        c.execute('commit_orders', {'action': {'protection_orders': [
            {'group_id': 'population-0', 'action': 'shelter',
             'destination_id': None, 'approach_id': None}]}})
    after_action = c.private_checkpoint()
    with pytest.raises(RecoveryError) as error:
        d.recover()
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert c.private_checkpoint() == after_action


def test_caught_failure_is_not_chained_without_outer_exception(private, monkeypatch):
    d = driver(control(), private, identity='PRIVATE-IDENTITY')
    class PrivateError(Exception):
        pass
    def fail(*args, **kwargs):
        raise PrivateError('PRIVATE-EXCEPTION')
    monkeypatch.setattr(d.control, 'private_checkpoint', fail)
    with pytest.raises(RecoveryError) as error:
        d.start(1, 1, initialize=True)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert 'PRIVATE' not in str(error.value)


def test_outer_exception_context_and_traceback_stay_inside_trusted_boundary(private):
    d = driver(control(), private, identity='PRIVATE-IDENTITY')
    try:
        raise RuntimeError('PRIVATE-OUTER-CONTEXT')
    except RuntimeError as outer:
        with pytest.raises(RecoveryError) as caught:
            d.recover()
        error = caught.value
        assert error.__context__ is outer
        assert str(error) == 'Wait recovery failed'
        frames = []
        traceback = error.__traceback__
        while traceback is not None:
            frames.append(traceback.tb_frame)
            traceback = traceback.tb_next
        assert any(frame.f_locals.get('self') is d for frame in frames)
