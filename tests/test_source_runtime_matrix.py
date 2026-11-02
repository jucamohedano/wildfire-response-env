import copy

import pytest

from eval import validate_source_runtime_matrix as matrix
from eval.incident_day_policies import POLICY_NAMES


def catalog():
    return dict(id=matrix.CATALOG_ID, status='active_development',
                inputs_sha256='input-pin', network_sha256='network-pin',
                case_ids=[f'source-{i:02}' for i in range(12)])


def manifest(ids=None):
    return {'runtimes': {'es-ma-2021': dict(inputs_sha256='input-pin',
        network_sha256='network-pin', allow_unapproved_data=True, scenario_ids=['old-source'],
        additional_scenario_ids=list(catalog()['case_ids'] if ids is None else ids))}}


def matrix_report(expected):
    cases = {(c['runtime_id'], c['case_id']) for c in expected}
    return dict(expected_cells=expected, runtime_findings=[],
        static_cases=[dict(runtime_id=r, case_id=c, findings=[]) for r, c in cases],
        cells=[dict(c, status='complete', minute=1440,
            verification=dict(passed=True, exact_replay=True, independent_reward_agreement=True),
            resume_checks=[dict(passed=True)], query_independence=dict(passed=True),
            operation_coverage={name: dict(status='complete', observed=1) for name in matrix.REQUIRED_COVERAGE})
            for c in expected])


def test_new_catalog_only_and_all_policies(monkeypatch):
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    cells = matrix.expected_cells(manifest())
    assert len(cells) == 48
    assert {c['case_id'] for c in cells} == set(catalog()['case_ids'])
    assert {c['policy'] for c in cells} == set(POLICY_NAMES)
    assert all(c['runtime_id'] == 'es-ma-2021' for c in cells)
    with pytest.raises(ValueError, match='shrink'):
        matrix.expected_cells(manifest(), policies=('passive',))
    bad = manifest(); bad['runtimes']['es-ma-2021']['network_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='identity'):
        matrix.expected_cells(bad)
    bad = catalog(); bad['case_ids'][-1] = bad['case_ids'][0]
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [bad]})
    with pytest.raises(ValueError, match='identity'):
        matrix.expected_cells(manifest())


@pytest.mark.parametrize('extra', [None, [], ['source-00'] * 12,
    [f'source-{i:02}' for i in range(11)], [f'source-{i:02}' for i in range(11)] + ['wrong'],
    [f'source-{i:02}' for i in range(12)] + ['extra'], 'not-a-list'])
def test_manifest_must_declare_exact_source_catalog(monkeypatch, extra):
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    package = manifest()
    if extra is None:
        del package['runtimes']['es-ma-2021']['additional_scenario_ids']
    else:
        package['runtimes']['es-ma-2021']['additional_scenario_ids'] = extra
    with pytest.raises(ValueError, match='identity'):
        matrix.expected_cells(package)


def test_real_source_and_original_matrix_domains_are_disjoint():
    from wildfire_core.catalog_lifecycle import registry
    from eval.validate_incident_day_runtime import expected_cells as old_expected_cells

    catalogs = {c['id']: c for c in registry()['catalogs']}
    fixture = catalogs['incident_day_fixture']
    old_source = catalogs['incident_day_realmap_v2']
    new_source = catalogs[matrix.CATALOG_ID]
    def runtime(catalog, unapproved):
        return dict(inputs_sha256=catalog['inputs_sha256'], network_sha256=catalog['network_sha256'],
                    scenario_ids=catalog['case_ids'], allow_unapproved_data=unapproved)
    package = {'runtimes': {'fixture': runtime(fixture, False),
                            'es-ma-2021': runtime(old_source, True)}}
    package['runtimes']['es-ma-2021']['additional_scenario_ids'] = new_source['case_ids']
    old = old_expected_cells(package)
    new = matrix.expected_cells(package)
    assert len(old) == len(new) == 48
    assert {c['case_id'] for c in new} == set(new_source['case_ids'])
    assert not ({c['case_id'] for c in old} & {c['case_id'] for c in new})
    assert {(c['case_id'], c['policy']) for c in new} == {
        (case, policy) for case in new_source['case_ids'] for policy in POLICY_NAMES}


def test_validation_requires_exact_static_and_runtime_evidence(monkeypatch):
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    expected = matrix.expected_cells(manifest())
    original = matrix_report(expected)
    assert matrix.validate_matrix(original) == 'complete'
    for mutation in (
        lambda r: r['static_cases'].pop(),
        lambda r: r['static_cases'].__setitem__(0, copy.deepcopy(r['static_cases'][1])),
        lambda r: r['static_cases'][0]['findings'].append({'category': 'blocking'}),
        lambda r: r['cells'][0].update(status='incomplete'),
        lambda r: r['cells'][0].update(resume_checks=[{'passed': 1}]),
        lambda r: r['cells'][0]['verification'].update(exact_replay=False),
        lambda r: r['cells'].pop(),
    ):
        value = copy.deepcopy(original); mutation(value)
        assert matrix.validate_matrix(value) != 'complete'
    value = copy.deepcopy(original)
    for cell in value['cells']:
        cell['operation_coverage']['releases']['observed'] = 0
    assert matrix.validate_matrix(value) == 'blocked'


@pytest.mark.parametrize('code', ['case_audit_unavailable', 'route_audit_unavailable',
    'tool_journal_incomplete', 'transition_incomplete', 'unknown_future_audit_gap'])
def test_uninspectable_runtime_findings_block_acceptance(monkeypatch, code):
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    value = matrix_report(matrix.expected_cells(manifest()))
    finding = {'category': 'unsupported', 'code': code}
    value['cells'][0]['findings'] = [finding]
    assert matrix.validate_matrix(value) == 'blocked'
    del value['cells'][0]['findings']
    value['runtime_findings'].append(finding)
    assert matrix.validate_matrix(value) == 'blocked'


