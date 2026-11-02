"""Split discovery and selection checks; no scenario compilation or simulation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wildfire_openenv import incident_day as adapter
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN


@pytest.fixture
def evaluation_catalog():
    return dict(id='incident_day_evaluation_v1', status='active_evaluation',
                inputs_sha256=PACK_PIN, network_sha256=NETWORK_PIN,
                case_ids=['evaluation-a', 'evaluation-b'], seed='private', curation='private')


def environment(inputs_pin=PACK_PIN, network_pin=NETWORK_PIN, *, archived=False):
    env = adapter.IncidentDayEnvironment.__new__(adapter.IncidentDayEnvironment)
    env.core = SimpleNamespace(inputs={'sha256': inputs_pin}, network=SimpleNamespace(sha256=network_pin))
    env.allow_archived_scenarios = archived
    return env


def test_evaluation_discovery_is_separate_and_opaque(monkeypatch, evaluation_catalog):
    monkeypatch.setattr(adapter, 'registry', lambda: {'catalogs': [evaluation_catalog]})
    env = environment()
    assert env.list_splits() == ['development', 'evaluation']
    assert env.list_tasks('evaluation') == [dict(id=identifier, split='evaluation')
                                            for identifier in evaluation_catalog['case_ids']]
    before = copy.deepcopy(evaluation_catalog)
    result = env.list_tasks('evaluation')
    result[0]['id'] = 'changed'
    assert evaluation_catalog == before
    public = json.dumps(env.list_tasks('evaluation'))
    for private in ('sha256', 'curation', 'seed', 'private'):
        assert private not in public
    assert not set(evaluation_catalog['case_ids']) & {t['id'] for t in env.list_tasks('development')}
    with pytest.raises(ValueError, match='Unknown split'):
        env.list_tasks('train')


@pytest.mark.parametrize('inputs_pin,network_pin', [
    ('wrong', NETWORK_PIN), (PACK_PIN, 'wrong'), ('wrong', 'wrong')])
def test_evaluation_requires_both_exact_pins(monkeypatch, evaluation_catalog, inputs_pin, network_pin):
    monkeypatch.setattr(adapter, 'registry', lambda: {'catalogs': [evaluation_catalog]})
    assert environment(inputs_pin, network_pin, archived=True).list_tasks('evaluation') == []


@pytest.mark.parametrize('change', ['status', 'id', 'missing'])
def test_inactive_or_unrelated_catalog_does_not_advertise_evaluation(monkeypatch, evaluation_catalog, change):
    if change == 'status': evaluation_catalog['status'] = 'active_development'
    if change == 'id': evaluation_catalog['id'] = 'other'
    monkeypatch.setattr(adapter, 'registry', lambda: {'catalogs': [] if change == 'missing' else [evaluation_catalog]})
    assert environment().list_tasks('evaluation') == []


def test_archival_discovery_cannot_relabel_evaluation_as_development(monkeypatch, evaluation_catalog):
    monkeypatch.setattr(adapter, 'registry', lambda: {'catalogs': [evaluation_catalog]})
    monkeypatch.setattr(adapter, 'ALL_CASE_IDS', ('old-case', *evaluation_catalog['case_ids']))
    env = environment(archived=True)
    # Even when every ID is selectable, the splits remain disjoint.
    env._select_case = lambda identifier: identifier
    assert env.list_tasks('development') == [dict(id='old-case', split='development')]
    assert len(env.list_tasks('evaluation')) == 2


def test_reset_still_uses_core_selection_and_public_allowlists(monkeypatch):
    env = environment()
    selector = Mock(return_value='selected-evaluation')
    monkeypatch.setattr(adapter, 'select_day_case_id', selector)
    env.core.reset = Mock(return_value=({'task_id': 'selected-evaluation', 'minute': 0,
                                        'checkpoint': 'private'}, {'sha256': 'private'}))
    observed = env.reset(task_id='requested-evaluation')
    selector.assert_called_once_with('requested-evaluation', PACK_PIN, NETWORK_PIN, allow_archived=False)
    env.core.reset.assert_called_once_with('selected-evaluation', None)
    assert observed.data == {'task_id': 'selected-evaluation', 'minute': 0}
    assert observed.metadata == {}


def test_real_catalog_default_archives_and_manifest_are_consistent():
    from envs.wildfire_response.incident_day.scenarios import EVALUATION_CASE_IDS, REAL_MAP_DEVELOPMENT_CASE_IDS
    from wildfire_core.catalog_lifecycle import registry, ARCHIVED_DAY_CASE_IDS
    from wildfire_core.incident_day import REAL_MAP_V2_CASE_IDS

    env = environment()
    expected = [dict(id=identifier, split='evaluation') for identifier in EVALUATION_CASE_IDS]
    assert len(expected) == 24
    assert env.list_tasks('evaluation') == expected
    assert [task['id'] for task in env.list_tasks('development')] == list(
        REAL_MAP_V2_CASE_IDS + REAL_MAP_DEVELOPMENT_CASE_IDS)
    assert env._select_case(None) == REAL_MAP_V2_CASE_IDS[0]
    for identifier in EVALUATION_CASE_IDS:
        assert env._select_case(identifier) == identifier
    env.allow_archived_scenarios = True
    assert env.list_tasks('evaluation') == expected
    assert not set(EVALUATION_CASE_IDS) & {task['id'] for task in env.list_tasks('development')}
    assert not set(EVALUATION_CASE_IDS) & set(ARCHIVED_DAY_CASE_IDS)
    catalog = next(c for c in registry()['catalogs'] if c['id'] == 'incident_day_evaluation_v1')
    assert catalog['status'] == 'active_evaluation'
    assert catalog['case_ids'] == list(EVALUATION_CASE_IDS)
    manifest = json.loads((Path(__file__).resolve().parents[1] /
        'envs/wildfire_response/openenv/incident_day_manifest.json').read_text())
    assert manifest['splits'] == env.list_splits()
    assert manifest['active_evaluation_scenario_ids'] == list(EVALUATION_CASE_IDS)
    assert manifest['evaluation_catalog_id'] == catalog['id']
    assert manifest['rest_task_discovery_split'] == 'development'
    assert manifest['training_corpus_available'] is False


@pytest.mark.parametrize('inputs_pin,network_pin', [
    ('wrong', NETWORK_PIN), (PACK_PIN, 'wrong'), ('wrong', 'wrong')])
def test_real_evaluation_selection_rejects_mismatched_pins(inputs_pin, network_pin):
    from envs.wildfire_response.incident_day.scenarios import EVALUATION_CASE_IDS
    env = environment(inputs_pin, network_pin, archived=True)
    assert env.list_tasks('evaluation') == []
    with pytest.raises(ValueError, match='matching input'):
        env._select_case(EVALUATION_CASE_IDS[0])


def test_fixture_discovery_remains_development_only_without_reset():
    from wildfire_core.incident_day import fixture_inputs, COVERAGE_CASE_IDS
    inputs, network = fixture_inputs()
    env = environment(inputs['sha256'], network['sha256'])
    assert env.list_tasks('evaluation') == []
    assert env.list_tasks('development') == [dict(id=identifier, split='development')
                                            for identifier in COVERAGE_CASE_IDS]
    assert env._select_case(None) == COVERAGE_CASE_IDS[0]
    env.allow_archived_scenarios = True
    assert env.list_tasks('evaluation') == []
