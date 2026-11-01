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
