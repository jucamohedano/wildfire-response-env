import json
from pathlib import Path

import pytest

from eval import replay_qwen3_day as replay
from eval.qwen3_protocol import parse_call
from wildfire_core.incident_day import IncidentDayEnv, IncidentDayTools, fixture_inputs
from wildfire_core.catalog_lifecycle import ACTIVE_FIXTURE_CASE_IDS

CASE = ACTIVE_FIXTURE_CASE_IDS[0]


def raw_call(name, arguments):
    return '<tool_call>' + json.dumps(dict(name=name, arguments=arguments)) + '</tool_call>'


@pytest.fixture
def captured(tmp_path, monkeypatch):
    inputs, network = fixture_inputs()

    def new_environment(*args, **kwargs):
        return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                              expected_network_sha256=network['sha256']), {'scenario_ids': [CASE]}

    monkeypatch.setattr(replay, 'make_environment', new_environment)
    env, _ = new_environment()
    env.reset(CASE)
    bridge = IncidentDayTools(env)
    records = []
    case_dir = tmp_path / CASE
    case_dir.mkdir()

    def add(name=None, arguments=None, *, raw=None, executed=True, include_malformed_result=False):
        response = raw if raw is not None else raw_call(name, arguments)
        rec = dict(minute=env.minute, response=response)
        if not executed:
            rec.update(executed=False, harness_stop='generation_deadline')
        else:
            try:
                call = parse_call(response)
            except ValueError as error:
                rec['error'] = str(error)
                observation, reward, terminated, truncated, info = env.step({'malformed_model_response': True})
                if include_malformed_result:
                    rec['result'] = dict(error=str(error), observation=observation, reward=reward,
                                         terminated=terminated, truncated=truncated, info=info)
            else:
                rec.update(tool=call['name'], arguments=call['arguments'],
                           result=bridge.execute(call['name'], call['arguments']))
        records.append(rec)

    def save():
        (case_dir / 'requests.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
        (case_dir / 'trajectory.json').write_text(json.dumps(env.trajectory()))

    return case_dir, env, records, add, save


def test_partial_prefix_preserves_queries_and_never_passively_finishes(captured):
    case_dir, env, _, add, save = captured
    add('get_situation', {})
    add('route_options', {'resource_id': 'engine-0'})
    assert env.minute == 0
    add('advance_time', {'max_minutes': 1})
    save()
    result = replay.replay_case('/unused', case_dir, CASE)
    assert result['passed'] and result['minute'] == 1
    assert result['results_compared'] == 3
    assert result['diagnostics']['public_route_queries'] == 1
    assert sum(result['diagnostics']['population_by_status'].values()) == result['diagnostics']['population_count']
    assert sum(result['diagnostics']['resource_by_kind_and_state'].values()) == result['diagnostics']['resource_count']
    assert result['diagnostics']['suppression_work_events'] == sum(len(record.get('applied_work', [])) for record in env._audit)
    assert result['score'] is None and not result['score_eligible']
    public = json.dumps(result)
    for field in ('initial_observation', 'private_audit', 'checkpoint', 'graded_score', 'transitions'):
        assert field not in public


@pytest.mark.parametrize('field', ['arguments', 'result', 'trajectory', 'minute'])
def test_tampering_cannot_establish_prefix_consistency(captured, field):
    case_dir, _, records, add, save = captured
    add('advance_time', {'max_minutes': 1})
    if field == 'arguments':
        records[0]['arguments'] = {'max_minutes': 2}
    elif field == 'result':
        records[0]['result']['reward'] = 123
    elif field == 'minute':
        records[0]['minute'] = 9
    save()
    if field == 'trajectory':
        path = case_dir / 'trajectory.json'
        value = json.loads(path.read_text())
        value['queries'] = [{'invented': True}]
        path.write_text(json.dumps(value))
    with pytest.raises(replay.ReplayError):
        replay.replay_case('/unused', case_dir, CASE)


def test_malformed_raw_response_replays_the_actual_penalty(captured):
    case_dir, _, _, add, save = captured
    add(raw='malformed response', include_malformed_result=True)
    save()
    result = replay.replay_case('/unused', case_dir, CASE)
    assert result['passed'] and result['minute'] == 1
    assert result['malformed_penalties'] == 1 and result['score'] is None


def test_expired_valid_or_malformed_responses_never_change_world(captured):
    case_dir, _, _, add, save = captured
    add('advance_time', {'max_minutes': 60}, executed=False)
    add(raw='invalid and expired', executed=False)
    save()
    result = replay.replay_case('/unused', case_dir, CASE)
    assert result['passed'] and result['minute'] == 0
    assert result['expired_unexecuted'] == 2 and result['malformed_penalties'] == 0
    assert result['score'] is None


def test_valid_response_cannot_be_relabelled_as_malformed(captured):
    case_dir, _, records, add, save = captured
    add('get_situation', {})
    records[0].pop('tool')
    records[0].pop('arguments')
    records[0]['error'] = 'claimed parser failure'
    save()
    with pytest.raises(replay.ReplayError, match='raw response'):
        replay.replay_case('/unused', case_dir, CASE)


def test_missing_final_trace_is_not_a_passing_replay(captured):
    case_dir, _, _, add, save = captured
    add('get_reports', {})
    save()
    (case_dir / 'trajectory.json').unlink()
    result = replay.replay_case('/unused', case_dir, CASE)
    assert not result['passed'] and result['trajectory_match'] is None
    assert result['reason'] == 'final_trajectory_missing' and result['score'] is None


def test_terminal_invalid_abandonment_replays_but_never_earns_score(captured):
    case_dir, env, _, add, save = captured
    for _ in range(3):
        add(raw='not a tool call')
    assert env.truncated and env.minute == 3
    save()
    result = replay.replay_case('/unused', case_dir, CASE)
    assert result['passed'] and result['verification']['passed']
    assert result['truncated'] and not result['score_eligible'] and result['score'] is None
