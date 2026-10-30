"""The v3 control wrapper must use the corrected public population view."""
import copy

from wildfire_core.incident_day import fixture_inputs
from wildfire_control_v3.control import ControlCenterV3, create_population_control, interface_digest
from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS


def make_control():
    inputs, network = fixture_inputs()
    return create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                     expected_network_sha256=network['sha256'])


def test_delivered_arrival_drives_briefing_and_relocation_options():
    control = make_control()
    observation, _ = control.reset('incident-day-egress-v1-000')
    group = next(item for item in observation['districts'] if item['id'] == 'population-0')
    initial_position = copy.deepcopy(group['position'])
    destinations = {item['id']: item for item in observation['destinations']}
    old = control.execute('evacuation_options', {'group_id': 'population-0',
                                                  'destination_id': 'destination-1'})['options'][0]
    destination = control.execute('evacuation_options', {'group_id': 'population-0',
                                                          'destination_id': 'destination-0'})['options'][0]
    action = {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate',
        'destination_id': 'destination-0', 'approach_id': destination['id']}]}
    assert control.execute('check_action', {'action': action})['valid']
    result = control.execute('commit_orders', {'action': action})
    assert result['observation']['minute'] == 1
    for _ in range(200):
        result = control.execute('advance', {'max_minutes': 5})
        observation = result['observation']
        delivered = control.execute('get_report_page', {'after_cursor': 0, 'limit': 20})
        # Early position changes may never reveal physical movement before a report.
        group = next(item for item in observation['districts'] if item['id'] == 'population-0')
        arrival = next((report for report in observation['reports']
                        if report['kind'] == 'evacuation_arrived'
                        and report.get('payload', {}).get('group_id') == 'population-0'), None)
        if arrival is not None:
            break
        assert group['position'] == initial_position or group.get('received_at') is not None
        assert not result['terminated'] and not result['truncated']
        assert delivered['next_cursor'] <= delivered['total']
    else:
        raise AssertionError('No public arrival report was delivered')
    assert group['status'] == 'arrived'
    assert group['position'] == destinations['destination-0']['position']
    assert group['observed_at'] <= group['received_at'] <= observation['minute']
    assert 'population-0' in result['briefing']['page']
    stale_action = {'protection_orders': [{'group_id': 'population-0', 'action': 'evacuate',
        'destination_id': 'destination-1', 'approach_id': old['id']}]}
    stale = control.execute('check_action', {'action': stale_action})
    assert not stale['valid'] and stale['next_tool'] == 'evacuation_options'
    replacement = control.execute('evacuation_options', {'group_id': 'population-0',
                                                          'destination_id': 'destination-1'})
    assert replacement['options']
    assert all(option['journey']['origin'] == group['position'] for option in replacement['options'])


def test_private_restore_preserves_v3_engine_and_public_guard():
    control = make_control()
    control.reset('incident-day-egress-v1-000')
    invalid = {'action': {'releases': ['unknown-resource']}}
    for _ in range(3):
        control.execute('check_action', invalid)
    checkpoint = control.private_checkpoint()
    other = make_control()
    other.restore_private_checkpoint(checkpoint)
    assert isinstance(other.environment, PopulationEngineV3)
    assert other.execute('check_action', invalid) == control.execute('check_action', invalid)
    assert other.execute('advance', {'max_minutes': 5}) == control.execute('advance', {'max_minutes': 5})


def test_v3_abandonment_is_replayed_and_checked_against_distinct_identity():
    control = make_control()
    observation, _ = control.reset('incident-day-egress-v1-000')
    identity = {key: control.environment.trajectory()[key] for key in IDENTITY_FIELDS}
    assert identity['interface_version'] != 'wildfire-incident-day-v2'
    assert observation['interface_version'] == identity['interface_version']
    pin = interface_digest()
    for _ in range(3):
        result = control.execute('commit_orders', {'action': {'unknown': []}})
    assert result['truncated'] and result['reward'] is None
    verification = control.finalize(expected_identity=identity, expected_interface_sha256=pin)
    assert verification['status'] == 'invalid_abandonment'
    assert verification['verified'] and verification['score'] == 0.0


def test_unknown_population_route_error_is_public_and_actionable(monkeypatch):
    control = make_control()
    observation, _ = control.reset('incident-day-egress-v1-000')
    group = next(item for item in observation['districts'] if item['id'] == 'population-0')
    group.update(position=None, status=None)
    monkeypatch.setattr(control.environment, 'get_situation', lambda: copy.deepcopy(observation))
    def unavailable(*args, **kwargs):
        raise ValueError('PRIVATE internal location detail')
    monkeypatch.setattr(control.environment, 'evacuation_options', unavailable)
    result = control.execute('evacuation_options', {'group_id':'population-0', 'destination_id':'destination-0'})
    assert result['code'] == 'population_position_unknown'
    assert result['next_tool'] == 'get_reports' and result['committed'] is False
    assert 'PRIVATE' not in str(result) and control.environment.minute == 0


def test_new_delivered_evidence_invalidates_old_route_even_at_same_position():
    control = make_control()
    observation, _ = control.reset('incident-day-egress-v1-000')
    group = next(item for item in observation['districts'] if item['id'] == 'population-0')
    query = {'group_id':'population-0', 'destination_id':'destination-0'}
    old = control.execute('evacuation_options', query)['options'][0]['id']
    control.execute('advance', {'max_minutes':1})
    # Deliver a new observed snapshot whose position remains the same.
    snapshot = {key:copy.deepcopy(group[key]) for key in ('id','status','position','destination_id')}
    report = {'id':'fixture-update', 'source':'population-0', 'role':'resident', 'kind':'resident_update',
              'observed_at':1, 'reported_at':1, 'received_at':1, 'payload':{'group':snapshot}}
    control.environment._receive([report])
    action = {'protection_orders':[{'group_id':'population-0', 'action':'evacuate',
                                    'destination_id':'destination-0', 'approach_id':old}]}
    result = control.execute('check_action', {'action':action})
    assert result['code'] == 'stale_evacuation_approach'
    assert result['field_path'] == 'protection_orders[0].approach_id'
    assert result['next_tool'] == 'evacuation_options' and result['next_arguments'] == query
    new = control.execute('evacuation_options', query)['options'][0]['id']
    assert new != old and not control.execute('check_action', {'action':action})['valid']
    action['protection_orders'][0]['approach_id'] = new
    assert control.execute('check_action', {'action':action})['valid']
