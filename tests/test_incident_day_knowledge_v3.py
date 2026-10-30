from copy import deepcopy
from itertools import permutations

from wildfire_control_v3.knowledge_v3 import reconcile_situation_v3


def situation(minute=40):
    return {'minute': minute, 'districts': [{'id': 'g', 'status': 'at_home',
        'position': [1, 2], 'destination_id': None, 'extra': {'kept': True}}],
        'protection_orders': [{'id':'o', 'group_id':'g', 'action':'evacuate', 'destination_id':'d',
                               'observed_at':10, 'received_at':12}], 'other': ['unchanged']}


def report(kind, observed, received, payload, id='r'):
    return dict(id=id, kind=kind, source='operations', role='responder', observed_at=observed, reported_at=observed,
                received_at=received, payload=payload)


def arrival(observed=10, received=12, order_id='o', dest='d'):
    return report('evacuation_arrived', observed, received, {'group_id': 'g', 'order_id': order_id,
        'order': {'id': order_id, 'group_id': 'g', 'action': 'evacuate', 'state': 'completed',
                  'completed_at': observed, 'destination_id': dest}})


def group(observed, received, status, **extra):
    return report('resident_update', observed, received,
                  {'group': {'id': 'g', 'status': status, **extra}}, id=f'g{observed}')


def knowledge(s, reports):
    return reconcile_situation_v3(s, reports)['districts'][0]['knowledge_v3']


def test_unknown_and_deep_copies_preserve_legacy():
    s = situation()
    before = deepcopy(s)
    result = reconcile_situation_v3(s, [])
    k = result['districts'][0].pop('knowledge_v3')
    assert result == before and s == before
    assert k == dict(status=None, destination_id=None, position=None, observed_at=None,
                     received_at=None, report_id=None, basis='unknown')
    result['districts'][0]['extra']['kept'] = False
    assert s == before


def test_arrival_requires_authoritative_matching_order_and_delivery():
    s = situation(11)
    r = arrival()
    assert knowledge(s, [r])['status'] is None
    s['minute'] = 12
    assert knowledge(s, [r]) == dict(status='arrived', destination_id='d', position=None,
        observed_at=10, received_at=12, report_id='r', basis='evacuation_arrived')
    for field, value in [('id', 'other'), ('group_id', 'other'), ('state', 'executing'),
                         ('action', 'shelter'), ('completed_at', 9), ('destination_id', None)]:
        bad = deepcopy(r)
        bad['payload']['order'][field] = value
        assert knowledge(s, [bad])['status'] is None
    assert knowledge(s, [report('order_acknowledged', 10, 12, r['payload'])])['status'] is None
    spoof = deepcopy(r)
    spoof['source'] = 'resident'
    assert knowledge(s, [spoof])['status'] is None
    missing_order = deepcopy(s)
    missing_order['protection_orders'] = []
    assert knowledge(missing_order, [r])['status'] is None


def test_observed_order_not_delivery_order_and_ties_conservative():
    s = situation()
    reports = [arrival(10, 30), group(20, 21, 'blocked', destination_id='d', position=[3, 4])]
    for sequence in permutations(reports):
        k = knowledge(s, list(sequence))
        assert (k['status'], k['observed_at'], k['received_at']) == ('blocked', 20, 21)
    reports = [arrival(10, 12), group(10, 13, 'blocked')]
    for sequence in permutations(reports):
        assert knowledge(s, list(sequence))['status'] is None


def test_later_relocation_and_new_order_invalidate_arrival():
    s = situation()
    a = arrival()
    relocation = report('relocation_required', 20, 23,
        {'group': {'id': 'g', 'status': 'blocked', 'destination_id': 'd', 'position': [4, 5]}})
    assert knowledge(s, [a, relocation])['status'] == 'blocked'
    replacement = report('order_acknowledged', 18, 20, {'group_id': 'g', 'order_id': 'new'})
    assert knowledge(s, [a, replacement])['status'] is None
    assert knowledge(s, [a, report('order_acknowledged', 18, 20,
        {'group_id': 'g', 'order_id': 'o'})])['status'] == 'arrived'
    s['protection_orders'].append({'id': 'new', 'group_id': 'g', 'observed_at': 18, 'received_at': 20})
    assert knowledge(s, [a])['status'] is None
    s['protection_orders'][-1]['received_at'] = 41
    assert knowledge(s, [a])['status'] == 'arrived'


