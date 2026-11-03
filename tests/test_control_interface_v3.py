import copy
import json
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_control_v3.control import ControlCenterV3, _fingerprint, interface_digest, tool_definitions_v3
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS


@pytest.fixture
def wrapped():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset('incident-day-egress-v1-000')
    return ControlCenterV3(env)


def test_core_implementation_pin_is_unchanged():
    release = json.loads((Path(__file__).parents[1] / 'data/hub_runtime_v1/release.json').read_text())
    assert implementation_digest() == release['engine_implementation_sha256']


def test_repeated_invalid_checks_do_not_mutate_time_or_block_valid_orders(wrapped):
    env = wrapped.environment
    before = copy.deepcopy(env.get_situation())
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        assert wrapped.execute('check_action', invalid)['valid'] is False
    guard = wrapped.execute('check_action', invalid)
    assert guard['code'] == 'repeated_invalid_draft'
    assert guard['previous_validation']['code'] == 'unknown_resource'
    assert env.minute == 0 and env.get_situation() == before
    situation = wrapped.execute('get_situation', {})
    assert situation.pop('control_interface_version') == wrapped.version
    assert situation == before
    valid = {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter',
                                     'destination_id': None, 'approach_id': None}]}
    assert wrapped.execute('check_action', {'action': valid})['valid'] is True
    committed = wrapped.execute('commit_orders', {'action': valid})
    assert committed['info']['error'] is None and committed['observation']['minute'] == 1
    assert committed['briefing']['reports']['delivered_count'] == committed['observation']['report_count']
    assert wrapped.execute('check_action', invalid).get('code') != 'repeated_invalid_draft'


def test_advance_alias_preserves_existing_wait_and_validates_bounds(wrapped):
    env = wrapped.environment
    assert wrapped.execute('advance', {'max_minutes': 0})['info']['error'] is not None
    # The underlying invalid committed action consumes a minute, exactly as v2 does.
    assert env.minute == 1
    result = wrapped.execute('advance', {'max_minutes': 1})
    assert result['observation']['minute'] == 2
    assert result['briefing']['reports']['delivered_count'] == result['observation']['report_count']
    assert wrapped.execute('advance', {'unexpected': 1})['committed'] is False
    assert env.minute == 2


