import hashlib
import json

import pyarrow.parquet as pq
import pytest

from eval import build_completion_bundle as builder
from eval import hub_runtime
from test_hub_runtime import package  # noqa: F401


def pin_source(monkeypatch, tmp_path, source):
    lock = tmp_path.parent / (tmp_path.name + '-release-lock.json')
    lock.write_text(json.dumps({'manifest_sha256': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest()}))
    monkeypatch.setattr(builder, 'LOCK', lock)


def test_builds_verified_bundle_without_changing_source(package, tmp_path, monkeypatch):
    source, _ = package
    pin_source(monkeypatch, tmp_path, source)
    before = (source / 'manifest.json').read_bytes()
    output = tmp_path.parent / (tmp_path.name + '-bundle')
    result = builder.build(source, output)
    assert (source / 'manifest.json').read_bytes() == before
    assert hub_runtime.verify_package(output) == result
    assert len(pq.read_table(output / 'tasks/development.parquet')) == len(result['runtimes']['fixture']['scenario_ids'])
    assert result['training_corpus_available'] is False


def test_rejects_symlinked_output_inside_source(package, tmp_path, monkeypatch):
    source, _ = package
    pin_source(monkeypatch, tmp_path, source)
    alias = tmp_path.parent / (tmp_path.name + '-alias')
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match='outside the source'):
        builder.build(source, alias / 'nested')
    assert not (source / 'nested').exists()


def test_refuses_modified_source_without_output(package, tmp_path, monkeypatch):
    source, _ = package
    pin_source(monkeypatch, tmp_path, source)
    (source / 'tasks/development.parquet').write_bytes(b'tampered')
    output = tmp_path.parent / (tmp_path.name + '-bad-bundle')
    with pytest.raises(ValueError, match='inventory mismatch'):
        builder.build(source, output)
    assert not output.exists()


def test_loader_requires_exact_catalog_for_additional_ids(package, monkeypatch):
    source, manifest = package
    entry = manifest['runtimes']['fixture']
    catalog = {'id': 'incident_day_realmap_development_v3', 'status': 'active_development',
               'inputs_sha256': entry['inputs_sha256'], 'network_sha256': entry['network_sha256'],
               'case_ids': ['new-1', 'new-2']}
    existing = hub_runtime.registry()
    existing['catalogs'].append(catalog)
    monkeypatch.setattr(hub_runtime, 'registry', lambda: existing)
    # The lifecycle digest remains the expected digest in this controlled fixture.
    entry['additional_scenario_ids'] = ['new-1', 'new-2']
    (source / 'manifest.json').write_text(json.dumps(manifest))
    assert hub_runtime.verify_package(source)['runtimes']['fixture']['additional_scenario_ids'] == ['new-1', 'new-2']
    entry['additional_scenario_ids'] = ['new-1', 'invented']
    (source / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Additional scenario IDs'):
        hub_runtime.verify_package(source)


def test_refuses_unlisted_source_file(package, tmp_path, monkeypatch):
    source, _ = package
    pin_source(monkeypatch, tmp_path, source)
    (source / 'unexpected.bin').write_bytes(b'unlisted')
    output = tmp_path.parent / (tmp_path.name + '-unlisted-bundle')
    with pytest.raises(ValueError, match='inventory mismatch'):
        builder.build(source, output)
    assert not output.exists()


def test_real_source_bundle_advertises_revised_twelve(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from eval.validate_source_runtime_matrix import expected_cells, POLICY_NAMES
    from envs.wildfire_response.incident_day.scenarios import REAL_MAP_DEVELOPMENT_CASE_IDS

    source = Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME', '.runtime/wildfire-response-spain'))
    if not (source / 'manifest.json').is_file():
        pytest.skip('Pinned local source runtime unavailable')
    pin_source(monkeypatch, tmp_path, source)
    output = tmp_path / 'bundle'
    result = builder.build(source, output)
    entry = result['runtimes']['es-ma-2021']
    assert entry['additional_scenario_ids'] == list(REAL_MAP_DEVELOPMENT_CASE_IDS)
    rows = pq.read_table(output / 'tasks/development.parquet').to_pylist()
    source_ids = {r['task_id'] for r in rows if r['runtime_id'] == 'es-ma-2021'}
    assert source_ids == set(entry['scenario_ids'] + entry['additional_scenario_ids'])
    assert not {'incident-day-realmap-development-v1-005', 'incident-day-realmap-development-v1-006',
                'incident-day-realmap-development-v2-002', 'incident-day-realmap-development-v2-003'} & source_ids
    cells = expected_cells(hub_runtime.verify_package(output))
    assert len(cells) == 48
    assert {(c['case_id'], c['policy']) for c in cells} == {
        (case, policy) for case in REAL_MAP_DEVELOPMENT_CASE_IDS for policy in POLICY_NAMES}
