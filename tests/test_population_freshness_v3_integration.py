"""Public-interface regression tests for the separately versioned population engine.

The cases are the repository's synthetic exercises; no evaluator state is used
as an oracle for what a controller knows. A report is evidence only after receipt.
"""
import copy
import json
from pathlib import Path

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs, EGRESS_CASE_IDS, COVERAGE_VARIANT_IDS
from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
from envs.wildfire_response.incident_day.environment import implementation_digest


@pytest.fixture(scope='module')
def v3_class():
    return PopulationEngineV3


def create(cls):
    inputs, network = fixture_inputs()
    return cls(inputs, network, expected_inputs_sha256=inputs['sha256'],
               expected_network_sha256=network['sha256'])


def group(obs, identifier='population-0'):
    return next(item for item in obs['districts'] if item['id'] == identifier)


def destination(obs, identifier):
    return next(item for item in obs['destinations'] if item['id'] == identifier)


def reports(env):
    result, cursor = [], 0
    while True:
        page = env.get_reports(after_cursor=cursor, limit=20)
        result.extend(page)
        cursor += len(page)
        if len(page) < 20:
            return result


def move(env, group_id, destination_id):
    result = env.evacuation_options(group_id, destination_id)
    assert result['status'] == 'reachable' and result['options']
    option = result['options'][0]
    action = {'protection_orders': [dict(group_id=group_id, action='evacuate',
        destination_id=destination_id, approach_id=option['id'])]}
    assert env.check_action(action)['valid']
    env.step(action)
    return option


def advance_until(env, predicate, limit=600):
    for _ in range(limit):
        obs = env.get_situation()
        found = predicate(obs, reports(env))
        if found:
            return obs, found
        _, _, terminal, truncated, _ = env.step({'advance_minutes': 1})
        assert not (terminal or truncated), 'fixture ended before the expected delivered event'
    pytest.fail('fixture did not produce the expected delivered event')


def test_arrival_delivery_changes_public_position_and_route_origin(v3_class):
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    old_position = group(initial)['position']
    target = destination(initial, 'destination-0')['position']
    option = move(env, 'population-0', 'destination-0')
    assert option['journey']['origin'] == old_position
    # Minute 25 is before the arrival is observed (26) in this fixed exercise.
    while env.get_situation()['minute'] < 25:
        obs, _, terminal, truncated, _ = env.step({'advance_minutes': 1})
        assert not (terminal or truncated)
    # An acknowledged movement may make the last known origin unusable.
    assert group(obs)['position'] != target
    assert group(obs)['status'] != 'arrived'
    assert not any(r['kind'] == 'evacuation_arrived' and r['payload'].get('group_id') == 'population-0'
                   for r in reports(env))
    obs, arrival = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'evacuation_arrived' and r['payload'].get('group_id') == 'population-0'), None))
    assert arrival['observed_at'] < arrival['received_at'] <= obs['minute']
    assert arrival['payload']['order']['destination_id'] == 'destination-0'
    assert group(obs)['status'] == 'arrived'
    assert group(obs)['position'] == target
    result = env.evacuation_options('population-0', 'destination-1')
    assert result['status'] == 'reachable' and result['options']
    assert all(route['journey']['origin'] == target for route in result['options'])


def test_report_snapshots_use_event_time_and_never_future_information(v3_class):
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    move(env, 'population-0', 'destination-0')
    obs, arrival = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'evacuation_arrived' and r['payload'].get('group_id') == 'population-0'), None))
    saved = copy.deepcopy(arrival)
    obs, update = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'resident_update' and r['payload'].get('group', {}).get('id') == 'population-0'
        and r['observed_at'] > arrival['observed_at']), None))
    assert update['observed_at'] <= update['reported_at'] <= update['received_at'] <= obs['minute']
    assert group(obs)['position'] == update['payload']['group']['position']
    assert group(obs)['status'] == update['payload']['group']['status']
    assert next(r for r in reports(env) if r['id'] == saved['id']) == saved
    assert all(r['received_at'] <= obs['minute'] for r in reports(env))
    assert '_events' not in json.dumps(obs) and 'future_events' not in json.dumps(obs)
    assert initial['minute'] == 0


