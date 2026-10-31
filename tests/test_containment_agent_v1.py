import json
import hashlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for package in ('wildfire_containment_agent_v1', 'wildfire_containment_v1', 'wildfire_response/core'):
    sys.path.insert(0, str(ROOT / 'envs' / package / 'src'))

from wildfire_containment_agent_v1 import ContainmentAgentTools, VERSION, tools
from wildfire_containment_v1.incident_day import ContainmentIncidentDayEnv
from wildfire_core.incident_day import fixture_inputs


def episode(*, reset=True):
    inputs, network = fixture_inputs()
    env = ContainmentIncidentDayEnv(
        inputs, network, expected_inputs_sha256=inputs['sha256'],
        expected_network_sha256=network['sha256'],
        containment_config=dict(work_gain=2, max_strength=10, decay=0,
                                wind_factor=1, wear_factor=.05, report_delay=2),
        pressure_scale=1)
    if reset:
        env.reset('incident-day-dev-000')
    return env


def wrapped(result):
    observation, reward, terminated, truncated, info = result
    return dict(observation=observation, reward=reward, terminated=terminated,
                truncated=truncated, info=info)


def test_tool_contract_is_small_fresh_and_excludes_trusted_operations():
    definitions = tools()
    assert VERSION == 'wildfire-containment-agent-tools-v1'
    assert {item['function']['name'] for item in definitions} == {
        'get_situation', 'route_options', 'request_resource', 'propose_hold',
        'authorize_hold', 'check_action', 'advance_time', 'get_reports',
        'evacuation_options', 'submit_action', 'request_status'}
    schemas = {item['function']['name']: item['function']['parameters'] for item in definitions}
    checked = schemas['check_action']['properties']['action']
    submitted = schemas['submit_action']['properties']['action']
    assert checked == submitted and checked is not submitted
    assert set(checked['properties']) == {'requests', 'authorizations', 'releases',
                                          'protection_orders', 'status_requests', 'advance_minutes'}
    definitions[0]['function']['parameters']['properties']['tamper'] = {}
    assert 'tamper' not in tools()[0]['function']['parameters']['properties']
    adapter = ContainmentAgentTools(episode())
    for name in ('reset', 'checkpoint', 'restore', 'trajectory', 'private_replay_pins',
                 'replay_transcript', 'verify_episode', 'outcomes'):
        with pytest.raises(ValueError):
            adapter.execute(name, {})
    with pytest.raises(ValueError):
        adapter.execute('get_situation', {'seed': 12})


