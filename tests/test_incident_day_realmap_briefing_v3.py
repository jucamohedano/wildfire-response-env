"""Public-client regression for the pinned real-map issue-59 exercise."""
import hashlib
import json
import os
from pathlib import Path

import pytest


DATA = Path(os.environ.get('WILDFIRE_ISSUE59_DATA', '.runtime/wildfire-response-spain'))


def briefing_district(control, identifier):
    page = control.execute('get_briefing', {})['page']
    line = next(line for line in page.splitlines() if line.startswith('DISTRICTS '))
    return next(item for item in json.loads(line.split(') ', 1)[1]) if item['id'] == identifier)


@pytest.mark.skipif(not (DATA / 'manifest.json').is_file(), reason='pinned real-map data pack is not installed')
def test_realmap_arrival_observed_at_six_delivered_at_ten():
    from eval.hub_runtime import make_environment
    from wildfire_control_v3.control import create_population_control

    lock = json.loads((Path(__file__).resolve().parents[1] / 'data/hub_runtime_v1/release.json').read_text())
    assert hashlib.sha256((DATA / 'manifest.json').read_bytes()).hexdigest() == lock['manifest_sha256']
    base, entry = make_environment(DATA, 'es-ma-2021', allow_unapproved_data=True)
    control = create_population_control(base.inputs, base.network.document,
        expected_inputs_sha256=entry['inputs_sha256'], expected_network_sha256=entry['network_sha256'],
        allow_unapproved_data=True)
    obs, _ = control.reset('incident-day-realmap-v2-001')
    group = next(item for item in obs['districts'] if item['id'] == 'population-0')
    destination = next(item for item in obs['destinations'] if item['id'] == 'destination-0')
    option = control.execute('evacuation_options', {'group_id': group['id'], 'destination_id': destination['id']})['options'][0]
    action = {'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                        destination_id=destination['id'], approach_id=option['id'])]}
    assert control.execute('check_action', {'action': action})['valid']
    result = control.execute('commit_orders', {'action': action})
    while result['observation']['minute'] < 9:
        result = control.execute('advance', {'max_minutes': 1})
        assert not result['terminated'] and not result['truncated']
    before = result['observation']
    view = next(item for item in before['districts'] if item['id'] == group['id'])
    assert view['position'] is None and view['status'] != 'arrived'
    assert not any(r['kind'] == 'evacuation_arrived' and r['payload'].get('group_id') == group['id']
                   for r in before['reports'])
    assert briefing_district(control, group['id'])['delivered_knowledge']['status'] != 'arrived'
    assert control.execute('evacuation_options', {'group_id': group['id'],
                                                  'destination_id': destination['id']})['status'] == 'position_unknown'
    result = control.execute('advance', {'max_minutes': 1})
    after = result['observation']
    assert after['minute'] == 10
    arrival = next(r for r in after['reports'] if r['kind'] == 'evacuation_arrived'
                   and r['payload'].get('group_id') == group['id'])
    assert (arrival['observed_at'], arrival['received_at']) == (6, 10)
    view = next(item for item in after['districts'] if item['id'] == group['id'])
    assert view['status'] == 'arrived' and view['position'] == arrival['payload']['group']['position']
    briefing = briefing_district(control, group['id'])
    assert briefing['status'] == briefing['delivered_knowledge']['status'] == 'arrived'
    assert briefing['delivered_knowledge']['destination_id'] == destination['id']
    routes = control.execute('evacuation_options', {'group_id': group['id'], 'destination_id': destination['id']})
    assert routes['options']
    assert all(item['journey']['origin'] == arrival['payload']['group']['position'] for item in routes['options'])
    assert routes['population_origin']['observed_at'] == 6
    assert routes['population_origin']['received_at'] == 10
