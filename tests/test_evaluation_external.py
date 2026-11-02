"""Opt-in full-day evaluation HTTP check; enable only after code/data freeze."""
import json
import os
from pathlib import Path
import socket
import threading
import time

import pytest

pytest.importorskip('uvicorn')
import uvicorn

from eval.incident_day_server import create_app
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
from envs.wildfire_response.incident_day.scenarios import EVALUATION_CASE_IDS
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_openenv.incident_day import IncidentDayEnvironment
from wildfire_openenv.incident_day_client import IncidentDayClient


def assert_public(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'task_id':
                assert item == 'incident-day-evaluation-v1'
            assert not any(marker in key.lower() for marker in
                           ('checkpoint', 'sha256', 'outcomes', 'graded_score', '_audit', 'curation', 'seed'))
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)


@pytest.mark.skipif(os.environ.get('WILDFIRE_RUN_EVALUATION_HTTP') != '1',
                    reason='Enable only after code and evaluation manifests are frozen')
def test_evaluation_external_client_reset_and_full_day_match_local():
    configured = os.environ.get('WILDFIRE_LOCAL_RUNTIME')
    if not configured:
        pytest.fail('Set WILDFIRE_LOCAL_RUNTIME to the frozen evaluation package')
    root = Path(configured) / 'runtime/es-ma-2021'
    inputs = json.loads((root / 'inputs.json').read_text())
    network = json.loads((root / 'network.json').read_text())
    options = dict(expected_inputs_sha256=PACK_PIN, expected_network_sha256=NETWORK_PIN,
                   allow_unapproved_data=True)
    local = IncidentDayEnv(inputs, network, **options)
    adapter = IncidentDayEnvironment(inputs, network, **options)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(adapter), log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                assert thread.is_alive(), 'Loopback server exited before startup'
                assert time.monotonic() < deadline, 'Loopback server failed to start'
                time.sleep(.05)
            client = IncidentDayClient(f'http://127.0.0.1:{port}', timeout=60)
            development = client.list_tasks()
            discovered = client.list_tasks(split='evaluation')
            assert discovered == {'tasks': [dict(id=identifier, split='evaluation') for identifier in EVALUATION_CASE_IDS],
                                  'default_scenario_id': None}
            assert_public(discovered)
            task_id = discovered['tasks'][0]['id']
            observation, _ = local.reset(task_id)
            result = client.reset(scenario_id=task_id)
            assert result['data'] == observation
            assert result['data']['task_id'] == 'incident-day-evaluation-v1'
            assert_public(result)
            action = {'advance_minutes': 60}
            for _ in range(1000):
                observation, reward, terminated, truncated, _ = local.step(action)
                result = client.step(action)
                assert result['data'] == observation
                assert result['reward'] == pytest.approx(reward)
                assert (result['terminated'], result['truncated']) == (terminated, truncated)
                assert result['done'] == (terminated or truncated)
                assert_public(result)
                if terminated or truncated:
                    break
            else:
                pytest.fail('Evaluation day did not finish within 1000 actions')
            assert terminated and not truncated
            assert observation['minute'] == 1440
            state = client.get_state()
            assert state['state']['minute'] == 1440
            assert state['state']['terminated'] and not state['state']['truncated']
            assert state['situation'] == local.get_situation()
            assert state['trajectory'][-1] == observation
            assert len(state['trajectory']) > 1
            assert_public(state)
            assert client.list_tasks() == development
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'Loopback server failed to shut down'