@pytest.mark.parametrize('tool_name', ['check_action', 'submit_action'])
@pytest.mark.parametrize('kind,destination,approach,valid', [
    ('shelter', None, None, True), ('shelter', 'destination-0', None, False),
    ('shelter', None, 'route-0', False), ('shelter', 'destination-0', 'route-0', False),
    ('evacuate', 'destination-0', 'route-0', True), ('evacuate', None, 'route-0', False),
    ('evacuate', 'destination-0', None, False), ('evacuate', None, None, False),
    ('evacuate', '', 'route-0', False),
])
def test_protection_schema_distinguishes_shelter_and_evacuation(tool_name, kind, destination, approach, valid):
    schema = next(item['function']['parameters'] for item in tools() if item['function']['name'] == tool_name)
    protection = schema['properties']['action']['properties']['protection_orders']['items']
    order = dict(group_id='population-0', action=kind, destination_id=destination, approach_id=approach)

    def branch_matches(branch):
        for key, rule in branch['properties'].items():
            value = order[key]
            if 'const' in rule and value != rule['const']:
                return False
            if rule.get('type') == 'null' and value is not None:
                return False
            if rule.get('type') == 'string' and (type(value) is not str or
                    not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', float('inf'))):
                return False
        return True

    assert sum(branch_matches(branch) for branch in protection['oneOf']) == int(valid)


@pytest.mark.parametrize('kind,destination,approach', [
    ('shelter', 'destination-0', None), ('shelter', None, 'route-0'),
    ('evacuate', None, 'route-0'), ('evacuate', 'destination-0', None),
])
def test_invalid_protection_orders_are_checked_and_committed_by_public_api(kind, destination, approach):
    actual, direct = episode(), episode()
    agent = ContainmentAgentTools(actual)
    action = {'protection_orders': [dict(group_id='population-0', action=kind,
                                         destination_id=destination, approach_id=approach)]}
    checked = agent.execute('check_action', {'action': action})
    assert checked == direct.check_action(action=action) and not checked['valid']
    committed = agent.execute('submit_action', {'action': action})
    assert committed == wrapped(direct.step(action=action))
    assert committed['info']['error'] == checked['error']
    assert actual.trajectory() == direct.trajectory()


def test_public_calls_and_transcript_match_direct_episode():
    actual, direct = episode(), episode()
    agent = ContainmentAgentTools(actual)
    outputs = []

    def compare(name, args, operation, *, transition=False):
        result = agent.execute(name, args)
        expected = operation()
        assert result == (wrapped(expected) if transition else expected)
        assert json.dumps(actual.trajectory(), sort_keys=True) == json.dumps(direct.trajectory(), sort_keys=True)
        outputs.append(result)
        return result

    compare('get_situation', {}, direct.get_situation)
    route = compare('route_options', {'resource_id': 'engine-1'},
                    lambda: direct.route_options(resource_id='engine-1'))['options'][1]
    request = dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])
    action = {'requests': [request]}
    assert compare('check_action', {'action': action}, lambda: direct.check_action(action=action))['valid']
    compare('request_resource', request, lambda: direct.step(action), transition=True)
    for _ in range(60):
        situation = compare('get_situation', {}, direct.get_situation)
        if situation['offers']:
            break
        compare('advance_time', {'max_minutes': 1}, lambda: direct.advance_time(max_minutes=1), transition=True)
    else:
        pytest.fail('Offer was not delivered')
    offer_id = situation['offers'][0]['id']
    proposals = compare('propose_hold', {'offer_id': offer_id}, lambda: direct.propose_hold(offer_id=offer_id))
    proposal_id = proposals[0]['id']
    compare('authorize_hold', {'offer_id': offer_id, 'proposal_id': proposal_id},
            lambda: direct.authorize_hold(offer_id=offer_id, proposal_id=proposal_id), transition=True)
    compare('advance_time', {'max_minutes': 10}, lambda: direct.advance_time(max_minutes=10), transition=True)
    compare('get_reports', {'after_cursor': 0, 'limit': 20},
            lambda: direct.get_reports(after_cursor=0, limit=20))
    compare('request_status', {'center_id': 'center-0'},
            lambda: direct.request_status(center_id='center-0'), transition=True)
    public_text = json.dumps(outputs).lower()
    for private in ('strength', 'checkpoint', 'verification', 'seed', 'future', 'containment_audit'):
        assert private not in public_text
    operations = [entry['operation'] for entry in actual.trajectory()['calls']]
    assert 'step' in operations and 'authorize_hold' in operations and 'propose_hold' in operations
    replay = episode(reset=False)
    replay.replay_transcript(actual.trajectory(), **actual.private_replay_pins())
    assert json.dumps(replay.trajectory(), sort_keys=True) == json.dumps(actual.trajectory(), sort_keys=True)


def test_errors_and_invalid_commit_are_transcribed_by_public_methods():
    actual, direct = episode(), episode()
    agent = ContainmentAgentTools(actual)
    before = actual.trajectory()
    with pytest.raises(ValueError):
        agent.execute('route_options', {'resource_id': 'missing'})
    with pytest.raises(ValueError):
        direct.route_options(resource_id='missing')
    assert actual.trajectory() == direct.trajectory()
    assert len(actual.trajectory()['calls']) == len(before['calls']) + 1
    request = dict(resource_id='engine-1', sector_id='incident', approach_id='invented')
    result = agent.execute('request_resource', request)
    assert result == wrapped(direct.step({'requests': [request]}))
    assert result['info']['error']
    assert actual.trajectory() == direct.trajectory()
    before = actual.trajectory()
    with pytest.raises(ValueError):
        agent.execute('request_resource', {'resource_id': 'engine-1'})
    assert actual.trajectory() == before


