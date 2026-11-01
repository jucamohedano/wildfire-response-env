"""Short checkpoint continuations at operational boundaries, without a full day."""
import pytest

from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import make_policy
from eval import validate_source_runtime_matrix as matrix
from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


def new_environment():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    return env


def tick_until(env, predicate, limit=100):
    for _ in range(limit):
        if predicate():
            return
        _, _, terminated, truncated, _ = env.advance_time(1)
        assert not terminated and not truncated
    pytest.fail('Expected boundary was not reached')


def exercise_boundary(env, observer, tmp_path, crossing):
    """Save via matrix's disk format, then compare each side of a continuation."""
    policy = make_policy('passive')
    policy.dispatcher.asked.add(('center-0', 0))  # Ensure nonempty policy state is restored.
    identity = dict(runtime_id='fixture', case_id=env._scenario['id'], policy=policy.name)
    expected = [identity]
    validator = matrix.validator_digest()
    report = dict(expected_cells=expected, cells=[dict(identity, status='incomplete')],
                  validator_sha256=validator, implementation_sha256=implementation_digest())
    (tmp_path / 'private').mkdir()
    matrix._checkpoint(tmp_path, report, 0, env, policy, [], set(), [], {'passed': True}, observer)
    _, saved = matrix.load_progress(tmp_path, expected, validator)

    resumed = new_environment()
    observation = resumed.restore(saved['environment'])
    restored_policy = make_policy(policy.name)
    restored_policy.restore(saved['policy'])
    restored_observer = MovementObserver(resumed)
    restored_observer.restore(saved['movement_observer'])
    assert observation == env.get_situation()
    assert restored_observer.snapshot() == observer.snapshot()
    assert restored_policy.checkpoint() == policy.checkpoint()

    public = matrix.PublicFacade(env, [])
    resumed_public = matrix.PublicFacade(resumed, [])
    for _ in range(100):
        # One-minute waits exercise both sides of the exact transition. The final
        # policy step below also checks the restored policy through named tools.
        if crossing(env):
            break
        action = {'advance_minutes': 1}
        assert matrix.commit(public, action) == matrix.commit(resumed_public, action)
        assert env.checkpoint() == resumed.checkpoint()
        assert observer.snapshot() == restored_observer.snapshot()
    else:
        pytest.fail('Continuation did not cross the expected boundary')
    assert crossing(resumed)
    action = policy.action(public, env.get_situation())
    restored_action = restored_policy.action(resumed_public, resumed.get_situation())
    assert action == restored_action == {'advance_minutes': 60}
    assert matrix.commit(public, action) == matrix.commit(resumed_public, restored_action)
    assert env.checkpoint() == resumed.checkpoint()
    assert policy.checkpoint() == restored_policy.checkpoint()
    assert observer.snapshot() == restored_observer.snapshot()


def test_recovery_during_resource_travel(tmp_path):
    env = new_environment()
    observer = MovementObserver(env)
    # The fixture starts engine-0 on scene. Release it through the public API
    # and checkpoint while its return journey is still in progress.
    env.step({'releases': ['engine-0']})
    tick_until(env, lambda: env.ops.resources['engine-0']['state'] == 'returning')
    assert env.ops.resources['engine-0']['journey']['arrives_at'] > env.minute
    exercise_boundary(env, observer, tmp_path,
                      lambda e: e.ops.resources['engine-0']['state'] != 'returning')


def test_recovery_before_message_delivery(tmp_path):
    env = new_environment()
    observer = MovementObserver(env)
    env.request_status('center-0', 'resources')
    def pending_response():
        return any(item['stage'] == 'response' and item['due'] > env.minute for item in env.bus._queue)
    tick_until(env, pending_response)
    queued = next(item for item in env.bus._queue if item['stage'] == 'response' and item['due'] > env.minute)
    request_id = queued['request_id']
    assert env.bus.requests[request_id]['status'] == 'pending'
    exercise_boundary(env, observer, tmp_path,
                      lambda e: e.bus.requests[request_id]['status'] == 'answered')


def test_recovery_during_refill(tmp_path):
    env = new_environment()
    observer = MovementObserver(env)
    env.step({'releases': ['engine-0']})
    tick_until(env, lambda: env.ops.resources['engine-0']['state'] == 'refilling')
    unit = env.ops.resources['engine-0']
    assert unit['due_at'] > env.minute and unit['water_litres'] < unit['capacity_litres']
    exercise_boundary(env, observer, tmp_path,
                      lambda e: e.ops.resources['engine-0']['state'] != 'refilling')
    assert env.ops.resources['engine-0']['water_litres'] == unit['capacity_litres']