def test_relocation_uses_delivered_group_position_and_options(v3_class):
    env = create(v3_class)
    obs, _ = env.reset(COVERAGE_VARIANT_IDS[1])
    # This case starts population-1 at destination-1; its closure is a later event.
    obs, report = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'relocation_required' and r['payload'].get('group', {}).get('id') == 'population-1'), None))
    snapshot = report['payload']['group']
    assert report['received_at'] <= obs['minute']
    assert group(obs, 'population-1')['status'] == snapshot['status'] == 'blocked'
    assert group(obs, 'population-1')['position'] == snapshot['position']
    option = move(env, 'population-1', 'destination-0')
    assert option['journey']['origin'] == snapshot['position']
    obs, arrived = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'evacuation_arrived' and r['payload'].get('group_id') == 'population-1'
        and r['observed_at'] > report['observed_at']), None))
    assert arrived['payload']['order']['destination_id'] == 'destination-0'
    assert group(obs, 'population-1')['position'] == destination(obs, 'destination-0')['position']


def test_invalid_approach_and_query_do_not_create_orders(v3_class):
    env = create(v3_class)
    obs, _ = env.reset(EGRESS_CASE_IDS[0])
    unit = obs['resources'][0]['id']
    assert 'status' in env.route_options(unit)
    with pytest.raises(ValueError):
        env.evacuation_options('not-a-group', 'destination-0')
    option = env.evacuation_options('population-0', 'destination-0')['options'][0]
    action = {'protection_orders': [dict(group_id='population-0', action='evacuate',
        destination_id='destination-1', approach_id=option['id'])]}
    assert not env.check_action(action)['valid']
    before = env.get_situation()
    after, _, terminated, truncated, info = env.step(action)
    assert after['minute'] == before['minute'] + 1
    assert not terminated and not truncated
    assert after['protection_orders'] == before['protection_orders']
    assert info


def test_v3_checkpoint_continuation_and_independent_replay(v3_class):
    first = create(v3_class)
    first.reset(EGRESS_CASE_IDS[0])
    move(first, 'population-0', 'destination-0')
    advance_until(first, lambda o, rs: next((r for r in rs if r['kind'] == 'evacuation_arrived'
        and r['payload'].get('group_id') == 'population-0'), None))
    snapshot = json.loads(json.dumps(first.checkpoint()))
    restored = create(v3_class)
    assert restored.restore(snapshot) == first.get_situation()
    while True:
        action = {'advance_minutes': 60}
        expected = first.step(action)
        assert restored.step(action) == expected
        if expected[2] or expected[3]:
            break
    assert expected[2] and not expected[3]
    assert restored.outcomes() == first.outcomes()
    verification = restored.verify_episode()
    assert verification['passed'] and verification['exact_replay'] and verification['independent_reward_agreement']


def test_v2_release_pin_and_replay_remain_valid():
    release = json.loads((Path(__file__).resolve().parents[1] / 'data/hub_runtime_v1/release.json').read_text())
    assert implementation_digest() == release['engine_implementation_sha256']
    env = create(IncidentDayEnv)
    env.reset(EGRESS_CASE_IDS[0])
    for _ in range(3):
        result = env.step({'bad': 1})
    assert result[3] and result[0]['minute'] == 3
    verification = env.verify_episode()
    assert verification['passed'] and verification['exact_replay'] and verification['independent_reward_agreement']


def delivered_record(snapshot, observed_at, received_at, identifier):
    """Construct an exercise report delivered through the engine's receive seam."""
    return dict(id=identifier, kind='resident_update', source=snapshot['id'], role='resident',
                observed_at=observed_at, reported_at=observed_at, received_at=received_at,
                payload={'group': copy.deepcopy(snapshot)})


def delivery_fixture(v3_class):
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    while env.get_situation()['minute'] < 20:
        _, _, terminal, truncated, _ = env.step({'advance_minutes': 1})
        assert not (terminal or truncated)
    return env, initial


def test_delivered_duplicate_and_late_old_snapshot_do_not_undo_newer_position(v3_class):
    env, initial = delivery_fixture(v3_class)
    prior = copy.deepcopy(group(initial))
    newer = dict(prior, status='blocked', position=destination(initial, 'destination-0')['position'],
                 destination_id='destination-0')
    fresh = delivered_record(newer, 15, 17, 'synthetic-new')
    old = delivered_record(dict(prior, status='unwarned'), 10, 20, 'synthetic-old')
    env._receive([fresh])
    first = group(env.get_situation())
    assert (first['status'], first['position'], first['observed_at']) == ('blocked', newer['position'], 15)
    route = env.evacuation_options('population-0', 'destination-1')
    assert route['options'] and all(o['journey']['origin'] == newer['position'] for o in route['options'])
    env._receive([copy.deepcopy(fresh), old, copy.deepcopy(old)])
    assert group(env.get_situation()) == first
    assert env.evacuation_options('population-0', 'destination-1') == route


