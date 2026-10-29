import gzip
import json

from wildfire_core.incident_day import EGRESS_CASE_IDS, IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.baselines import DayDispatcher


def test_disk_resume_during_buffer_exit_preserves_the_complete_day(tmp_path):
    inputs, network = fixture_inputs()
    arguments = dict(expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
    environment = IncidentDayEnv(inputs, network, **arguments)
    observation, unused = environment.reset(EGRESS_CASE_IDS[0])
    dispatcher = DayDispatcher()
    active = []
    while not (environment.terminated or environment.truncated):
        action = dispatcher.action(environment, observation)
        if 'advance_minutes' in action:
            action = dict(advance_minutes=1)
        observation, unused_reward, unused_terminated, unused_truncated, unused_info = environment.step(action)
        active = [unit for unit in environment.ops.resources.values()
                  if (unit.get('journey') or {}).get('_trusted_return_egress') is True
                  and unit['journey']['emergency_egress']['exited'] is False]
        if active:
            break
    assert active, 'The fixture must exercise the in-buffer checkpoint boundary'
    checkpoint = tmp_path / 'private' / 'withdrawal.json.gz'
    environment.save_checkpoint(checkpoint)
    restored = IncidentDayEnv(inputs, network, **arguments)
    assert restored.restore(json.loads(gzip.decompress(checkpoint.read_bytes()))) == observation
    while not (environment.terminated or environment.truncated):
        action = dict(advance_minutes=60)
        assert environment.step(action) == restored.step(action)
    assert environment.minute == restored.minute == 1440
    assert environment._audit == restored._audit
    assert environment.trajectory() == restored.trajectory()
    assert environment.outcomes() == restored.outcomes()
    assert environment.checkpoint() == restored.checkpoint()
    assert any(record['emergency_withdrawals'] for record in environment._audit)
    verification = restored.verify_episode()
    assert verification['passed']
    assert verification['exact_replay']
    assert verification['independent_reward_agreement']
