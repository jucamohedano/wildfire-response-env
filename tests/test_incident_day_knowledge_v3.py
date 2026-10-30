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