@pytest.mark.parametrize('reverse', [False, True])
def test_conflicting_equal_time_reports_are_order_independent_and_block_unsafe_routing(v3_class, reverse):
    env, initial = delivery_fixture(v3_class)
    base = copy.deepcopy(group(initial))
    first = dict(base, status='arrived', position=destination(initial, 'destination-0')['position'],
                 destination_id='destination-0')
    second = dict(base, status='blocked', position=destination(initial, 'destination-1')['position'],
                  destination_id='destination-1')
    pair = [delivered_record(first, 15, 18, 'synthetic-a'),
            delivered_record(second, 15, 19, 'synthetic-b')]
    if reverse:
        pair.reverse()
    env._receive(pair)
    observed = group(env.get_situation())
    assert observed['observed_at'] == 15
    assert observed['status'] is None and observed['position'] is None
    assert observed['destination_id'] is None
    result = env.evacuation_options('population-0', 'destination-0')
    assert result['status'] == 'position_unknown' and result['options'] == []
    env._receive([copy.deepcopy(pair[0])])
    assert group(env.get_situation()) == observed


def test_unable_to_comply_delivery_restores_reported_position_for_active_evacuation(v3_class):
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    move(env, 'population-0', 'destination-0')
    obs, notice = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] in ('protection_accepted', 'order_delivered', 'order_acknowledged')
        and r['payload'].get('group_id') == 'population-0'
        and r['payload'].get('order', {}).get('action') == 'evacuate'), None))
    order_id = notice['payload']['order_id']
    assert group(obs)['position'] is None
    result = env.evacuation_options('population-0', 'destination-1')
    assert result['status'] == 'position_unknown' and result['options'] == []

    event_minute = obs['minute']
    position = group(initial)['position']
    snapshot = dict(id='population-0', status='unable_to_comply', position=position,
                    destination_id='destination-0')
    order = dict(id=order_id, group_id='population-0', action='evacuate',
                 destination_id='destination-0', state='unable_to_comply', completed_at=event_minute)
    event_id = env.bus.emit('operations', 'unable_to_comply',
        dict(group_id='population-0', order_id=order_id, position=position,
             group=snapshot, order=order), event_minute, delay=2)
    before = env.get_situation()
    assert event_id not in {r['id'] for r in reports(env)}
    assert group(before)['position'] is None
    env.step({'advance_minutes': 1})
    assert event_id not in {r['id'] for r in reports(env)}
    assert group(env.get_situation())['position'] is None
    obs, delivered = advance_until(env, lambda o, rs: next((r for r in rs if r['id'] == event_id), None))
    assert delivered['observed_at'] == event_minute < delivered['received_at']
    assert delivered['payload']['group']['position'] == position
    assert group(obs)['status'] == 'unable_to_comply'
    assert group(obs)['position'] == position
    options = env.evacuation_options('population-0', 'destination-1')
    assert options['options'] and all(item['journey']['origin'] == position for item in options['options'])


def test_resident_report_redacts_pending_order_but_keeps_reported_evidence(v3_class):
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    position = destination(initial, 'destination-0')['position']
    secret = 'synthetic-pending-order-never-public'
    snapshot = dict(id='population-0', status='blocked', position=position, exposed=True,
                    pending_order_id=secret)
    env.step({'advance_minutes': 1})
    event_minute = env.get_situation()['minute']
    event_id = env.bus.emit('population-0', 'resident_update', {'group': snapshot},
                            event_minute, role='resident', channel='phone', delay=4)
    for _ in range(3):
        obs = env.get_situation()
        assert event_id not in {r['id'] for r in reports(env)}
        assert secret not in json.dumps(obs) and secret not in json.dumps(reports(env))
        assert group(obs)['position'] != position
        env.step({'advance_minutes': 1})
    obs, delivered = advance_until(env, lambda o, rs: next((r for r in rs if r['id'] == event_id), None))
    assert delivered['observed_at'] == event_minute < delivered['received_at']
    assert secret not in json.dumps(delivered) and secret not in json.dumps(obs)
    assert 'pending_order_id' not in delivered['payload']['group']
    public = delivered['payload']['group']
    assert (public['status'], public['position'], public['exposed']) == ('blocked', position, True)
    current = group(obs)
    assert (current['status'], current['position'], current['exposed']) == ('blocked', position, True)
    assert 'pending_order_id' not in current