def test_same_minute_resume_precedes_arrival_for_the_same_order():
    s = situation()
    resumed = report('movement_resumed', 10, 12, {'group_id': 'g', 'order_id': 'o'})
    assert knowledge(s, [arrival(), resumed])['status'] == 'arrived'
    other = report('movement_resumed', 10, 12, {'group_id': 'g', 'order_id': 'other'})
    assert knowledge(s, [arrival(), other])['status'] is None


def test_invalid_times_and_private_lookalikes_are_never_used():
    s = situation()
    r = arrival()
    r['payload']['private_audit'] = {'status': 'secret', 'future_events': [1]}
    before = deepcopy(r)
    assert 'secret' not in str(knowledge(s, [r]))
    assert r == before
    for key, value in [('observed_at', True), ('reported_at', 9), ('received_at', 41),
                       ('received_at', None)]:
        bad = deepcopy(r)
        bad[key] = value
        assert knowledge(s, [bad])['status'] is None
    assert knowledge(s, [{'kind': 'evacuation_arrived', 'payload': r['payload']}])['status'] is None


def test_snapshot_freshness_and_late_old_report():
    s = situation()
    s['districts'][0].update(status='blocked', observed_at=20, received_at=22)
    assert knowledge(s, [arrival(10, 30)])['status'] == 'blocked'
    s['districts'][0]['received_at'] = 41
    assert knowledge(s, [])['observed_at'] is None


def test_realmap_public_arrival_at_six_delivered_at_ten_matches_orderless_district():
    s = situation(9)
    s['protection_orders'][0].update(observed_at=3, received_at=6)
    s['districts'][0].update(status='unwarned', destination_id=None,
                            position=[1, 2], observed_at=0, received_at=0)
    arrived = arrival(6, 10)
    # A future report and order snapshot cannot be used at minute nine.
    future = deepcopy(s)
    future['protection_orders'][0].update(observed_at=6, received_at=10)
    assert knowledge(future, [arrived])['status'] == 'unwarned'
    s['minute'] = 10
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=6, received_at=10,
                            field_provenance={'position': {'observed_at': 6, 'received_at': 10}})
    s['protection_orders'][0].update(state='completed', observed_at=6, received_at=10)
    actual = knowledge(s, [arrived])
    assert (actual['status'], actual['destination_id'], actual['position']) == ('arrived', 'd', [3, 4])
    assert (actual['observed_at'], actual['received_at']) == (6, 10)
    # Out-of-order delivery of an older notice about another order cannot
    # invalidate a newer observation, even when it arrives later.
    old = report('order_acknowledged', 2, 10, {'group_id': 'g', 'order_id': 'old'})
    assert knowledge(s, [arrived, old]) == actual


def test_same_minute_different_order_and_conflicting_arrivals_remain_unknown():
    s = situation(20)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=10, received_at=12)
    s['protection_orders'][0].update(observed_at=10, received_at=12)
    another = report('order_acknowledged', 10, 13, {'group_id': 'g', 'order_id': 'new'})
    for sequence in permutations([arrival(), another]):
        assert knowledge(s, list(sequence))['status'] is None
    other_order = dict(id='other', group_id='g', action='evacuate', destination_id='d',
                       observed_at=10, received_at=12)
    s['protection_orders'].append(other_order)
    other_arrival = arrival(order_id='other')
    for sequence in permutations([arrival(), other_arrival]):
        assert knowledge(s, list(sequence))['status'] is None