def test_population_protection_ordinary_dispatch_release_and_status():
    actual, direct = episode(), episode()
    agent = ContainmentAgentTools(actual)

    def call(name, args, public_call, transition=False):
        result = agent.execute(name, args)
        expected = public_call()
        assert result == (wrapped(expected) if transition else expected)
        assert json.dumps(actual.trajectory(), sort_keys=True) == json.dumps(direct.trajectory(), sort_keys=True)
        return result

    route = call('route_options', {'resource_id': 'crew-1'},
                 lambda: direct.route_options(resource_id='crew-1'))['options'][0]
    evac_args = dict(group_id='population-0', destination_id='destination-0')
    evacuation = call('evacuation_options', evac_args,
                      lambda: direct.evacuation_options(**evac_args))['options'][0]
    action = {
        'requests': [dict(resource_id='crew-1', sector_id='incident', approach_id=route['id'])],
        'protection_orders': [
            dict(group_id='population-0', action='evacuate', destination_id='destination-0', approach_id=evacuation['id']),
            dict(group_id='population-1', action='shelter', destination_id=None, approach_id=None)],
        'status_requests': [dict(center_id='center-0', subject='situation')],
    }
    assert call('check_action', {'action': action}, lambda: direct.check_action(action=action))['valid']
    committed = call('submit_action', {'action': action}, lambda: direct.step(action=action), transition=True)
    assert committed['info']['error'] is None
    for _ in range(60):
        situation = call('get_situation', {}, direct.get_situation)
        offers = [offer for offer in situation['offers'] if offer['status'] == 'offered']
        if offers:
            break
        call('advance_time', {'max_minutes': 1}, lambda: direct.advance_time(max_minutes=1), transition=True)
    else:
        pytest.fail('Offer was not delivered')
    authorization = {'authorizations': [offers[0]['id']]}
    assert call('check_action', {'action': authorization}, lambda: direct.check_action(action=authorization))['valid']
    authorized = call('submit_action', {'action': authorization},
                      lambda: direct.step(action=authorization), transition=True)
    assert authorized['info']['error'] is None
    assert any(offer['status'] == 'authorized' for offer in authorized['observation']['offers'])
    release = {'releases': ['crew-1']}
    assert call('check_action', {'action': release}, lambda: direct.check_action(action=release))['valid']
    assert call('submit_action', {'action': release}, lambda: direct.step(action=release), transition=True)['info']['error'] is None
    for _ in range(60):
        situation = call('get_situation', {}, direct.get_situation)
        if len(situation['protection_orders']) == 2 and situation['status_requests']:
            break
        call('advance_time', {'max_minutes': 1}, lambda: direct.advance_time(max_minutes=1), transition=True)
    else:
        pytest.fail('Protection orders were not publicly delivered')
    assert {order['action'] for order in situation['protection_orders']} == {'shelter', 'evacuate'}
    replay = episode(reset=False)
    replay.replay_transcript(actual.trajectory(), **actual.private_replay_pins())
    assert json.dumps(replay.trajectory(), sort_keys=True) == json.dumps(actual.trajectory(), sort_keys=True)


def test_validation_uses_only_public_calls_and_does_not_leak_private_values():
    class NoAccess:
        def __getattribute__(self, name):
            raise AssertionError('Rejected tool input must not inspect the environment')

    guard = ContainmentAgentTools(NoAccess())
    for name, args in [('checkpoint', {}), ('submit_action', {}),
                       ('evacuation_options', {'group_id': 'x', 'destination_id': 'y', 'seed': 4})]:
        with pytest.raises(ValueError) as error:
            guard.execute(name, args)
        assert str(error.value) in ('A named tool and argument object are required',
                                    'Tool arguments do not match the named contract')

    actual, direct = episode(), episode()
    agent = ContainmentAgentTools(actual)
    invalid = {'protection_orders': [dict(group_id='unknown', action='evacuate',
                                          destination_id='unknown', approach_id='unknown')]}
    result = agent.execute('check_action', {'action': invalid})
    assert result == direct.check_action(action=invalid)
    assert not result['valid']
    assert result['error'] == 'Protection needs a public group/action/destination'
    invalid_wait = {'advance_minutes': 3, 'releases': ['crew-1']}
    checked = agent.execute('check_action', {'action': invalid_wait})
    assert checked == direct.check_action(action=invalid_wait)
    assert not checked['valid']
    committed = agent.execute('submit_action', {'action': invalid_wait})
    assert committed == wrapped(direct.step(action=invalid_wait))
    assert committed['info']['error'] == checked['error']
    assert actual.trajectory() == direct.trajectory()
    forbidden = agent.execute('check_action', {'action': {'checkpoint': {}}})
    assert forbidden == {'valid': False, 'error': 'Unknown incident-day action fields'}
    for name, args in [('evacuation_options', {'group_id': 'unknown', 'destination_id': 'unknown'}),
                       ('propose_hold', {'offer_id': 'unknown'}),
                       ('authorize_hold', {'offer_id': 'unknown', 'proposal_id': 'unknown'})]:
        with pytest.raises(ValueError) as error:
            agent.execute(name, args)
        assert str(error.value) in ('Choose a reported group and destination',
                                    'Choose a live delivered offer',
                                    'Choose a line proposed for this offer')
    for private in ('seed', 'strength', 'checkpoint', 'verification', 'future'):
        assert private not in json.dumps(result).lower()


