import hashlib
import json
from pathlib import Path

import pytest

from eval import hub_runtime as hub
from wildfire_core.catalog_lifecycle import active_day_case_ids, registry
from wildfire_core.incident_day import fixture_inputs


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def package(tmp_path):
    inputs, network = fixture_inputs()
    payload = {'runtime/fixture/inputs.json': encode(inputs),
               'runtime/fixture/network.json': encode(network),
               'tasks/development.parquet': b'fixture task placeholder'}
    files = []
    for name, raw in payload.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        files.append(dict(path=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    entry = dict(inputs='runtime/fixture/inputs.json', network='runtime/fixture/network.json',
                 inputs_sha256=inputs['sha256'], network_sha256=network['sha256'],
                 allow_unapproved_data=False,
                 scenario_ids=list(active_day_case_ids(inputs['sha256'], network['sha256'])))
    manifest = dict(version=hub.VERSION, release_version=hub.RELEASE_VERSION,
                    dataset_repo='jucamohedano/wildfire-response-spain', source_revision='abc123',
                    engine_implementation_sha256=hub.implementation_digest(), lifecycle_sha256=registry()['sha256'],
                    files=files, runtimes={'fixture': entry})
    (tmp_path / 'manifest.json').write_bytes(encode(manifest))
    return tmp_path, manifest


def save(root, manifest):
    (root / 'manifest.json').write_bytes(encode(manifest))


def test_fixture_load_and_complete_replay(package):
    root, manifest = package
    assert hub.verify_package(root) == manifest
    env, entry = hub.make_environment(root)
    result = hub.run_episode(env, entry, case_id=entry['scenario_ids'][0], wall_seconds=120)
    assert result['status'] == 'complete'
    assert result['verification'] == 'passed'
    assert result['simulated_minutes'] == 1440
    assert result['score'] is not None


def test_tampering_and_unlisted_file(package):
    root, _ = package
    path = root / 'tasks/development.parquet'
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='(mismatch|limit)'):
        hub.verify_package(root)
    path.write_bytes(b'fixture task placeholder')
    (root / 'extra').write_text('unexpected')
    with pytest.raises(ValueError, match='inventory'):
        hub.verify_package(root)


def test_huggingface_transport_metadata_is_not_payload(package):
    root, manifest = package
    (root / '.gitattributes').write_text('*.parquet filter=lfs diff=lfs merge=lfs -text\n')
    metadata = root / '.cache/huggingface/download/runtime/fixture/inputs.json.metadata'
    metadata.parent.mkdir(parents=True)
    metadata.write_text('revision\netag\n1234567890\n')
    (metadata.parent / 'inputs.json.lock').write_text('')
    assert hub.verify_package(root) == manifest
    (root / '.cache/huggingface-extra').write_text('not transport metadata')
    with pytest.raises(ValueError, match='inventory'):
        hub.verify_package(root)


@pytest.mark.parametrize('name', ['.gitattributes', '.cache/huggingface/download/inputs.json.metadata'])
def test_transport_metadata_cannot_be_inventoried(package, name):
    root, manifest = package
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'metadata')
    manifest['files'].append(dict(path=name, bytes=8, sha256=hashlib.sha256(b'metadata').hexdigest()))
    manifest['runtimes']['fixture']['inputs'] = name
    save(root, manifest)
    with pytest.raises(ValueError, match='reserved'):
        hub.verify_package(root)


@pytest.mark.parametrize('path', ['../outside', '/etc/passwd', 'runtime//file', 'runtime/./file', 'runtime\\file'])
def test_bad_paths(package, path):
    root, manifest = package
    manifest['files'][0]['path'] = path
    save(root, manifest)
    with pytest.raises(ValueError, match='path'):
        hub.verify_package(root)


def test_symlink_rejected(package):
    root, _ = package
    path = root / 'tasks/development.parquet'
    path.unlink()
    path.symlink_to(root / 'runtime/fixture/inputs.json')
    with pytest.raises(ValueError, match='Symlink'):
        hub.verify_package(root)


