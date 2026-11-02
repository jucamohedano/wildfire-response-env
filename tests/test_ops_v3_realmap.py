"""Original issues reproduced against frozen v2 and corrected v3 contracts."""
import copy
import hashlib
import json
import os
from pathlib import Path
import pytest
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_ops_v3 import IncidentDayEnvV3, observation_identity
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN


@pytest.fixture(scope='module')
def source():
    root=Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME','/var/tmp/wildfire-evaluation-pack-v1'))
    folder=root/'runtime/es-ma-2021'
    if not (folder/'inputs.json').exists(): pytest.skip('Prepared source pack required')
    return json.loads((folder/'inputs.json').read_text()),json.loads((folder/'network.json').read_text())


def test_original_arrival_reconciles_only_after_delivery_and_preserves_physics(source):
    opts=dict(expected_inputs_sha256=PACK_PIN,expected_network_sha256=NETWORK_PIN,allow_unapproved_data=True)
    old,new=IncidentDayEnv(*source,**opts),IncidentDayEnvV3(*source,**opts)
    for e in (old,new):
        e.reset('incident-day-realmap-v2-001')
        option=e.evacuation_options('population-0','destination-0')['options'][0]
        assert e.check_action({'protection_orders':[dict(group_id='population-0',action='evacuate',
            destination_id='destination-0',approach_id=option['id'])]})['valid']
        e.step({'protection_orders':[dict(group_id='population-0',action='evacuate',
            destination_id='destination-0',approach_id=option['id'])]})
    for _ in range(20):
        assert old.minute==new.minute
        assert old.fire.checkpoint()==new.fire.checkpoint()
        assert old.ops.checkpoint()==new.ops.checkpoint()
        delivered=[r for r in new.bus.reports if r['kind']=='evacuation_arrived']
        if delivered: break
        assert next(g for g in new.get_situation()['districts'] if g['id']=='population-0')['status']!='arrived'
        old.advance_time(1);new.advance_time(1)
    else:pytest.fail('Expected delivered arrival')
    report=delivered[-1];group=next(g for g in new.get_situation()['districts'] if g['id']=='population-0')
    assert (report['observed_at'],report['received_at'])==(6,10)
    assert group['status']=='arrived'
    assert (group['observed_at'],group['received_at'])==(6,10)
    assert next(g for g in old.get_situation()['districts'] if g['id']=='population-0')['status']=='unwarned'
    preview=new.evacuation_options('population-0','destination-1')
    assert preview['population_origin']['position']==group['position']


def test_original_cross_kind_check_is_actionable_and_side_effect_free(source):
    e=IncidentDayEnvV3(*source,expected_inputs_sha256=PACK_PIN,expected_network_sha256=NETWORK_PIN,allow_unapproved_data=True)
    e.reset('incident-day-evaluation-v1-000')
    option=e.evacuation_options('population-0','destination-0')['options'][0]
    action={'requests':[dict(resource_id='engine-0',sector_id='incident',approach_id=option['id'])]}
    before=copy.deepcopy(e.checkpoint()); result=e.check_action(action)
    assert not result['valid'] and 'requests[0].approach_id' in result['error']
    assert 'route_options' in result['error'] and "'resource_id'"!=result['error']
    assert before==e.checkpoint()
    e.step(action)
    assert e.minute==1 and e.invalid_streak==1


def test_frozen_v2_release_artifacts_unchanged():
    assert implementation_digest()=='f1e9388cbdc01ea48c51ceda091e66a9ecb103718bf62f0dfe6b01cfd3166581'
    path=Path('/var/tmp/wildfire-evaluation-release-v1.json')
    if not path.exists():pytest.skip('Private frozen release descriptor unavailable')
    for item in json.loads(path.read_text())['artifacts'].values():
        assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256']
    assert observation_identity()!=implementation_digest()
