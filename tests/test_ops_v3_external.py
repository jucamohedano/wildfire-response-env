"""Explicit source-map HTTP acceptance for the opt-in corrected contract."""
import json
import os
from pathlib import Path
import socket
import threading
import time
import pytest
import uvicorn
from eval.incident_day_server import create_app
from wildfire_ops_v3 import IncidentDayEnvV3, observation_identity
from wildfire_openenv.incident_day import IncidentDayEnvironment
from wildfire_openenv.incident_day_client import IncidentDayClient
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN


@pytest.mark.skipif(os.environ.get('WILDFIRE_RUN_OPS_V3_HTTP')!='1',reason='Explicit prepared-data full-day acceptance')
def test_source_http_v3_arrival_feedback_and_full_day():
    folder=Path(os.environ['WILDFIRE_LOCAL_RUNTIME'])/'runtime/es-ma-2021'
    inputs=json.loads((folder/'inputs.json').read_text());network=json.loads((folder/'network.json').read_text())
    opts=dict(expected_inputs_sha256=PACK_PIN,expected_network_sha256=NETWORK_PIN,allow_unapproved_data=True)
    local=IncidentDayEnvV3(inputs,network,**opts)
    adapter=IncidentDayEnvironment(inputs,network,**opts,observation_version='v3')
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',0));listener.listen(128)
        server=uvicorn.Server(uvicorn.Config(create_app(adapter),log_level='error',lifespan='off'))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        try:
            deadline=time.monotonic()+20
            while not server.started:
                assert thread.is_alive() and time.monotonic()<deadline
                time.sleep(.05)
            client=IncidentDayClient(f'http://127.0.0.1:{listener.getsockname()[1]}',timeout=60)
            tasks=client.list_tasks(split='evaluation')
            assert len(tasks['tasks'])==24 and tasks['observation_identity']==observation_identity()
            obs,_=local.reset('incident-day-realmap-v2-001')
            assert client.reset(scenario_id='incident-day-realmap-v2-001')['data']==obs
            options=local.evacuation_options('population-0','destination-0')
            assert client.evacuation_options('population-0','destination-0')==options
            option=options['options'][0]
            action={'protection_orders':[dict(group_id='population-0',action='evacuate',destination_id='destination-0',approach_id=option['id'])]}
            for _ in range(2000):
                obs,reward,terminated,truncated,_=local.step(action)
                result=client.step(action)
                assert result['data']==obs and result['reward']==reward
                assert (result['terminated'],result['truncated'])==(terminated,truncated)
                if local.minute==10:
                    group=next(g for g in obs['districts'] if g['id']=='population-0')
                    assert (group['status'],group['observed_at'],group['received_at'])==('arrived',6,10)
                    wrong={'requests':[dict(resource_id='engine-0',sector_id='incident',approach_id=option['id'])]}
                    check=client.check_action(wrong)
                    assert check==local.check_action(wrong)
                    assert 'requests[0].approach_id' in check['error'] and 'route_options' in check['error']
                if terminated or truncated:break
                action={'advance_minutes':1 if local.minute<10 else 60}
            assert terminated and not truncated and local.minute==1440
            assert local.verify_episode()['passed']
            state=client.get_state()
            assert state['observation_identity']==observation_identity()
            assert state['situation']==local.get_situation()
        finally:
            server.should_exit=True;thread.join(timeout=10)
            assert not thread.is_alive()