def test_fresh_route_query_resets_identical_check_guard(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'
    wrapped.execute('route_options', {'resource_id': 'plane-0'})
    assert wrapped.execute('check_action', invalid).get('code') != 'repeated_invalid_draft'


def test_rejected_route_lookup_does_not_reset_identical_check_guard(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    assert wrapped.execute('route_options', {'resource_id': 'missing'})['committed'] is False
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'


def test_unchanged_route_lookup_does_not_reset_identical_check_guard(wrapped):
    query = {'resource_id': 'plane-0'}
    first = wrapped.execute('route_options', query)
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    assert wrapped.execute('route_options', query) == first
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'


def test_non_json_drafts_still_have_a_bounded_check_budget(wrapped):
    invalid = {'action': {'requests': [], 1: []}}
    for _ in range(3):
        assert wrapped.execute('check_action', invalid)['valid'] is False
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'
    changed = {'action': {'releases': [], 2: []}}
    assert wrapped.execute('check_action', changed)['code'] != 'repeated_invalid_draft'
    assert wrapped.environment.minute == 0


def test_tool_contract_and_complete_paginated_public_ledger(wrapped):
    names = [item['function']['name'] for item in tool_definitions_v3()]
    assert len(names) == len(set(names))
    assert {'advance', 'get_briefing', 'get_report_page', 'get_situation'} <= set(names)
    env = wrapped.environment
    for index in range(25):
        env.bus.emit('test', 'message', {'text': f'public-{index}'}, 0, delay=0)
    env.bus.tick(0, {})
    before = copy.deepcopy(env.checkpoint())
    first = wrapped.execute('get_report_page', {'after_cursor': 0, 'limit': 20})
    second = wrapped.execute('get_report_page', {'after_cursor': first['next_cursor'], 'limit': 20})
    reports = first['reports'] + second['reports']
    assert first['has_more'] and not second['has_more']
    assert len(reports) == first['total'] == second['total'] == env.get_situation()['report_count']
    assert reports == env.bus.reports
    page = wrapped.execute('get_briefing', {})
    assert page['reports']['delivered_count'] == len(reports)
    assert env.checkpoint() == before
    assert wrapped.execute('get_report_page', {'after_cursor': True})['committed'] is False


def test_cross_kind_check_and_commit_explain_error_without_hidden_data(wrapped):
    env = wrapped.environment
    option = wrapped.execute('evacuation_options', {'group_id': 'population-0',
                                                   'destination_id': 'destination-0'})['options'][0]['id']
    action = {'requests': [{'resource_id': 'crew-0', 'sector_id': 'incident', 'approach_id': option}]}
    draft = wrapped.execute('check_action', {'action': action})
    assert draft['code'] == 'invalid_resource_approach'
    assert draft['field_path'] == 'requests[0].approach_id'
    assert draft['next_tool'] == 'route_options'
    assert "'resource_id'" not in draft['error']
    assert env.minute == 0
    result = wrapped.execute('commit_orders', {'action': action})
    assert result['info']['validation'] == draft
    assert result['info']['error'] == draft['error']
    assert result['observation']['minute'] == 1
    assert result['observation']['pending_resource_requests'] == []


def test_briefing_failure_does_not_hide_an_executed_action(wrapped, monkeypatch):
    def broken():
        raise RuntimeError('private failure details')
    monkeypatch.setattr(wrapped, '_briefing', broken)
    result = wrapped.execute('advance', {'max_minutes': 1})
    assert result['observation']['minute'] == 1
    assert result['briefing_error'].startswith('Briefing unavailable')
    assert 'private failure details' not in str(result)


def test_execution_failure_is_indeterminate_and_sanitized(wrapped, monkeypatch):
    def fail_after_mutation(action):
        wrapped.environment.minute += 1
        raise RuntimeError('PRIVATE execution detail')
    monkeypatch.setattr(wrapped.environment, 'step', fail_after_mutation)
    result = wrapped.execute('commit_orders', {'action': {}})
    assert wrapped.environment.minute == 1
    assert result['execution'] == 'indeterminate'
    assert 'committed' not in result and 'PRIVATE' not in str(result)


def test_invalid_tool_envelope_is_rejected_before_dispatch(wrapped, monkeypatch):
    def must_not_run(action):
        raise AssertionError('should not dispatch')
    monkeypatch.setattr(wrapped.environment, 'step', must_not_run)
    result = wrapped.execute('commit_orders', {'action': {}, 'unexpected': True})
    assert result['committed'] is False and 'indeterminate' not in str(result)
    assert wrapped.environment.minute == 0


def test_read_only_tool_errors_do_not_expose_exception_details(wrapped, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE hidden detail')
    monkeypatch.setattr(wrapped.environment, 'check_action', fail)
    result = wrapped.execute('check_action', {'action': {}})
    assert result['code'] == 'check_unavailable' and 'PRIVATE' not in str(result)
    monkeypatch.setattr(wrapped.environment, 'route_options', fail)
    result = wrapped.execute('route_options', {'resource_id': 'crew-0'})
    assert result['committed'] is False and 'PRIVATE' not in str(result)
    monkeypatch.setattr(wrapped.environment, 'get_reports', fail)
    result = wrapped.execute('get_report_page', {})
    assert result['committed'] is False and 'PRIVATE' not in str(result)
    def fail_os(*args, **kwargs):
        raise OSError('PRIVATE operating-system detail')
    monkeypatch.setattr(wrapped.environment, 'get_reports', fail_os)
    result = wrapped.execute('get_report_page', {})
    assert result['committed'] is False and 'PRIVATE' not in str(result)


def test_terminal_reward_requires_pinned_driver_verification(wrapped):
    identity = {key: wrapped.environment.trajectory()[key] for key in IDENTITY_FIELDS}
    interface_pin = interface_digest()
    for _ in range(3):
        result = wrapped.execute('commit_orders', {'action': {'unknown': []}})
    assert result['truncated'] is True
    assert result['reward'] is None and result['verification_required'] is True
    assert 'outcomes' not in result['info']
    assert wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)['status'] == 'invalid_abandonment'
    assert wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)['score'] == 0.0
    assert wrapped.finalize(expected_identity=identity, expected_interface_sha256='bad')['status'] == 'identity_mismatch'
    assert wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin, infrastructure_timeout=True)['score'] is None
    identity['seed'] += 1
    assert wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)['status'] == 'identity_mismatch'


def test_interface_change_during_verification_is_not_scored(wrapped, monkeypatch):
    import wildfire_control_v3.control as module
    identity = {key: wrapped.environment.trajectory()[key] for key in IDENTITY_FIELDS}
    interface_pin = interface_digest()
    for _ in range(3):
        wrapped.execute('commit_orders', {'action': {'unknown': []}})
    values = iter((interface_pin, '0' * 64))
    monkeypatch.setattr(module, 'interface_digest', lambda: next(values))
    result = wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)
    assert result['status'] == 'identity_mismatch' and result['score'] is None


