import copy

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_control_v3.population_engine_v3 import PopulationEngineV3, VERSION, population_engine_identity


def create(cls=PopulationEngineV3):
    inputs, network = fixture_inputs()
    return cls(inputs, network, expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])


def report(group, observed, received, kind='resident_update'):
    return dict(id=f'{kind}-{observed}-{received}', kind=kind, observed_at=observed,
                reported_at=observed, received_at=received, payload={'group': group})


def test_handover_and_event_snapshot_are_causal():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    assert (group['observed_at'], group['received_at']) == (0, 0)
    assert obs['interface_version'] == VERSION
    physical = env.ops.groups[group['id']]
    physical.update(status='arrived', position=[123., 456.], destination_id='dest', exposed=999)
    env._emit('operations', 'evacuation_arrived', {'group_id': group['id']})
    physical.update(status='blocked', position=[9., 9.])
    event = env.bus._queue[-1]['record']
    assert event['payload']['group']['position'] == [123., 456.]
    assert 'exposed' not in event['payload']['group']
    assert env.get_situation()['districts'][0] == group


def test_delayed_conflicting_and_duplicate_updates():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 20
    one = dict(group, status='arrived', position=[1, 2], destination_id='d')
    two = dict(one, status='blocked', position=[3, 4])
    for sequence in ([report(one, 10, 15), report(two, 10, 20), report(one, 5, 20)],
                     [report(two, 10, 20), report(one, 10, 15), report(one, 10, 15)]):
        env._population_evidence[group['id']] = [group]
        env._known_groups[group['id']] = group
        env._receive(sequence)
        known = env._known_groups[group['id']]
        assert known['status'] is None and known['position'] is None and known['observed_at'] == 10
        assert env.evacuation_options(group['id'], obs['destinations'][0]['id'])['status'] == 'position_unknown'