def test_incomplete_coverage_blocks_but_documented_limitations_do_not(monkeypatch):
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    value = matrix_report(matrix.expected_cells(manifest()))
    value['static_cases'][0]['findings'] = [{'category': 'unsupported', 'code': 'land_mask_missing'},
                                            {'category': 'unsupported', 'code': 'synthetic_physics'}]
    value['cells'][0]['findings'] = [{'category': 'unsupported', 'code': 'fire_front_screen'}]
    value['cells'][0]['operation_coverage']['physical_access_and_real_clearance'] = {'status': 'unsupported'}
    assert matrix.validate_matrix(value) == 'complete'
    for name in ('get_situation', 'facility_conservation', 'capacity_each_tick', 'movement_paths'):
        broken = copy.deepcopy(value)
        broken['cells'][0]['operation_coverage'][name]['status'] = 'unsupported'
        assert matrix.validate_matrix(broken) == 'blocked'
    broken = copy.deepcopy(value)
    del broken['cells'][0]['operation_coverage']['facility_conservation']
    assert matrix.validate_matrix(broken) == 'blocked'


def test_resume_rejects_changed_package_before_environment_use(monkeypatch, tmp_path):
    monkeypatch.setattr(matrix, 'verify_package', lambda _: manifest())
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()], 'sha256': 'lifecycle'})
    monkeypatch.setattr(matrix, 'validator_digest', lambda: 'validator')
    monkeypatch.setattr(matrix, 'load_progress', lambda *args: (
        {'package_sha256': 'different', 'lifecycle_sha256': 'lifecycle'}, {}))
    with pytest.raises(ValueError, match='package or lifecycle'):
        matrix.run('unused', tmp_path, resume=True)


def test_recovery_bundle_survives_missing_report_and_rejects_tampering(monkeypatch, tmp_path):
    import gzip
    import json

    from envs.wildfire_response.incident_day.environment import implementation_digest
    from eval.incident_day_movement_checks import MovementObserver
    from eval.incident_day_policies import make_policy
    from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    new_catalog = catalog()
    new_catalog['case_ids'][0] = env._scenario['id']
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [new_catalog]})
    expected = matrix.expected_cells(manifest(new_catalog['case_ids']))
    index = next(i for i, c in enumerate(expected)
                 if c['case_id'] == env._scenario['id'] and c['policy'] == 'passive')
    report = matrix_report(expected)
    report.update(validator_sha256=matrix.validator_digest(), implementation_sha256=implementation_digest())
    report['cells'][index]['status'] = 'incomplete'
    (tmp_path / 'private').mkdir()
    matrix._checkpoint(tmp_path, report, index, env, make_policy('passive'), [], set(), [],
                       {'passed': True}, MovementObserver(env))
    loaded, state = matrix.load_progress(tmp_path, expected, matrix.validator_digest())
    assert loaded['cells'][index]['status'] == 'incomplete'
    assert state['index'] == index
    path = tmp_path / 'private/checkpoint.json.gz'
    raw = json.loads(gzip.decompress(path.read_bytes()))
    raw['policy']['policy'] = 'dispatcher'
    raw['sha256'] = matrix.checksum({k: v for k, v in raw.items() if k != 'sha256'})
    path.write_bytes(gzip.compress(json.dumps(raw).encode()))
    with pytest.raises(ValueError, match='identity'):
        matrix.load_progress(tmp_path, expected, matrix.validator_digest())


def test_runner_saves_initial_checkpoint_before_first_action(monkeypatch, tmp_path):
    from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    new_catalog = catalog()
    new_catalog['case_ids'][0] = env._scenario['id']
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [new_catalog], 'sha256': 'lifecycle'})
    package = manifest(new_catalog['case_ids'])
    monkeypatch.setattr(matrix, 'verify_package', lambda _: package)
    monkeypatch.setattr(matrix, 'make_environment', lambda *a, **kw: (env, package['runtimes']['es-ma-2021']))
    monkeypatch.setattr(matrix, 'static_audit', lambda *a: [])
    monkeypatch.setattr(matrix, '_query_independence', lambda env: {'passed': True})
    # Use the fixture ID first so the test can initialize a real environment.
    cells = matrix.expected_cells(package)
    cells.sort(key=lambda c: c['case_id'] != env._scenario['id'])
    monkeypatch.setattr(matrix, 'expected_cells', lambda *a: cells)
    seen = []
    def stop_at_checkpoint(output, report, index, live_env, *args):
        seen.append((index, live_env.minute, report['cells'][index]['reason']))
        raise RuntimeError('simulated process stop')
    monkeypatch.setattr(matrix, '_checkpoint', stop_at_checkpoint)
    with pytest.raises(RuntimeError, match='simulated process stop'):
        matrix.run('unused', tmp_path / 'output')
    assert seen == [(0, 0, 'checkpoint')]


def test_corrupt_real_action_audit_cannot_pass_matrix(monkeypatch):
    from eval.incident_day_action_checks import audit_runtime
    from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    env._audit[0]['minute'] = 99
    findings = audit_runtime(env, [])['findings']
    assert any(f['code'] == 'case_audit_unavailable' and f['category'] == 'unsupported' for f in findings)
    monkeypatch.setattr(matrix, 'registry', lambda: {'catalogs': [catalog()]})
    report = matrix_report(matrix.expected_cells(manifest()))
    report['cells'][0]['findings'] = matrix.aggregate_findings(findings)
    # Other independent checks can also detect contradictions in the same
    # corrupt audit, in which case failed is stronger than blocked.
    assert matrix.validate_matrix(report) in ('blocked', 'failed')
