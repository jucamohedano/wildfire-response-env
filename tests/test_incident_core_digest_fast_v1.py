"""Parity tests for the isolated digest prototype; no production hooks."""
import copy
import os
from pathlib import Path

import pytest

from eval.incident_core_digest_fast_v1 import _relative_paths, implementation_digest as fast_digest
from envs.wildfire_response.incident_day import environment


def test_actual_source_digest_matches_legacy():
    root = Path(environment.__file__).parents[3]
    folders = (root / 'envs/wildfire_response', root / 'wildfire_core', root / 'simulator')
    paths = [path for folder in folders for path in folder.rglob('*.py') if 'tests' not in path.parts]
    paths.append(root / 'envs/wildfire_response/data/huelva_regional_v2.json')
    assert [root.joinpath(*parts) for parts in _relative_paths(root)] == sorted(paths)
    assert fast_digest() == environment.implementation_digest()


def test_discovery_and_content_mutation_match_legacy(tmp_path, monkeypatch):
    root = tmp_path / 'core' / 'src'
    package = root / 'envs' / 'wildfire_response'
    for path in (package, root / 'wildfire_core', root / 'simulator', package / 'data'):
        path.mkdir(parents=True, exist_ok=True)
    data = package / 'data' / 'huelva_regional_v2.json'
    data.write_text('{}')
    tracked = package / 'a.py'
    tracked.write_text('one')
    (package / '.hidden.py').write_text('hidden')
    (package / 'nested').mkdir()
    (package / 'nested' / 'b.py').write_text('nested')
    (package / 'tests').mkdir()
    (package / 'tests' / 'ignored.py').write_text('ignored')
    (root / 'wildfire_core' / 'linked.py').symlink_to(tracked)
    (root / 'simulator' / 'linked-dir').symlink_to(package / 'nested', target_is_directory=True)
    monkeypatch.setattr(environment, '__file__', str(package / 'incident_day' / 'environment.py'))

    def compare():
        folders = (package, root / 'wildfire_core', root / 'simulator')
        paths = [path for folder in folders for path in folder.rglob('*.py') if 'tests' not in path.parts]
        paths.append(data)
        assert [root.joinpath(*parts) for parts in _relative_paths(root)] == sorted(paths)
        old = environment.implementation_digest()
        assert fast_digest(root) == old
        return old

    baseline = compare()
    # A same-size edit with its original mtime must still be detected.
    before = tracked.stat()
    tracked.write_text('two')
    os.utime(tracked, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert compare() != baseline
    changed = compare()
    extra = root / 'simulator' / 'new.py'
    extra.write_text('new')
    assert compare() != changed
    extra.unlink()
    assert compare() == changed
    (package / 'tests' / 'ignored.py').write_text('still ignored')
    assert compare() == changed
    data.write_text('{"changed":true}')
    assert compare() != changed


def test_matching_error_on_python_named_directory(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    package = root / 'envs' / 'wildfire_response'
    (package / 'bad.py').mkdir(parents=True)
    (package / 'data').mkdir()
    (package / 'data' / 'huelva_regional_v2.json').write_text('{}')
    monkeypatch.setattr(environment, '__file__', str(package / 'incident_day' / 'environment.py'))
    with pytest.raises(IsADirectoryError):
        environment.implementation_digest()
    with pytest.raises(IsADirectoryError):
        fast_digest(root)


def test_restricted_host_checkpoint_rng_replay_and_censor_parity(monkeypatch, tmp_path):
    import adapter_v1 as api
    import adapter_fire_v1 as fire_adapter
    from envs.wildfire_response.incident_day.scenarios import PROFILE
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
    from wildfire_source_restricted_v1 import RestrictedSourceIncidentV1

    n = 12
    grid = dict(width=n, height=n, crs='EPSG:25830', transform=[500, 0, 0, 0, -500, 6000])
    position = [5250, 750]
    parent = {'grid': {'centre_lon_lat': [[[-4., 36.7]]]}, 'version': 'synthetic'}
    parent['sha256'] = api._hash(parent)
    unit = dict(id='air', kind='aircraft', position=position, base_position=position, state='available',
                available_at=0, capacity_litres=1000, water_litres=1000, work_remaining=100,
                total_work_remaining=100, fuel_minutes=180, fuel_capacity_minutes=180, facility_id='base')
    profile = dict(copy.deepcopy(PROFILE), facilities={'base': dict(position=position,
        allowed_kinds=['aircraft'], water_litres=10000, refuel=True)})
    case = dict(id='fixture', seed=3, synthetic=True, historical_replay=False, sector_grid=[0, 0],
        start_utc='2021-06-01T12:00:00Z', resources=[unit], groups=[], destinations=[],
        centers=['center'], events=[], profile=profile)
    case['sha256'] = api._hash(case)
    record = dict(minute=0, wind=[0., 0.], temperature_c=25, relative_humidity_pct=35,
                  rain_mm_per_hour=0, received_at=0)
    fire = dict(id='fire', seed=3, initial_fire=[[5, 5]], wind=[0., 0.], warmup_minutes=0,
        events=[], weather_schedule=[record], parent_scenario_sha256=case['sha256'], incoming_reports=[])
    fire['sha256'] = api._hash(fire)
    drafts = dict(version='wildfire-extended-scenario-drafts-v1', status='unqualified-not-runnable',
                  source_catalog_sha256='a'*64, cases=[fire])
    drafts['sha256'] = api._hash(drafts)
    doc = dict(version='wildfire-extended-inputs-v1', grid=grid,
        domain_mask=[[True]*n for _ in range(n)], surface=[['land']*n for _ in range(n)],
        fuel=[[4]*n for _ in range(n)], elevation=[[100.]*n for _ in range(n)],
        access=[['unknown']*n for _ in range(n)],
        source_pins={k: c*64 for k, c in zip(('surface', 'fuel', 'elevation', 'access'), 'abcd')})
    contract = api.load_inputs(doc, api._hash(doc), expected_source_pins=doc['source_pins'], opt_in=True)
    ident = dict(version='wildfire-small-water-mixed-exercise-input-v4', catalog='a'*64,
                 input_sha256=contract.document_sha256)
    mixed = MixedInputsV2(contract, (), tuple(sorted(ident.items())), _VERIFIED)
    selector = object.__new__(SourceCaseCatalogV1)
    selector._identity = dict(input_identity=ident, parent=parent['sha256'], fire=drafts['sha256'])
    selector._fire = drafts
    monkeypatch.setattr(SourceCaseCatalogV1, '_ensure', lambda self: None)
    monkeypatch.setattr(SourceCaseCatalogV1, 'private_case',
                        lambda self, unused: copy.deepcopy(dict(original=case, expanded_fire=fire)))
    config = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, report_delay=2, wear_factor=.1)
    legacy = fire_adapter.implementation_digest

    def invoke(digest, function, *args, **kwargs):
        with monkeypatch.context() as context:
            context.setattr(fire_adapter, 'implementation_digest', digest)
            return function(*args, **kwargs)

    def make(digest):
        return invoke(digest, RestrictedSourceIncidentV1, mixed, selector, parent, case_id='fixture',
                      containment_config=config, pressure_scale=1, opt_in=True)

    a, b = make(legacy), make(fast_digest)
    for _ in range(4):
        assert invoke(legacy, a.advance, 1) == invoke(fast_digest, b.advance, 1)
        assert api._encoded(invoke(legacy, a.checkpoint)) == api._encoded(invoke(fast_digest, b.checkpoint))
    tape = invoke(legacy, a.transcript)
    saved = invoke(legacy, a.checkpoint)
    replay = make(fast_digest)
    assert invoke(fast_digest, replay.replay, tape, expected_sha256=api._hash(tape),
        expected_identity_sha256=tape['identity_sha256'], expected_final_checkpoint_sha256=api._hash(saved)) == a.situation()
    assert invoke(legacy, a._fire.change, {'ignite': [[0, 0]]}) is False
    assert invoke(fast_digest, b._fire.change, {'ignite': [[0, 0]]}) is False
    assert invoke(legacy, a.advance, 1) == invoke(fast_digest, b.advance, 1)
    assert api._encoded(invoke(legacy, a.checkpoint)) == api._encoded(invoke(fast_digest, b.checkpoint))

    # Exercise the actual backend guard against a private copy, never modifying
    # the running host's frozen source tree. Restore mtime after a same-size edit.
    import shutil
    source_root = Path(environment.__file__).parents[3]
    clone_root = tmp_path / 'clone'
    for relative in ('envs/wildfire_response', 'wildfire_core', 'simulator'):
        destination = clone_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root / relative, destination, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    assert fast_digest(clone_root) == legacy()
    guarded = make(fast_digest)
    cloned_digest = lambda: fast_digest(clone_root)
    invoke(cloned_digest, guarded._fire._ensure)
    target = clone_root / 'envs/wildfire_response/incident_day/environment.py'
    before = target.stat()
    content = target.read_bytes()
    target.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(RuntimeError, match='unreviewed incident-day core'):
        invoke(cloned_digest, guarded._fire._ensure)
