"""Exercise a pinned source-map development case over real loopback HTTP."""
import json
import os
from pathlib import Path
import socket
import threading
import time

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('uvicorn')
pytest.importorskip('wildfire_openenv')

import uvicorn

from eval.incident_day_server import create_app
from envs.wildfire_response.incident_day.real_map_scenarios import NETWORK_PIN, PACK_PIN
from envs.wildfire_response.incident_day.scenarios import (
    REAL_MAP_DEVELOPMENT_CASE_IDS, REAL_MAP_V2_CASE_IDS,
)
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_openenv.incident_day import IncidentDayEnvironment
from wildfire_openenv.incident_day_client import IncidentDayClient


PRIVATE_MARKERS = ('checkpoint', 'sha256', 'outcomes', 'graded_score', '_audit', 'curation')


def assert_public(value):
    # Wake reasons legitimately include 'hourly_checkpoint'; only reject
    # private data fields, not ordinary words in public report values.
    if isinstance(value, dict):
        for key, item in value.items():
            assert not any(marker in key.lower() for marker in PRIVATE_MARKERS), key
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)


@pytest.mark.parametrize('task_index', [0, 2, 3])
def test_source_development_external_client_matches_local_day(task_index):
    root = Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME',
                               '/var/tmp/wildfire-packaged-completion-data-v2'))
    inputs_path = root / 'runtime/es-ma-2021/inputs.json'
    network_path = root / 'runtime/es-ma-2021/network.json'
    if not inputs_path.is_file() or not network_path.is_file():
        pytest.skip('Pinned source-map runtime unavailable')
    inputs = json.loads(inputs_path.read_text())
    network = json.loads(network_path.read_text())
    options = dict(expected_inputs_sha256=PACK_PIN, expected_network_sha256=NETWORK_PIN,
                   allow_unapproved_data=True)
    local = IncidentDayEnv(inputs, network, **options)
    adapter = IncidentDayEnvironment(inputs, network, **options)
    task_id = REAL_MAP_DEVELOPMENT_CASE_IDS[task_index]

    # Pass the bound socket to uvicorn to avoid a port selection race.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(adapter), log_level='error',
                                                lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                assert thread.is_alive(), 'HTTP server exited before startup'
                assert time.monotonic() < deadline, 'HTTP server failed to start'
                time.sleep(0.05)
            client = IncidentDayClient(f'http://127.0.0.1:{port}', timeout=60)
            tasks = client.list_tasks()
            assert [task['id'] for task in tasks['tasks']] == list(
                REAL_MAP_V2_CASE_IDS + REAL_MAP_DEVELOPMENT_CASE_IDS)
            assert tasks['default_scenario_id'] == REAL_MAP_V2_CASE_IDS[0]
            assert tasks['tasks'].count({'id': task_id, 'split': 'development'}) == 1
            assert_public(tasks)

            observation, _ = local.reset(task_id)
            result = client.reset(scenario_id=task_id)
            assert result['data'] == observation
            assert result['data']['task_id'] == task_id
            assert_public(result)
            action = {'advance_minutes': 60}
            assert client.check_action(action) == local.check_action(action)
            reports = client.get_reports()
            assert reports['reports'] == local.get_reports()
            assert_public(reports)

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
                pytest.fail('Source-map episode did not finish within 1000 steps')
            assert terminated and not truncated
            assert observation['minute'] == 1440
            state = client.get_state()
            assert state['state']['minute'] == 1440
            assert state['state']['terminated'] and not state['state']['truncated']
            assert state['situation'] == local.get_situation()
            assert state['trajectory'][-1] == observation
            assert len(state['trajectory']) > 1
            assert_public(state)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'HTTP server failed to shut down'