def test_approach_invalidated_after_observation():
    env = create()
    obs, _ = env.reset()
    group, destination = obs['districts'][0], obs['destinations'][0]
    options = env.evacuation_options(group['id'], destination['id'])['options']
    assert options, 'fixture evacuation route must be available'
    order = {'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                        destination_id=destination['id'], approach_id=options[0]['id'])]}
    assert env.check_action(order)['valid']
    env.minute = 2
    env._receive([report(group, 1, 2)])
    assert not env.check_action(order)['valid']


def test_checkpoint_and_replay_are_v3_only():
    env = create()
    env.reset()
    env.advance_time(5)
    checkpoint = env.checkpoint()
    restored = create()
    assert restored.restore(checkpoint) == env.get_situation()
    assert restored.advance_time(5) == env.advance_time(5)
    legacy = create(IncidentDayEnv)
    legacy.reset()
    with pytest.raises(ValueError):
        create().restore(legacy.checkpoint())
    with pytest.raises(ValueError):
        create(IncidentDayEnv).restore(checkpoint)
    corrupt = copy.deepcopy(checkpoint)
    corrupt['population_evidence'] = {}
    with pytest.raises(ValueError):
        create().restore(corrupt)
    while not (env.terminated or env.truncated):
        env.step({'bad': 1})
    assert env.verify_episode()['passed']


def test_direct_resident_delivery_uses_captured_snapshot():
    env = create()
    obs, _ = env.reset()
    group_id = obs['districts'][0]['id']
    physical = env.ops.groups[group_id]
    physical.update(status='blocked', position=[1., 2.], exposed=20)
    env.minute = 1
    env.bus.emit(group_id, 'resident_update', {'group': copy.deepcopy(physical)}, 1, role='resident', delay=1)
    physical.update(status='arrived', position=[8., 9.], exposed=90)
    env.minute = 2
    delivered = env.bus.tick(2, env._center_views())
    ledger_before = copy.deepcopy(env.bus.reports)
    env._receive(delivered)
    assert env.bus.reports == ledger_before
    known = env._known_groups[group_id]
    assert (known['status'], known['position']) == ('blocked', [1., 2.])
    public = env.bus.reports[-1]['payload']['group']
    assert public['position'] == [1., 2.] and public['exposed'] == 20
    assert known['exposed'] == 20


def test_order_notice_invalidates_without_exposing_physical_state():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 2
    env.ops.groups[group['id']].update(status='arrived', position=[99, 99])
    env._emit('operations', 'order_acknowledged', {'group_id': group['id']})
    queued = env.bus._queue[-1]['record']
    assert 'group' not in queued['payload']
    notice = dict(queued, reported_at=2, received_at=2)
    env._receive([notice])
    known = env._known_groups[group['id']]
    assert known['position'] is None and known['status'] == group['status']
    assert known['count'] == group['count'] and known['home'] == group['home']
    assert known['exposed'] == group['exposed']
    assert known['field_provenance']['position']['basis'] == 'order_notice_position_unknown'
    assert env.evacuation_options(group['id'], obs['destinations'][0]['id'])['status'] == 'position_unknown'


def test_full_day_replays_with_v3_engine():
    env = create()
    env.reset()
    while not (env.terminated or env.truncated):
        env.advance_time(60)
    assert env.terminated
    assert env.verify_episode()['passed']


def test_evacuation_arrival_is_delivered_and_action_replays():
    env = create()
    obs, _ = env.reset()
    group, destination = obs['districts'][0], obs['destinations'][0]
    option = env.evacuation_options(group['id'], destination['id'])['options'][0]
    env.step({'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                        destination_id=destination['id'], approach_id=option['id'])]})
    arrivals = []
    while env.minute < 100 and not arrivals and not env.truncated:
        env.advance_time(10)
        arrivals = [r for r in env.bus.reports if r['kind'] == 'evacuation_arrived'
                    and r['payload'].get('group_id') == group['id']]
    assert arrivals
    arrival = arrivals[0]
    assert arrival['payload']['group']['status'] == 'arrived'
    assert arrival['payload']['group']['position'] == destination['position']
    known = env._known_groups[group['id']]
    assert known['status'] == 'arrived' and known['position'] == destination['position']
    while not (env.terminated or env.truncated):
        env.step({'bad': 1})
    assert env.verify_episode()['passed']


@pytest.mark.parametrize('action', ['shelter', 'evacuate'])
def test_delivered_order_action_preserves_public_fields(action):
    env = create()
    obs, _ = env.reset()
    initial = obs['districts'][0]
    identifier = initial['id']
    group = dict(initial, exposed=7, status='blocked')
    env.minute = 5
    env._receive([report(group, 2, 4)])
    before = copy.deepcopy(env._known_groups[identifier])
    order = dict(id='public-order', group_id=identifier, action=action, destination_id=None)
    notice = dict(id='notice', kind='order_acknowledged', observed_at=3, reported_at=3, received_at=5,
                  payload={'group_id': identifier, 'order_id': order['id'], 'order': order})
    env.ops.groups[identifier].update(position=[999, 999], exposed=999, status='arrived')
    env._receive([notice])
    after = env._known_groups[identifier]
    for field in ('count', 'home', 'exposed', 'status'):
        assert after[field] == before[field]
        assert after['field_provenance'][field] == before['field_provenance'][field]
    if action == 'shelter':
        assert after == before
    else:
        assert after['position'] is None
        assert after['field_provenance']['position'] == dict(observed_at=3, received_at=5,
                                                             basis='order_notice_position_unknown')
        assert env.evacuation_options(identifier, obs['destinations'][0]['id'])['status'] == 'position_unknown'


def test_relocation_ledger_and_older_fields_survive_new_operation_snapshot():
    env = create()
    obs, _ = env.reset()
    group = copy.deepcopy(env.ops.groups[obs['districts'][0]['id']])
    group.update(exposed=23, status='blocked')
    env.minute = 1
    env._emit('destination-0', 'relocation_required', {'group': group}, urgent=True)
    queued = env.bus._queue[-1]['record']
    assert queued['payload']['group'] == group
    env.minute = 10
    delivered = env.bus.tick(10, env._center_views())
    before = copy.deepcopy(env.bus.reports)
    env._receive(delivered)
    assert env.bus.reports == before
    assert env._known_groups[group['id']]['exposed'] == 23
    newer = {'id': group['id'], 'status': 'arrived', 'position': [2, 3]}
    env._receive([report(newer, 9, 10, 'evacuation_arrived')])
    known = env._known_groups[group['id']]
    assert known['exposed'] == 23 and known['count'] == group['count'] and known['home'] == group['home']
    assert known['field_provenance']['exposed']['observed_at'] == 1


def test_evacuation_notice_survives_newer_stationary_resident_snapshot():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 8
    order = dict(id='evac-1', group_id=group['id'], action='evacuate')
    notice = dict(id='ack', kind='order_acknowledged', observed_at=2, reported_at=2, received_at=3,
                  payload=dict(group_id=group['id'], order_id=order['id'], order=order))
    resident = report(dict(group, status='unwarned'), 4, 5)
    env._receive([notice, resident])
    assert env._known_groups[group['id']]['position'] is None
    assert env.evacuation_options(group['id'], obs['destinations'][0]['id'])['status'] == 'position_unknown'
    arrived_order = dict(order, state='completed', completed_at=6)
    arrival = dict(id='arrival', kind='evacuation_arrived', source='operations', role='responder',
                   observed_at=6, reported_at=6, received_at=8,
                   payload=dict(group_id=group['id'], order_id=order['id'], order=arrived_order,
                                group=dict(id=group['id'], status='arrived', position=[3, 4])))
    env._receive([arrival])
    assert env._known_groups[group['id']]['position'] == [3, 4]


def test_new_query_does_not_revalidate_old_route_token():
    env = create()
    obs, _ = env.reset()
    group, dest = obs['districts'][0], obs['destinations'][0]
    old = env.evacuation_options(group['id'], dest['id'])['options'][0]['id']
    def order(token):
        return {'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                          destination_id=dest['id'], approach_id=token)]}
    assert env.check_action(order(old))['valid']
    env.minute = 2
    env._receive([report(group, 1, 2)])
    new = env.evacuation_options(group['id'], dest['id'])['options'][0]['id']
    assert new != old
    assert not env.check_action(order(old))['valid']
    assert env.check_action(order(new))['valid']


def test_public_engine_identity_matches_trajectory_without_episode_state():
    identity = population_engine_identity()
    assert isinstance(identity, str) and len(identity) == 64
    int(identity, 16)
    env = create()
    env.reset()
    assert env.trajectory()['implementation_sha256'] == identity
    env.advance_time(5)
    assert population_engine_identity() == identity == env.trajectory()['implementation_sha256']


@pytest.mark.parametrize('kind,state', [('movement_blocked', 'blocked'),
                                        ('unable_to_comply', 'unable_to_comply')])
def test_stopped_evacuation_allows_alternate_route_only_after_delivery(kind, state):
    env = create()
    obs, _ = env.reset()
    initial = obs['districts'][0]
    group_id = initial['id']
    destination_id = obs['destinations'][0]['id']
    order = dict(id='evac-stopped', group_id=group_id, action='evacuate', state=state,
                 destination_id=destination_id)
    if kind == 'unable_to_comply':
        order['completed_at'] = 3
    env.minute = 2
    notice = dict(id='ack-stopped', kind='order_acknowledged', observed_at=1,
                  reported_at=1, received_at=2,
                  payload=dict(group_id=group_id, order_id=order['id'],
                               order=dict(order, state='acknowledged')))
    env._receive([notice])
    env.ops.orders[order['id']] = order
    env.ops.groups[group_id].update(status=state, position=copy.deepcopy(initial['position']))
    env.minute = 3
    env._emit('operations', kind, dict(group_id=group_id, order_id=order['id'],
                                       position=copy.deepcopy(initial['position'])))
    queued = copy.deepcopy(env.bus._queue[-1]['record'])
    # The actual group resumes after the event, but that is not delivered.
    env.ops.groups[group_id].update(position=[999, 999], status='evacuating')
    assert env.evacuation_options(group_id, destination_id)['status'] == 'position_unknown'
    assert env._known_groups[group_id]['position'] is None
    for minute in range(3, 12):
        env.minute = minute
        env._receive(env.bus.tick(minute, env._center_views()))
        if any(item['id'] == queued['id'] for item in env.bus.reports):
            break
    else:
        pytest.fail('stop report was not delivered')
    known = env._known_groups[group_id]
    assert known['position'] == initial['position']
    assert known['field_provenance']['position']['observed_at'] == 3
    route_result = env.evacuation_options(group_id, destination_id)
    origin = route_result['population_origin']
    assert origin['position'] == initial['position'] and origin['observed_at'] == 3
    assert origin['received_at'] == env.minute and origin['order_id'] == order['id']
    assert 'Last reported' in origin['note'] and 'may have moved' in origin['note']
    assert '[999, 999]' not in str(route_result)
    options = route_result['options']
    assert options, 'fixture alternate route must be available'
    action = {'protection_orders': [dict(group_id=group_id, action='evacuate',
                                         destination_id=destination_id, approach_id=options[0]['id'])]}
    assert env.check_action(action)['valid']


def test_stopped_report_for_different_order_does_not_resolve_uncertainty():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 8
    notice_order = dict(id='pending', group_id=group['id'], action='evacuate')
    notice = dict(id='ack', kind='order_acknowledged', observed_at=2, reported_at=2, received_at=3,
                  payload=dict(group_id=group['id'], order_id='pending', order=notice_order))
    stopped_order = dict(id='other', group_id=group['id'], action='evacuate', state='blocked')
    stopped = dict(id='blocked', kind='movement_blocked', source='operations', role='responder',
                   observed_at=4, reported_at=4, received_at=5,
                   payload=dict(group_id=group['id'], order_id='other', order=stopped_order,
                                position=group['position'], group=dict(group, status='blocked')))
    env._receive([notice, stopped])
    assert env._known_groups[group['id']]['position'] is None


@pytest.mark.parametrize('restored', [False, True])
def test_bus_filters_population_bookkeeping_before_ledger_insertion(restored):
    env = create()
    obs, _ = env.reset()
    if restored:
        saved = env.checkpoint()
        env = create()
        env.restore(saved)
    identifier = obs['districts'][0]['id']
    group = copy.deepcopy(env.ops.groups[identifier])
    group.update(exposed=11, pending_order_id='secret-pending', internal_plan='private')
    original = copy.deepcopy(group)
    env.minute = 1
    event_id = env.bus.emit(identifier, 'resident_update', {'group': group}, 1, role='resident', delay=1)
    assert group == original
    queued = next(item['record'] for item in env.bus._queue if item['record']['id'] == event_id)
    assert queued['payload']['group']['exposed'] == 11
    assert 'pending_order_id' not in queued['payload']['group']
    env.minute = 2
    records = env.bus.tick(2, env._center_views())
    ledger = copy.deepcopy(env.bus.reports)
    env._receive(records)
    assert env.bus.reports == ledger
    delivered = next(item for item in env.bus.reports if item['id'] == event_id)
    district = next(item for item in env.get_situation()['districts'] if item['id'] == identifier)
    for value in (delivered['payload']['group'], district):
        assert value['exposed'] == 11
        assert 'pending_order_id' not in value and 'internal_plan' not in value


def test_terminal_failure_clears_only_its_own_pending_order():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    identifier = group['id']
    env.minute = 10
    def notice(order_id, observed):
        order = dict(id=order_id, group_id=identifier, action='evacuate',
                     issued_at=1 if order_id == 'a' else 2)
        return dict(id='ack-' + order_id, kind='order_acknowledged', observed_at=observed,
                    reported_at=observed, received_at=10,
                    payload=dict(group_id=identifier, order_id=order_id, order=order))
    def failure(order_id, observed):
        order = dict(id=order_id, group_id=identifier, action='evacuate',
                     issued_at=1 if order_id == 'a' else 2,
                     state='unable_to_comply', completed_at=observed)
        return dict(id='fail-' + order_id, kind='unable_to_comply', source='operations', role='responder',
                    observed_at=observed, reported_at=observed, received_at=10,
                    payload=dict(group_id=identifier, order_id=order_id, order=order,
                                 position=group['position'], group=dict(group,
                                     status='evacuating' if order_id == 'b' else 'unable_to_comply')))
    # B is a rejected replacement; A remains the active evacuation.
    env._receive([notice('a', 1), notice('b', 2), failure('b', 4)])
    assert env._known_groups[identifier]['position'] is None
    assert env._known_groups[identifier]['status'] == 'evacuating'
    env._receive([failure('a', 5)])
    assert env._known_groups[identifier]['position'] == group['position']
    assert env.evacuation_options(identifier, obs['destinations'][0]['id'])['options']


def test_unknown_position_query_is_logged_and_replayed():
    env = create()
    obs, _ = env.reset()
    group, dest = obs['districts'][0], obs['destinations'][0]
    with pytest.raises(ValueError, match='reported group'):
        env.evacuation_options('not-a-group', dest['id'])
    with pytest.raises(ValueError, match='reported group'):
        env.evacuation_options(group['id'], 'not-a-destination')
    option = env.evacuation_options(group['id'], dest['id'])['options'][0]
    env.step({'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                        destination_id=dest['id'], approach_id=option['id'])]})
    while env.minute < 20 and env._usable_position(group['id']):
        env.advance_time(1)
    assert not env._usable_position(group['id'])
    unknown = env.evacuation_options(group['id'], dest['id'])
    assert unknown['status'] == 'position_unknown' and unknown['options'] == []
    assert 'unknown' in unknown['explanation']
    assert env._query_log[-1]['tool'] == 'evacuation_options'
    while not (env.terminated or env.truncated):
        env.step({'bad': 1})
    assert env.verify_episode()['passed']


def test_successful_replacement_supersedes_only_earlier_orders_in_any_delivery_order():
    from itertools import permutations

    def record(group, order_id, issued, observed, kind):
        order = dict(id=order_id, group_id=group['id'], action='evacuate', issued_at=issued)
        payload = dict(group_id=group['id'], order_id=order_id, order=order)
        if kind == 'evacuation_arrived':
            order.update(state='completed', completed_at=observed)
            payload['group'] = dict(id=group['id'], status='arrived', position=group['position'])
        return dict(id=kind + order_id, kind=kind, source='operations', role='responder',
                    observed_at=observed, reported_at=observed, received_at=20, payload=payload)

    for sequence in permutations(('a', 'b', 'success')):
        env = create()
        obs, _ = env.reset()
        group = obs['districts'][0]
        env.minute = 20
        events = {'a': record(group, 'a', 1, 3, 'order_acknowledged'),
                  'b': record(group, 'b', 4, 6, 'order_acknowledged'),
                  'success': record(group, 'b', 4, 10, 'evacuation_arrived')}
        for key in sequence:
            env._receive([events[key]])
        assert env._known_groups[group['id']]['position'] == group['position']
        assert env.evacuation_options(group['id'], obs['destinations'][0]['id'])['options']
        # A newer pending order remains uncertain even though B arrived.
        env._receive([record(group, 'c', 11, 12, 'order_acknowledged')])
        assert env._known_groups[group['id']]['position'] is None


@pytest.mark.parametrize('a_issued,b_issued', [(None, 4), (1, None), (4, 4), (5, 4)])
def test_missing_equal_or_later_issue_times_cannot_supersede(a_issued, b_issued):
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 20
    def event(identifier, issued, observed, arrival=False):
        order = dict(id=identifier, group_id=group['id'], action='evacuate')
        if issued is not None:
            order['issued_at'] = issued
        payload = dict(group_id=group['id'], order_id=identifier, order=order)
        if arrival:
            order.update(state='completed', completed_at=observed)
            payload['group'] = dict(id=group['id'], status='arrived', position=group['position'])
        return dict(id=identifier + str(observed), kind='evacuation_arrived' if arrival else 'order_acknowledged',
                    source='operations', role='responder', observed_at=observed, reported_at=observed,
                    received_at=20, payload=payload)
    env._receive([event('a', a_issued, 6), event('b', b_issued, 7), event('b', b_issued, 10, True)])
    assert env._known_groups[group['id']]['position'] is None


def test_conflicting_public_issue_times_do_not_prove_supersession():
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 20
    def event(identifier, issued, observed, arrival=False):
        order = dict(id=identifier, group_id=group['id'], action='evacuate', issued_at=issued)
        payload = dict(group_id=group['id'], order_id=identifier, order=order)
        if arrival:
            order.update(state='completed', completed_at=observed)
            payload['group'] = dict(id=group['id'], status='arrived', position=group['position'])
        return dict(id=identifier + str(observed), kind='evacuation_arrived' if arrival else 'order_acknowledged',
                    source='operations', role='responder', observed_at=observed, reported_at=observed,
                    received_at=20, payload=payload)
    env._receive([event('a', 1, 3), event('a', 2, 4), event('b', 5, 7), event('b', 5, 10, True)])
    assert env._known_groups[group['id']]['position'] is None


@pytest.mark.parametrize('source,role', [('resident', 'resident'), ('operations', 'resident'), ('other', 'responder')])
def test_untrusted_arrival_cannot_resolve_order_uncertainty(source, role):
    env = create()
    obs, _ = env.reset()
    group = obs['districts'][0]
    env.minute = 10
    order = dict(id='evac', group_id=group['id'], action='evacuate', issued_at=1)
    notice = dict(id='ack', kind='order_acknowledged', observed_at=2, reported_at=2, received_at=3,
                  payload=dict(group_id=group['id'], order_id=order['id'], order=copy.deepcopy(order)))
    order.update(state='completed', completed_at=5)
    arrival = dict(id='fake', kind='evacuation_arrived', source=source, role=role,
                   observed_at=5, reported_at=5, received_at=6,
                   payload=dict(group_id=group['id'], order_id=order['id'], order=order,
                                group=dict(id=group['id'], status='arrived', position=group['position'])))
    # The forged arrival can be delivered before the earlier notice.
    env._receive([arrival, notice])
    assert env._known_groups[group['id']]['position'] is None
    assert env.evacuation_options(group['id'], obs['destinations'][0]['id'])['status'] == 'position_unknown'