def test_recovery_at_long_wait_wake_boundary(tmp_path):
    env = new_environment()
    observer = MovementObserver(env)
    # Public waits are atomic: save after an early wake from a 60-minute request,
    # then compare a fresh 60-minute policy wait after restore.
    env.request_status('center-0', 'resources')
    start = env.minute
    _, _, terminated, truncated, info = env.advance_time(60)
    assert not terminated and not truncated
    assert 1 <= env.minute - start < 60, info
    exercise_boundary(env, observer, tmp_path, lambda e: e.minute > start + 1)


def test_process_killed_inside_wait_replays_from_atomic_checkpoint(tmp_path):
    """A killed wait is discarded; its entire pending action is replayed."""
    import os
    import select
    import subprocess
    import sys
    import textwrap

    env = new_environment()
    observer = MovementObserver(env)
    # Move past the initial very short wakes. The next wait reaches at least
    # three ticks before its next actionable delivery in this pinned fixture.
    for _ in range(3):
        _, _, terminated, truncated, _ = env.advance_time(60)
        assert not terminated and not truncated
    policy = make_policy('passive')
    policy.dispatcher.asked.add(('center-0', 0))
    identity = dict(runtime_id='fixture', case_id=env._scenario['id'], policy=policy.name)
    expected = [identity]
    validator = matrix.validator_digest()
    report = dict(expected_cells=expected, cells=[dict(identity, status='incomplete')],
                  validator_sha256=validator, implementation_sha256=implementation_digest())
    (tmp_path / 'private').mkdir()
    matrix._checkpoint(tmp_path, report, 0, env, policy, [], set(), [], {'passed': True}, observer)

    child_code = textwrap.dedent('''
        import gzip, json, os, sys
        from eval.incident_day_movement_checks import MovementObserver
        from eval.incident_day_policies import make_policy
        from eval.validate_source_runtime_matrix import PublicFacade, commit
        from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

        with gzip.open(sys.argv[1], 'rt') as stream:
            saved = json.load(stream)
        inputs, network = fixture_inputs()
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
        observation = env.restore(saved['environment'])
        policy = make_policy(saved['policy']['policy'])
        policy.restore(saved['policy'])
        observer = MovementObserver(env)
        observer.restore(saved['movement_observer'])
        original_tick = env.ops.tick
        ticks = [0]
        def interruptible_tick(*args, **kwargs):
            result = original_tick(*args, **kwargs)
            ticks[0] += 1
            if ticks[0] == 3:
                os.write(1, b'INSIDE_WAIT_AFTER_THREE_TICKS\\n')
                os.read(0, 1)  # Parent kills this process while the call is in flight.
            return result
        env.ops.tick = interruptible_tick
        public = PublicFacade(env, [])
        action = policy.action(public, observation)
        assert action == {'advance_minutes': 60}
        commit(public, action)
        raise AssertionError('Wait ended before the interruption point')
    ''')
    child_env = os.environ.copy()
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    process = subprocess.Popen(
        [sys.executable, '-u', '-c', child_code, str(tmp_path / 'private/checkpoint.json.gz')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready, 'Child did not reach the in-wait interruption point'
        marker = process.stdout.readline()
        if marker != b'INSIDE_WAIT_AFTER_THREE_TICKS\n':
            process.wait(timeout=5)
            pytest.fail(f'Child exited before the interruption point: {process.stderr.read().decode()}')
        process.kill()
        process.wait(timeout=5)
        assert process.returncode < 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()

    # The on-disk pre-action state is still intact and determines the policy's
    # pending action. Compare its replay and subsequent decisions with the
    # original uninterrupted branch, including full checkpoints and observer.
    _, saved = matrix.load_progress(tmp_path, expected, validator)
    resumed = new_environment()
    resumed.restore(saved['environment'])
    restored_policy = make_policy(policy.name)
    restored_policy.restore(saved['policy'])
    restored_observer = MovementObserver(resumed)
    restored_observer.restore(saved['movement_observer'])
    public, resumed_public = matrix.PublicFacade(env, []), matrix.PublicFacade(resumed, [])
    for step in range(4):
        action = policy.action(public, env.get_situation())
        replay = restored_policy.action(resumed_public, resumed.get_situation())
        assert action == replay == {'advance_minutes': 60}
        before = env.minute
        assert matrix.commit(public, action) == matrix.commit(resumed_public, replay)
        if step == 0:
            assert env.minute - before >= 3  # The killed process had entered this same action.
        assert env.checkpoint() == resumed.checkpoint()
        assert policy.checkpoint() == restored_policy.checkpoint()
        assert observer.snapshot() == restored_observer.snapshot()
