"""Version selection contracts with mocked engines; no episode simulation."""
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eval import hub_runtime as hub
from eval import incident_day_server as server
from wildfire_openenv import incident_day as transport


@pytest.fixture
def addon(monkeypatch):
    observation = {'interface_version': 'wildfire-incident-day-ops-v3', 'task_id': 'case', 'minute': 0,
                   'districts': [{'id': 'group', 'position': None, 'field_provenance': {'position': {'basis': 'unknown'}}}],
                   'checkpoint': 'private'}
    engine = SimpleNamespace(inputs={'sha256': 'input'}, network=SimpleNamespace(sha256='network'),
        reset=Mock(return_value=(observation, {'interface_version': 'wildfire-incident-day-ops-v3', '_audit': 'private'})),
        step=Mock(return_value=(dict(observation, minute=1), 0., False, False, {})),
        get_situation=Mock(return_value=observation))
    backend = Mock(return_value=engine)
    identity = Mock(return_value='v3-observation-digest')
    module = SimpleNamespace(IncidentDayEnvV3=backend, observation_identity=identity)
    monkeypatch.setitem(sys.modules, 'wildfire_ops_v3', module)
    return SimpleNamespace(module=module, backend=backend, engine=engine, identity=identity)


def test_adapter_default_stays_v2_without_optional_addon(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wildfire_ops_v3', None)
    backend = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(transport, 'IncidentDayEnv', backend)
    env = transport.IncidentDayEnvironment('inputs', 'roads', expected_inputs_sha256='input', expected_network_sha256='network')
    assert env.core is backend.return_value
    assert env.observation_version == 'v2' and env.observation_identity is None
    backend.assert_called_once_with('inputs', 'roads', expected_inputs_sha256='input',
                                    expected_network_sha256='network', allow_unapproved_data=False)


def test_adapter_v3_is_explicit_and_preserves_public_provenance(addon):
    env = transport.IncidentDayEnvironment('inputs', 'roads', expected_inputs_sha256='input',
        expected_network_sha256='network', observation_version='v3', allow_unapproved_data=True)
    env._select_case = lambda task: 'case'
    result = env.reset(task_id='case')
    assert env.core is addon.engine
    assert result.data['interface_version'] == 'wildfire-incident-day-ops-v3'
    assert result.data['districts'][0]['position'] is None
    assert result.data['districts'][0]['field_provenance']['position']['basis'] == 'unknown'
    assert 'checkpoint' not in result.data and '_audit' not in result.metadata
    assert 'observation_identity' not in result.data and 'observation_identity' not in result.metadata
    assert env.observation_identity == 'v3-observation-digest'
    assert env.step(transport.IncidentDayAction(advance_minutes=1)).data['minute'] == 1


@pytest.mark.parametrize('version', ['', 'V3', 'v4', None, True])
def test_adapter_rejects_unsupported_observation_version(version):
    with pytest.raises(ValueError, match='observation_version'):
        transport.IncidentDayEnvironment({}, {}, expected_inputs_sha256='input',
            expected_network_sha256='network', observation_version=version)


def test_v3_missing_addon_has_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wildfire_ops_v3', None)
    with pytest.raises(ValueError, match='pip install -e envs/wildfire_ops_v3'):
        transport.IncidentDayEnvironment({}, {}, expected_inputs_sha256='input',
            expected_network_sha256='network', observation_version='v3')


@pytest.fixture
def package(tmp_path, monkeypatch):
    payload = {'inputs.json': b'{"fixture":"inputs"}', 'network.json': b'{"fixture":"network"}'}
    files = []
    for name, raw in payload.items():
        (tmp_path / name).write_bytes(raw)
        files.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    entry = dict(inputs='inputs.json', network='network.json', inputs_sha256='input',
                 network_sha256='network', allow_unapproved_data=False, scenario_ids=['case'])
    manifest = dict(files=files, runtimes={'fixture': entry})
    monkeypatch.setattr(hub, 'verify_package', lambda path: copy.deepcopy(manifest))
    return tmp_path, entry


def test_loader_default_v2_and_v3_do_not_rewrite_pinned_entry(package, addon, monkeypatch):
    root, entry = package
    old = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(hub, 'IncidentDayEnv', old)
    v2, original_entry = hub.make_environment(root)
    assert v2 is old.return_value and original_entry == entry
    assert not hasattr(v2, '_runtime_observation_identity')
    v3, new_entry = hub.make_environment(root, observation_version='v3')
    assert v3 is addon.engine and new_entry == original_entry == entry
    assert v3._runtime_observation_identity() == 'v3-observation-digest'
    addon.backend.assert_called_once_with({'fixture': 'inputs'}, {'fixture': 'network'},
        expected_inputs_sha256='input', expected_network_sha256='network', allow_unapproved_data=False)


def test_loader_keeps_approval_gate_with_v3(package, addon, monkeypatch):
    root, entry = package
    original = hub.verify_package(root)
    original['runtimes']['fixture']['allow_unapproved_data'] = True
    monkeypatch.setattr(hub, 'verify_package', lambda path: original)
    with pytest.raises(ValueError, match='explicit allow_unapproved_data'):
        hub.make_environment(root, observation_version='v3')
    addon.backend.assert_not_called()
    hub.make_environment(root, observation_version='v3', allow_unapproved_data=True)
    assert addon.backend.call_args.kwargs['allow_unapproved_data'] is True


def test_loader_missing_addon_does_not_fallback(package, monkeypatch):
    monkeypatch.setitem(sys.modules, 'wildfire_ops_v3', None)
    with pytest.raises(ValueError, match='pip install -e'):
        hub.make_environment(package[0], observation_version='v3')
    with pytest.raises(ValueError, match='observation_version'):
        hub.make_environment(package[0], observation_version='v4')


def fake_finished_env():
    return SimpleNamespace(reset=Mock(return_value=({}, {})), terminated=True, truncated=False, minute=1440,
        verify_episode=Mock(return_value=dict(passed=True, exact_replay=True, independent_reward_agreement=True,
            outcomes=dict(completed_minutes=1440, score_eligible=True, graded_score=.7))))


def test_run_reports_distinguish_v3_and_refuse_changed_identity():
    entry = {'scenario_ids': ['case']}
    legacy = hub.run_episode(fake_finished_env(), entry)
    assert legacy['status'] == 'complete'
    assert 'observation_version' not in legacy and 'observation_identity' not in legacy
    env = fake_finished_env()
    env._runtime_observation_identity = Mock(return_value='v3-digest')
    result = hub.run_episode(env, entry)
    assert result['status'] == 'complete'
    assert result['observation_version'] == 'v3' and result['observation_identity'] == 'v3-digest'
    env._runtime_observation_identity = Mock(side_effect=['before', 'after'])
    changed = hub.run_episode(env, entry)
    assert changed['status'] == 'failed' and changed['score'] is None


def test_server_cli_passes_explicit_v3_and_default_v2(monkeypatch):
    backend = Mock()
    monkeypatch.setattr(server, 'IncidentDayEnvironment', backend)
    monkeypatch.setattr(server, 'fixture_inputs', lambda: ({'sha256': 'input'}, {'sha256': 'network'}))
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=Mock()))
    server.main(['--fixture', '--observation-version', 'v3'])
    assert backend.call_args.kwargs['observation_version'] == 'v3'
    server.main(['--fixture'])
    assert backend.call_args.kwargs['observation_version'] == 'v2'
    with pytest.raises(SystemExit): server.main(['--fixture', '--observation-version', 'v4'])


