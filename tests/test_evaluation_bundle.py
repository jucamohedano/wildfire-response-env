"""Fixture-sized tests for opt-in, evaluation-only packaging and loading."""
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eval import build_completion_bundle as builder, hub_runtime as hub
from test_hub_runtime import package  # noqa: F401


@pytest.fixture
def evaluation_source(package, monkeypatch, tmp_path):
    source, manifest = package
    entry = manifest['runtimes']['fixture']
    catalogs = hub.registry()
    ids = [f'incident-day-evaluation-v1-{i:03}' for i in range(24)]
    catalogs['catalogs'].append(dict(id='incident_day_evaluation_v1', status='active_evaluation',
        case_ids=ids, inputs_sha256=entry['inputs_sha256'], network_sha256=entry['network_sha256'],
        generation_seed=20261102))
    monkeypatch.setattr(builder, 'registry', lambda: catalogs)
    monkeypatch.setattr(hub, 'registry', lambda: catalogs)
    class TinyEnvironment:
        def __init__(self, *args, **kwargs): pass
        def reset(self, case_id):
            index = ids.index(case_id) if case_id in ids else 0
            self._scenario = dict(sha256=hashlib.sha256(case_id.encode()).hexdigest(), seed=42, evaluation=dict(
                configuration_group=f'evaluation-v1-family-{index // 4}', generation_seed=20261102,
                parent_id=entry['scenario_ids'][0]))
    monkeypatch.setattr(builder, 'IncidentDayEnv', TinyEnvironment)
    lock = tmp_path.parent / (tmp_path.name + '-evaluation-lock.json')
    lock.write_text(json.dumps({'manifest_sha256': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest()}))
    monkeypatch.setattr(builder, 'LOCK', lock)
    return source, ids


def output_for(source, suffix):
    return source.parent / (source.name + suffix)


def test_default_does_not_include_evaluation(evaluation_source):
    source, _ = evaluation_source
    output = output_for(source, '-development')
    result = builder.build(source, output)
    assert 'evaluation_scenario_ids' not in result['runtimes']['fixture']
    assert not (output / 'tasks/evaluation.parquet').exists()
    assert 'evaluation' not in result['tables']
    assert 'evaluation_tasks' not in result['counts']


def test_opt_in_has_exact_catalog_and_configuration_metadata(evaluation_source):
    source, ids = evaluation_source
    before = (source / 'manifest.json').read_bytes()
    output = output_for(source, '-evaluation')
    result = builder.build(source, output, include_evaluation=True)
    assert hub.verify_package(output) == result
    assert result['runtimes']['fixture']['evaluation_scenario_ids'] == ids
    rows = pq.read_table(output / 'tasks/evaluation.parquet').to_pylist()
    assert len(rows) == result['counts']['evaluation_tasks'] == 24
    assert result['counts']['tasks'] == result['counts']['development_tasks'] + 24
    assert {row['task_id'] for row in rows} == set(ids)
    assert all(row['split'] == 'evaluation' and row['training_eligible'] is False for row in rows)
    assert {row['generation_seed'] for row in rows} == {20261102}
    assert {row['episode_seed'] for row in rows} == {42}
    from eval.evaluate_packaged_runtime import evaluation_rows
    frozen = evaluation_rows(output, result)
    assert set(frozen) == {('fixture', identifier) for identifier in ids}
    assert frozen[('fixture', ids[0])]['episode_seed'] == 42
    groups = {row['configuration_group'] for row in rows}
    assert len(groups) == 6
    assert all(sum(row['configuration_group'] == group for row in rows) == 4 for group in groups)
    assert all(row['related_exercise_group'] == row['configuration_group'] for row in rows)
    assert (source / 'manifest.json').read_bytes() == before


@pytest.mark.parametrize('mutation', ['duplicate_row', 'training', 'seed', 'split', 'group', 'parent'])
def test_loader_rejects_semantically_corrupt_evaluation_rows(evaluation_source, mutation):
    source, _ = evaluation_source
    output = output_for(source, '-corrupt-' + mutation)
    manifest = builder.build(source, output, include_evaluation=True)
    table = output / 'tasks/evaluation.parquet'
    rows = pq.read_table(table).to_pylist()
    if mutation == 'duplicate_row': rows[0] = dict(rows[1])
    elif mutation == 'training': rows[0]['training_eligible'] = True
    elif mutation == 'seed': rows[0]['generation_seed'] += 1
    elif mutation == 'split': rows[0]['split'] = 'development'
    elif mutation == 'group': rows[0]['configuration_group'] = 'unrelated'
    elif mutation == 'parent': rows[0]['parent_scenario_id'] = 'invented'
    pq.write_table(pa.Table.from_pylist(rows), table)
    raw = table.read_bytes()
    entry = next(item for item in manifest['files'] if item['path'] == 'tasks/evaluation.parquet')
    entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (output / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='[Ee]valuation'):
        hub.verify_package(output)


@pytest.mark.parametrize('mutation', ['invented', 'duplicate', 'missing', 'wrong_runtime_pin'])
def test_loader_rejects_wrong_evaluation_catalog(evaluation_source, mutation):
    source, _ = evaluation_source
    output = output_for(source, '-ids-' + mutation)
    manifest = builder.build(source, output, include_evaluation=True)
    runtime = manifest['runtimes']['fixture']
    if mutation == 'invented': runtime['evaluation_scenario_ids'][0] = 'invented'
    elif mutation == 'duplicate': runtime['evaluation_scenario_ids'][0] = runtime['evaluation_scenario_ids'][1]
    elif mutation == 'missing': runtime['evaluation_scenario_ids'].pop()
    else: runtime['network_sha256'] = '0' * 64
    (output / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): hub.verify_package(output)


def test_expected_code_rejected_before_output(evaluation_source):
    source, _ = evaluation_source
    output = output_for(source, '-wrong-code')
    with pytest.raises(ValueError, match='Expected implementation'):
        builder.build(source, output, include_evaluation=True, expected_implementation_sha256='0' * 64)
    assert not output.exists()


def test_run_episode_accepts_declared_evaluation_id(monkeypatch):
    class Sentinel:
        def reset(self, identifier):
            assert identifier == 'evaluation-case'
            raise RuntimeError('Reached evaluation reset')
    with pytest.raises(RuntimeError, match='Reached evaluation reset'):
        hub.run_episode(Sentinel(), {'scenario_ids': ['dev'], 'evaluation_scenario_ids': ['evaluation-case']},
                        case_id='evaluation-case', wall_seconds=1)