def test_orderless_arrival_does_not_borrow_later_or_undelivered_order_evidence():
    s = situation(15)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=10, received_at=12)
    s['protection_orders'][0].update(observed_at=10, received_at=12)
    future = arrival(10, 16)
    assert knowledge(s, [future])['status'] is None
    different_minute = arrival(11, 12)
    assert knowledge(s, [different_minute])['observed_at'] == 11
    # A new same-minute order, even delivered late, is not confused with the
    # matching arrival order once its receipt becomes public.
    newer = report('order_delivered', 10, 16, {'group_id': 'g', 'order_id': 'new'})
    assert knowledge(s, [arrival(), newer])['status'] == 'arrived'
    s['minute'] = 16
    assert knowledge(s, [arrival(), newer])['status'] is None


def test_position_provenance_cannot_make_an_older_position_look_new():
    s = situation()
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=10, received_at=12,
                            field_provenance={'position': {'observed_at': 5, 'received_at': 6}})
    result = knowledge(s, [arrival()])
    assert result['status'] == 'arrived' and result['position'] is None
    s['districts'][0]['field_provenance']['position'] = {'observed_at': 10, 'received_at': 41}
    assert knowledge(s, [arrival()])['position'] is None


def test_conflicting_duplicate_order_snapshots_never_authenticate_arrival():
    s = situation()
    duplicate = dict(s['protection_orders'][0], destination_id='different', received_at=13)
    for orders in permutations([s['protection_orders'][0], duplicate]):
        s['protection_orders'] = list(orders)
        assert knowledge(s, [arrival()])['status'] is None


def test_combined_position_is_not_attributed_to_an_earlier_arrival_receipt():
    s = situation(14)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=10, received_at=14,
                            field_provenance={'position': {'observed_at': 10, 'received_at': 14}})
    result = knowledge(s, [arrival()])
    assert result['status'] == 'arrived' and result['position'] == [3, 4]
    assert result['received_at'] == 14
    s['districts'][0]['received_at'] = 12
    assert knowledge(s, [arrival()])['position'] is None


def test_invalid_district_order_identity_stays_unknown():
    s = situation()
    s['districts'][0].update(status='arrived', destination_id='d', observed_at=10,
                            received_at=12, order_id=['invalid'])
    assert knowledge(s, [arrival()])['status'] is None


def test_borrowed_order_identity_waits_for_arrival_and_public_order():
    s = situation(10)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=6, received_at=7)
    s['protection_orders'][0].update(observed_at=6, received_at=10)
    arrived = arrival(6, 10)
    result = knowledge(s, [arrived])
    assert result['status'] == 'arrived' and result['received_at'] == 10
    assert result['observed_at'] == 6 and result['position'] == [3, 4]

    # The arrival was received at ten, but its public order was not delivered
    # until thirteen: the association cannot be attributed to minute ten.
    s['minute'] = 13
    s['protection_orders'][0]['received_at'] = 13
    result = knowledge(s, [arrived])
    assert result['status'] == 'arrived' and result['received_at'] == 13
    # Arrival-only evidence also requires both deliveries.
    s['districts'][0].pop('observed_at')
    assert knowledge(s, [arrived])['received_at'] == 13


def test_duplicate_matching_orders_use_first_public_receipt_in_any_order():
    s = situation(20)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=6, received_at=7)
    order = dict(s['protection_orders'][0], observed_at=6, received_at=9)
    later = dict(order, received_at=13)
    for sequence in permutations([order, later]):
        s['protection_orders'] = list(sequence)
        assert knowledge(s, [arrival(6, 10)])['received_at'] == 10


def test_late_conflicting_order_invalidates_borrowed_identity():
    s = situation(15)
    s['districts'][0].update(status='arrived', destination_id='d', position=[3, 4],
                            observed_at=6, received_at=7)
    s['protection_orders'][0].update(observed_at=6, received_at=10)
    arrived = arrival(6, 10)
    conflict = report('order_acknowledged', 6, 16, {'group_id': 'g', 'order_id': 'different'})
    assert knowledge(s, [arrived, conflict])['received_at'] == 10
    s['minute'] = 16
    result = knowledge(s, [arrived, conflict])
    assert result['status'] is None and result['received_at'] is None