def test_preimported_helper_changed_before_control_import_cannot_be_scored():
    script = r'''
from pathlib import Path
from unittest.mock import patch
import wildfire_control_v3.verifier_v3
original = Path.read_bytes
def updated(path):
    data = original(path)
    return data + b'\n# changed on disk' if path.name == 'verifier_v3.py' else data
with patch.object(Path, 'read_bytes', updated):
    import wildfire_control_v3.control as control
    wrapper = control.ControlCenterV3(object())
    result = wrapper.finalize(expected_identity={}, expected_interface_sha256=control.interface_digest())
    assert result['status'] == 'identity_mismatch' and result['score'] is None
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stale_timestamp_bytecode_cannot_be_scored(tmp_path):
    root = Path(__file__).parents[1]
    source = root/'envs/wildfire_control_v3/src/wildfire_control_v3'
    package = tmp_path/'wildfire_control_v3'
    shutil.copytree(source, package, ignore=shutil.ignore_patterns('__pycache__'))
    target = package/'verifier_v3.py'
    old = target.read_bytes()
    replacement = old.replace(b"wildfire-incident-day-verifier-v3", b"wildfire-incident-day-verifier-v4")
    assert replacement != old and len(replacement) == len(old)
    original_stat = target.stat()
    py_compile.compile(str(target), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    target.write_bytes(replacement)
    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    script = r'''
import wildfire_control_v3.verifier_v3 as verifier
assert verifier.VERSION.endswith('v3')  # stale bytecode really was executed
assert verifier._LOADED_CODE_MATCH is False
from wildfire_control_v3.control import ControlCenterV3, interface_digest
result = ControlCenterV3(object()).finalize(expected_identity={}, expected_interface_sha256=interface_digest())
assert result['status'] == 'identity_mismatch' and result['score'] is None
'''
    environment = os.environ.copy()
    environment['PYTHONPATH'] = os.pathsep.join((str(tmp_path),
        str(root/'envs/wildfire_response/core/src'), environment.get('PYTHONPATH', '')))
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=environment,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_private_checkpoint_restores_guard_and_seeded_continuation(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    checkpoint = wrapped.private_checkpoint()
    assert 'engine_checkpoint' in checkpoint
    assert not {'private_checkpoint', 'restore_private_checkpoint', 'finalize'} & {
        item['function']['name'] for item in tool_definitions_v3()}
    inputs, network = fixture_inputs()
    restored = ControlCenterV3(IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                              expected_network_sha256=network['sha256']))
    restored.restore_private_checkpoint(checkpoint)
    assert restored.execute('check_action', invalid) == wrapped.execute('check_action', invalid)
    assert restored.execute('get_briefing', {}) == wrapped.execute('get_briefing', {})
    assert restored.execute('advance', {'max_minutes': 5}) == wrapped.execute('advance', {'max_minutes': 5})
    tampered = copy.deepcopy(checkpoint)
    tampered['repetitions'] = 0
    with pytest.raises(ValueError, match='integrity'):
        restored.restore_private_checkpoint(tampered)


def test_reset_clears_check_guard(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    for _ in range(3):
        wrapped.execute('check_action', invalid)
    assert wrapped.execute('check_action', invalid)['code'] == 'repeated_invalid_draft'
    observation, info = wrapped.reset('incident-day-egress-v1-000')
    assert info['briefing']['reports']['delivered_count'] == observation['report_count']
    assert wrapped.execute('check_action', invalid).get('code') != 'repeated_invalid_draft'


def test_failed_semantic_restore_does_not_replace_live_episode(wrapped):
    invalid = {'action': {'releases': ['unknown-unit']}}
    wrapped.execute('check_action', invalid)
    checkpoint = wrapped.private_checkpoint()
    checkpoint['last_failed_check'][0] = '0' * 64
    checkpoint['sha256'] = _fingerprint({k: v for k, v in checkpoint.items() if k != 'sha256'}).hex()
    wrapped.execute('advance', {'max_minutes': 1})
    original = wrapped.environment
    before = wrapped.private_checkpoint()
    with pytest.raises(ValueError, match='public state'):
        wrapped.restore_private_checkpoint(checkpoint)
    assert wrapped.environment is original
    assert wrapped.private_checkpoint() == before


def test_full_fixture_day_matches_v2_and_independently_verifies(wrapped):
    inputs, network = fixture_inputs()
    baseline = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                              expected_network_sha256=network['sha256'])
    initial, _ = baseline.reset('incident-day-egress-v1-000')
    assert initial == wrapped.environment.get_situation()
    identity = {key: baseline.trajectory()[key] for key in IDENTITY_FIELDS}
    interface_pin = interface_digest()
    while not baseline.terminated and not baseline.truncated:
        observation, reward, terminated, truncated, info = baseline.advance_time(60)
        result = wrapped.execute('advance', {})
        assert result['observation'] == observation
        assert result['terminated'] == terminated and result['truncated'] == truncated
        assert result['briefing']['reports']['delivered_count'] == observation['report_count']
        assert {k: v for k, v in info.items() if k != 'outcomes'} == result['info']
        if not (terminated or truncated):
            assert result['reward'] == reward
    assert baseline.terminated and baseline.minute == 1440
    final = wrapped.finalize(expected_identity=identity, expected_interface_sha256=interface_pin)
    assert final['status'] == 'full_day' and final['verified'] is True
    assert final['score'] == pytest.approx(baseline.verify_episode()['outcomes']['graded_score'])