def test_hub_cli_selects_v3_and_serializes_identity(monkeypatch, capsys):
    env = fake_finished_env()
    env._runtime_observation_identity = lambda: 'v3-digest'
    factory = Mock(return_value=(env, {'scenario_ids': ['case']}))
    monkeypatch.setattr(hub, 'make_environment', factory)
    assert hub.main(['--data-dir', '/unused', '--observation-version', 'v3']) == 0
    assert factory.call_args.kwargs['observation_version'] == 'v3'
    assert json.loads(capsys.readouterr().out)['observation_identity'] == 'v3-digest'


def test_http_v3_evaluator_metadata_and_v2_response_compatibility(addon):
    from fastapi.testclient import TestClient
    env = transport.IncidentDayEnvironment({}, {}, expected_inputs_sha256='input',
        expected_network_sha256='network', observation_version='v3')
    env._select_case = lambda task: 'case'
    env.list_tasks = lambda split: [{'id': 'case', 'split': split}]
    with TestClient(server.create_app(env), base_url='http://127.0.0.1:8005', client=('127.0.0.1', 12345)) as http:
        discovery = http.get('/tasks').json()
        assert discovery['observation_identity'] == 'v3-observation-digest'
        assert discovery['observation_version'] == 'v3'
        assert http.post('/reset', json={'scenario_id': 'case'}).json()['data']['interface_version'] == 'wildfire-incident-day-ops-v3'
        state = http.get('/state').json()
        assert state['observation_identity'] == 'v3-observation-digest'
        assert state['situation']['districts'][0]['position'] is None
        assert 'checkpoint' not in state['situation']
        env.observation_version = 'v2'  # Inspect unchanged evaluator envelope only.
        legacy = http.get('/tasks').json()
        assert set(legacy) == {'tasks', 'default_scenario_id'}
        assert 'observation_identity' not in http.get('/state').json()