def test_duplicate_inventory_and_json_keys(package):
    root, manifest = package
    manifest['files'].append(manifest['files'][0])
    save(root, manifest)
    with pytest.raises(ValueError, match='Duplicate'):
        hub.verify_package(root)
    (root / 'manifest.json').write_text('{"version": 1, "version": 2}')
    with pytest.raises(ValueError, match='Duplicate'):
        hub.verify_package(root)


@pytest.mark.parametrize('field', ['engine_implementation_sha256', 'lifecycle_sha256'])
def test_unknown_package_pins(package, field):
    root, manifest = package
    manifest[field] = '0' * 64
    save(root, manifest)
    with pytest.raises(ValueError, match='pin mismatch'):
        hub.verify_package(root)


def test_unknown_runtime_pin_and_ids(package):
    root, manifest = package
    manifest['runtimes']['fixture']['inputs_sha256'] = '0' * 64
    save(root, manifest)
    with pytest.raises(ValueError, match='No active'):
        hub.verify_package(root)
    manifest['runtimes']['fixture']['inputs_sha256'] = fixture_inputs()[0]['sha256']
    manifest['runtimes']['fixture']['scenario_ids'][0] = 'archived'
    save(root, manifest)
    with pytest.raises(ValueError, match='active set'):
        hub.verify_package(root)


def test_metadata_does_not_approve_unapproved_data(package, monkeypatch):
    root, manifest = package
    manifest['runtimes']['fixture']['allow_unapproved_data'] = True
    save(root, manifest)
    with pytest.raises(ValueError, match='explicit'):
        hub.make_environment(root)
    env, _ = hub.make_environment(root, allow_unapproved_data=True)
    assert env is not None
    # False metadata must not override the core's own source approval gate.
    manifest['runtimes']['fixture']['allow_unapproved_data'] = False
    save(root, manifest)
    def reject(*args, **kwargs):
        assert kwargs['allow_unapproved_data'] is False
        raise ValueError('unapproved')
    monkeypatch.setattr(hub, 'IncidentDayEnv', reject)
    with pytest.raises(ValueError, match='unapproved'):
        hub.make_environment(root)


def test_canonical_pin_checked_at_load(package):
    root, manifest = package
    entry = manifest['runtimes']['fixture']
    path = root / entry['inputs']
    document = json.loads(path.read_bytes())
    document['approval_status'] = 'tampered'
    raw = encode(document)
    path.write_bytes(raw)
    record = next(item for item in manifest['files'] if item['path'] == entry['inputs'])
    record.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    save(root, manifest)
    hub.verify_package(root)
    with pytest.raises(ValueError, match='(hash|digest)'):
        hub.make_environment(root)


def test_command_limit_and_timeout_unscored(package, monkeypatch):
    root, _ = package
    env, entry = hub.make_environment(root)
    result = hub.run_episode(env, entry, max_commands=1)
    assert result['status'] == 'incomplete' and result['score'] is None
    assert result['case_id'] == entry['scenario_ids'][0]
    env, entry = hub.make_environment(root)
    monkeypatch.setattr(env, 'reset', lambda *args: __import__('time').sleep(.2))
    result = hub.run_episode(env, entry, wall_seconds=.01)
    assert result['status'] == 'incomplete' and result['score'] is None
    assert result['verification'] != 'passed'


def test_cli_verify_only(package, capsys):
    root, _ = package
    assert hub.main(['--data-dir', str(root), '--verify-only']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'verified'


def test_compatible_new_release_metadata_keeps_schema_and_engine_checks(package):
    root, manifest = package
    manifest['release_version'] = '0.2.1'
    save(root, manifest)
    assert hub.verify_package(root)['release_version'] == '0.2.1'
    manifest['version'] = 'unsupported-runtime-schema-v2'
    save(root, manifest)
    with pytest.raises(ValueError, match='schema version'):
        hub.verify_package(root)
