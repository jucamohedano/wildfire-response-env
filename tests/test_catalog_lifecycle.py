import contextlib
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wildfire_core.catalog_lifecycle import (
    ACTIVE_FIXTURE_CASE_IDS, ACTIVE_REAL_MAP_CASE_IDS, ARCHIVED_DAY_CASE_IDS,
    active_day_case_ids, default_day_case_id, registry, require_archived_opt_in,
    select_day_case_id,
)
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


def pins():
    inputs, network = fixture_inputs()
    return inputs, network, (inputs['sha256'], network['sha256'])


def test_active_defaults_are_corrected_and_archives_cannot_enter_discovery():
    _, _, hashes = pins()
    assert active_day_case_ids(*hashes) == ACTIVE_FIXTURE_CASE_IDS
    assert len(ACTIVE_FIXTURE_CASE_IDS) == len(ACTIVE_REAL_MAP_CASE_IDS) == 6
    assert len(ARCHIVED_DAY_CASE_IDS) == 20
    assert not set(ARCHIVED_DAY_CASE_IDS) & set(ACTIVE_FIXTURE_CASE_IDS + ACTIVE_REAL_MAP_CASE_IDS)
    assert default_day_case_id(*hashes) == 'incident-day-egress-v1-000'
    with pytest.raises(ValueError, match='Archived'):
        select_day_case_id('incident-day-dev-000', *hashes)
    assert select_day_case_id('incident-day-dev-000', *hashes, allow_archived=True) == 'incident-day-dev-000'


def test_pack_context_is_exact_and_archive_ack_does_not_make_unknown_packs_active():
    from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
    assert active_day_case_ids(PACK_PIN, NETWORK_PIN) == ACTIVE_REAL_MAP_CASE_IDS
    for hashes in ((PACK_PIN, '0'*64), ('0'*64, NETWORK_PIN), ('0'*64, '0'*64)):
        with pytest.raises(ValueError, match='No active'):
            active_day_case_ids(*hashes)
        with pytest.raises(ValueError, match='No active'):
            select_day_case_id(None, *hashes, allow_archived=True)


def test_core_no_id_uses_corrected_fixture_but_explicit_old_id_is_replayable():
    inputs, network, hashes = pins()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=hashes[0], expected_network_sha256=hashes[1])
    observation, _ = env.reset()
    assert observation['task_id'] == ACTIVE_FIXTURE_CASE_IDS[0]
    observation, _ = env.reset('incident-day-dev-000')
    assert observation['task_id'] == 'incident-day-dev-000'


def test_registry_seal_and_tracked_snapshot_match_and_cannot_be_mutated():
    snapshot = json.loads(Path('data/catalog_lifecycle_v1/registry.json').read_text())
    value = registry()
    assert value == snapshot
    seal = value.pop('sha256')
    assert seal == hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    value['catalogs'][0]['case_ids'].clear()
    assert len(registry()['catalogs'][0]['case_ids']) == 6
    assert registry()['training_corpus_available'] is False


@pytest.mark.parametrize('command', ['export-demo', 'import-trajectory', 'freeze-bank', 'sft', 'grpo', 'validate'])
def test_old_learning_cli_stops_before_loading_config_model_or_data(command):
    from train import benchmark
    with patch.object(benchmark, 'make_factory', side_effect=AssertionError('model/data not permitted')), contextlib.redirect_stderr(io.StringIO()):
        with pytest.raises(SystemExit):
            benchmark.main([command, '--config', '/nonexistent-config'])


def test_archive_optin_never_implies_spending_or_release_permission():
    with pytest.raises(ValueError, match='does not authorize GPU spending or publication'):
        require_archived_opt_in('benchmark_v1')
    assert require_archived_opt_in('benchmark_v1', allow_archived=True) is None
    with pytest.raises(TypeError):
        require_archived_opt_in('benchmark_v1', allow_archived='true')


def test_archival_mode_cannot_bypass_real_map_pin_identity():
    _, _, hashes = pins()
    with pytest.raises(ValueError, match='matching input'):
        select_day_case_id('incident-day-realmap-v1-000', *hashes, allow_archived=True)


def test_archival_quality_cli_refuses_before_input_io():
    from eval import validate_scenarios
    arguments = ['audit', '--prepared-inputs', '/missing', '--prepared-sha256', '0'*64,
                 '--topographic-inputs', '/missing', '--topographic-sha256', '0'*64, '--output', '/missing']
    with patch('sys.argv', arguments), patch.object(validate_scenarios, 'load_inputs',
            side_effect=AssertionError('must stop before data IO')), contextlib.redirect_stderr(io.StringIO()):
        with pytest.raises(SystemExit):
            validate_scenarios.main()


def test_packaged_pilot_and_root_wrapper_share_archival_guard_before_run():
    from envs.wildfire_response.regional.training import pilot
    from train import grpo
    assert grpo.main is pilot.main
    args = ['pilot', '--output', '/tmp/must-not-create-model-output']
    with patch('sys.argv', args), patch.object(pilot, 'run', side_effect=AssertionError('must not start model')), contextlib.redirect_stderr(io.StringIO()):
        with pytest.raises(SystemExit):
            pilot.main()
    with patch('sys.argv', [*args, '--allow-archived-catalog']), patch.object(pilot, 'run', return_value={'archival': True}) as run, contextlib.redirect_stdout(io.StringIO()):
        pilot.main()
        run.assert_called_once()


def test_transport_manifest_matches_active_and_archived_registry_partitions():
    manifest = json.loads(Path('envs/wildfire_response/openenv/incident_day_manifest.json').read_text())
    assert manifest['active_fixture_scenario_ids'] == list(ACTIVE_FIXTURE_CASE_IDS)
    assert manifest['active_real_map_scenario_ids'] == list(ACTIVE_REAL_MAP_CASE_IDS)
    assert manifest['archived_scenario_ids'] == list(ARCHIVED_DAY_CASE_IDS)
    assert manifest['training_corpus_available'] is False