def resumed_before_block_report(v3_class, monkeypatch, *, resume_delay):
    """Drive actual movement through one synthetic obstruction and delayed radio."""
    env = create(v3_class)
    initial, _ = env.reset(EGRESS_CASE_IDS[0])
    movement = env._movement_safe
    obstruction = {'used': False}
    emitted = []

    def one_tick_obstruction(journey, minute, kind):
        if kind == 'group' and not obstruction['used']:
            obstruction['used'] = True
            return False
        return movement(journey, minute, kind)

    emit = env.bus.emit

    def delayed_radio(source, kind, payload, minute, **kwargs):
        if payload.get('group_id') == 'population-0' and kind in ('movement_blocked', 'movement_resumed'):
            kwargs['delay'] = 4 if kind == 'movement_blocked' else resume_delay
            emitted.append((kind, minute))
        return emit(source, kind, payload, minute, **kwargs)

    monkeypatch.setattr(env, '_movement_safe', one_tick_obstruction)
    monkeypatch.setattr(env.bus, 'emit', delayed_radio)
    move(env, 'population-0', 'destination-0')
    obs, blocked = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'movement_blocked' and r['payload'].get('group_id') == 'population-0'), None), limit=25)
    resumed_minute = next(minute for kind, minute in emitted if kind == 'movement_resumed')
    assert blocked['observed_at'] < resumed_minute < blocked['received_at']
    assert not any(r['kind'] == 'movement_resumed' and r['payload'].get('group_id') == 'population-0'
                   for r in reports(env))
    assert group(obs)['position'] == blocked['payload']['group']['position']
    return env, initial, obs, blocked, resumed_minute


def test_late_block_report_offers_last_observed_route_and_physical_redirect_fails(v3_class, monkeypatch):
    env, initial, obs, blocked, resumed_minute = resumed_before_block_report(v3_class, monkeypatch, resume_delay=20)
    previous_order = blocked['payload']['order_id']
    options = env.evacuation_options('population-0', 'destination-1')
    origin = options['population_origin']
    assert origin['position'] == blocked['payload']['group']['position']
    assert origin['observed_at'] == blocked['observed_at'] and origin['received_at'] == blocked['received_at']
    assert 'Last reported' in origin['note'] and 'may have moved' in origin['note']
    assert options['options'] and options['options'][0]['journey']['origin'] == origin['position']
    action = {'protection_orders': [dict(group_id='population-0', action='evacuate',
        destination_id='destination-1', approach_id=options['options'][0]['id'])]}
    assert env.check_action(action)['valid']
    env.step(action)
    after, failure = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'unable_to_comply' and r['payload'].get('group_id') == 'population-0'
        and r['payload'].get('order_id') != previous_order), None), limit=20)
    assert failure['payload']['order']['state'] == 'unable_to_comply'
    assert failure['payload']['order']['action'] == 'evacuate'
    assert failure['observed_at'] > resumed_minute
    assert failure['payload']['group']['position'] != origin['position']
    assert not any(r['kind'] == 'movement_resumed' and r['payload'].get('group_id') == 'population-0'
                   for r in reports(env))
    assert group(after)['status'] == 'evacuating'


def test_failed_pending_redirect_does_not_stop_original_evacuation(v3_class, monkeypatch):
    env, initial, obs, blocked, _ = resumed_before_block_report(v3_class, monkeypatch, resume_delay=8)
    original_id = blocked['payload']['order_id']
    move(env, 'population-0', 'destination-1')
    obs, failure = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'unable_to_comply' and r['payload'].get('group_id') == 'population-0'
        and r['payload'].get('order_id') != original_id), None), limit=20)
    failed_id = failure['payload']['order_id']
    assert failure['payload']['order']['state'] == 'unable_to_comply'
    assert any(r['kind'] == 'movement_resumed' and r['payload'].get('order_id') == original_id
               for r in reports(env))
    assert failure['payload']['group']['status'] == group(obs)['status'] == 'evacuating'
    unknown = env.evacuation_options('population-0', 'destination-1')
    assert unknown['status'] == 'position_unknown' and unknown['options'] == []
    obs, arrival = advance_until(env, lambda o, rs: next((r for r in rs
        if r['kind'] == 'evacuation_arrived' and r['payload'].get('order_id') == original_id), None), limit=60)
    assert original_id != failed_id
    assert arrival['payload']['order']['destination_id'] == 'destination-0'
    assert group(obs)['status'] == 'arrived'
    assert group(obs)['position'] == destination(initial, 'destination-0')['position']