def test_host_log_captures_rejections_and_replays_in_order():
    env = episode()
    agent = ContainmentAgentTools(env)
    situation = agent.execute('get_situation', {})
    before = env.trajectory()
    with pytest.raises(ValueError):
        agent.execute('unknown_tool', {})
    with pytest.raises(ValueError):
        agent.execute('request_status', {'subject': 'situation'})
    with pytest.raises(ValueError):
        agent.execute('get_situation', {'bad': float('nan')})
    with pytest.raises(ValueError):
        agent.execute('get_situation', {'bad': 'x' * 70000})
    assert env.trajectory() == before
    with pytest.raises(ValueError):
        agent.execute('evacuation_options', {'group_id': 'missing', 'destination_id': 'missing'})
    status = agent.execute('request_status', {'center_id': 'center-0', 'subject': 'resources'})
    assert status['info']['error'] is None
    log = agent.host_log()
    digest = agent.host_log_sha256()
    assert len(log['calls']) == 7 and not log['closed']
    assert log['calls'][0]['result'] == situation
    assert [item.get('name') for item in log['calls']] == [
        'get_situation', 'unknown_tool', 'request_status', None, None, 'evacuation_options', 'request_status']
    assert all('error' in item for item in log['calls'][1:6])
    assert all(item['input_redacted'] for item in log['calls'][3:5])
    assert 'x' * 100 not in json.dumps(log)
    for private in ('strength', 'checkpoint', 'verification', 'seed', 'future'):
        assert private not in json.dumps(log).lower()
    log_copy = agent.host_log()
    log_copy['calls'].clear()
    assert len(agent.host_log()['calls']) == 7

    replay_env = episode()
    replay = ContainmentAgentTools(replay_env)
    replay.replay_host_log(log, expected_sha256=digest)
    assert replay.host_log() == log
    assert replay_env.trajectory() == env.trajectory()
    altered = agent.host_log()
    altered['calls'][1]['name'] = 'get_situation'
    fresh = ContainmentAgentTools(episode())
    with pytest.raises(ValueError, match='digest'):
        fresh.replay_host_log(altered, expected_sha256=digest)
    assert fresh.host_log()['calls'] == []
    altered = agent.host_log()
    altered['calls'][0]['result']['minute'] = 999
    encoded = json.dumps(altered, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    forged_digest = hashlib.sha256(encoded.encode()).hexdigest()
    mismatch = ContainmentAgentTools(episode())
    with pytest.raises(ValueError, match='mismatch'):
        mismatch.replay_host_log(altered, expected_sha256=forged_digest)
    with pytest.raises(RuntimeError, match='invalid'):
        mismatch.host_log()


def test_host_log_capacity_seals_and_replays_without_episode_mutation(monkeypatch):
    import importlib
    module = importlib.import_module('wildfire_containment_agent_v1.tools')
    monkeypatch.setattr(module, 'MAX_CALLS', 2)
    env = episode()
    agent = ContainmentAgentTools(env)
    agent.execute('get_situation', {})
    with pytest.raises(ValueError):
        agent.execute('unknown', {})
    before = env.trajectory()
    with pytest.raises(RuntimeError, match='capacity'):
        agent.execute('advance_time', {'max_minutes': 1})
    assert env.trajectory() == before
    log = agent.host_log()
    assert log['closed'] and len(log['calls']) == 3
    assert log['calls'][-1]['error']['message'] == 'Tool call log capacity exhausted'
    with pytest.raises(RuntimeError, match='closed'):
        agent.execute('get_situation', {})
    replay_env = episode()
    replay = ContainmentAgentTools(replay_env)
    replay.replay_host_log(log, expected_sha256=agent.host_log_sha256())
    assert replay.host_log() == log and replay_env.trajectory() == before


def test_oversized_result_invalidates_log_without_retaining_result(monkeypatch):
    import importlib
    module = importlib.import_module('wildfire_containment_agent_v1.tools')
    monkeypatch.setattr(module, 'MAX_LOG_BYTES', 500)
    monkeypatch.setattr(module, '_RESERVE_BYTES', 100)

    class LargePublicResult:
        def __getattr__(self, name):
            return lambda **kwargs: {'public': 'x' * 1000}

    agent = ContainmentAgentTools(LargePublicResult())
    with pytest.raises(RuntimeError, match='invalid'):
        agent.execute('get_situation', {})
    with pytest.raises(RuntimeError, match='invalid'):
        agent.host_log()
    assert agent._calls == []


@pytest.mark.parametrize('after_append', [False, True])
@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('successful_call', [False, True])
def test_interruption_while_recording_success_or_error_invalidates_host_log(
        monkeypatch, after_append, interruption, successful_call):
    env = episode()
    agent = ContainmentAgentTools(env)
    empty_log = agent.host_log()
    empty_digest = agent.host_log_sha256()
    original_append = agent._append

    def interrupted_append(entry):
        if after_append:
            original_append(entry)
        raise interruption()

    monkeypatch.setattr(agent, '_append', interrupted_append)
    with pytest.raises(interruption):
        if successful_call:
            agent.execute('advance_time', {'max_minutes': 1})
        else:
            agent.execute('route_options', {'resource_id': 'missing'})
    transcript = env.trajectory()
    assert len(transcript['calls']) == 1
    assert transcript['calls'][0]['after_minute'] == (1 if successful_call else 0)
    with pytest.raises(RuntimeError, match='invalid'):
        agent.host_log()
    with pytest.raises(RuntimeError, match='invalid'):
        agent.host_log_sha256()
    with pytest.raises(RuntimeError, match='invalid'):
        agent.execute('get_situation', {})
    with pytest.raises(ValueError, match='fresh'):
        agent.replay_host_log(empty_log, expected_sha256=empty_digest)


def test_interruption_while_recording_capacity_error_invalidates_host_log(monkeypatch):
    import importlib
    module = importlib.import_module('wildfire_containment_agent_v1.tools')
    monkeypatch.setattr(module, 'MAX_CALLS', 0)
    env = episode()
    agent = ContainmentAgentTools(env)

    def interrupted_append(entry):
        raise KeyboardInterrupt()

    monkeypatch.setattr(agent, '_append', interrupted_append)
    with pytest.raises(KeyboardInterrupt):
        agent.execute('advance_time', {'max_minutes': 1})
    assert env.trajectory()['calls'] == []
    with pytest.raises(RuntimeError, match='invalid'):
        agent.host_log()


@pytest.mark.parametrize('outcome', ['success', 'error', 'capacity'])
def test_trace_interrupt_after_recording_before_return_or_raise_invalidates_log(monkeypatch, outcome):
    import importlib
    module = importlib.import_module('wildfire_containment_agent_v1.tools')
    if outcome == 'capacity':
        monkeypatch.setattr(module, 'MAX_CALLS', 0)
    env = episode()
    agent = ContainmentAgentTools(env)
    source, first_line = inspect.getsourcelines(ContainmentAgentTools.execute)
    if outcome == 'success':
        target = next(i for i, line in enumerate(source) if line.strip() == 'return result')
    elif outcome == 'capacity':
        target = next(i for i, line in enumerate(source) if line.strip() == 'raise recorded_error')
    else:
        target = next(i + 1 for i, line in enumerate(source) if line.strip() == 'recorded_error = error')
        assert source[target].strip() == 'raise'
    triggered = []

    def trace(frame, event, arg):
        if (frame.f_code is ContainmentAgentTools.execute.__code__ and event == 'line'
                and frame.f_lineno == first_line + target):
            triggered.append(True)
            raise KeyboardInterrupt()
        return trace

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            if outcome == 'error':
                agent.execute('route_options', {'resource_id': 'missing'})
            else:
                agent.execute('advance_time', {'max_minutes': 1})
    finally:
        sys.settrace(previous_trace)
    assert triggered and len(agent._calls) == 1
    assert len(env.trajectory()['calls']) == (0 if outcome == 'capacity' else 1)
    with pytest.raises(RuntimeError, match='invalid'):
        agent.host_log()


def test_trace_return_event_interruption_is_a_host_recovery_boundary():
    env = episode()
    agent = ContainmentAgentTools(env)
    triggered = []

    def trace(frame, event, arg):
        if frame.f_code is ContainmentAgentTools.execute.__code__ and event == 'return':
            triggered.append(True)
            raise KeyboardInterrupt()
        return trace

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            agent.execute('advance_time', {'max_minutes': 1})
    finally:
        sys.settrace(previous_trace)
    assert triggered and env.trajectory()['calls'][0]['after_minute'] == 1
    # The return event occurs outside the method's exception handler. The host
    # must discard this apparently valid log after observing an interruption.
    assert len(agent.host_log()['calls']) == 1
