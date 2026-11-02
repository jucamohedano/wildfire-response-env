"""Synthetic saved-evidence tests; never compile or simulate an episode."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from eval import derive_evaluation_scores as view

rv = view.rv


@pytest.fixture
def finished(tmp_path, monkeypatch):
    root = tmp_path / 'revalidation'
    (root / 'source/private').mkdir(parents=True)
    (root / 'cells').mkdir()
    checks = [{'passed': True}]
    independent = dict(graded_score=.7, score_eligible=True, completed_minutes=1440,
        reward_version=rv.REWARD_VERSION, invalid_abandonment=False, boundary_censored=False)
    verification = {k: True for k in ('passed', 'exact_replay', 'independent_reward_agreement')}
    coverage = {k: {'status': 'complete', 'observed': 1} for k in rv.runner.uninspectable_coverage.__globals__['REQUIRED_COVERAGE']}
    expected = [dict(runtime_id='source', case_id=f'case-{i:03}', policy=p)
                for i in range(24) for p in rv.runner.POLICIES]
    pins = dict(implementation_sha256='engine', lifecycle_sha256='life', package_sha256='package', validator_sha256='old')
    freeze = dict(version='evaluation-freeze-v1', training=False, code_commit='a' * 40,
        reward_identity=rv.REWARD_VERSION, cells=expected, tasks=[], **pins)
    resources = [{'id': 'crew', 'kind': 'crew', 'base_position': [0, 0], 'duty_end_at': 2}]
    audit = [{'minute': i, 'resources': {'crew': {'state': 'available' if i == 0 else
        'needs_assistance' if i < 4 else 'returning' if i == 4 else 'off_duty',
        'position': [100, 0] if i < 4 else [90, 0] if i == 4 else [0, 0], 'duty_end_at': 2}},
        'applied_work': []} for i in range(1441)]
    reports = [{'kind': 'egress_unavailable', 'observed_at': 1,
                'payload': {'resource_id': 'crew', 'position': [100, 0]}}]
    detail = dict(category='unsupported', code='recovery_after_shift_end_egress_unavailable', resource='crew', minute=4)
    code = dict(source_git_commit=freeze['code_commit'], source_validator_sha256='old',
        corrected_validator_sha256='corrected', revalidator_sha256=rv.sha(Path(rv.__file__).read_bytes()),
        reviewed_observer_sha256=rv.sha((rv.ROOT / rv.OBSERVER).read_bytes()))
    source = dict(version=rv.runner.VERSION, expected_cells=expected, pins=pins, cells=[], policies=list(rv.runner.POLICIES))
    strict_cells, payloads, source_hashes = [], [], {}
    for i, ident in enumerate(expected):
        adverse = i == 0
        row = dict(runtime_id='source', task_id=ident['case_id'], episode_seed=12, scenario_sha256='scenario')
        if i % 2 == 0: freeze['tasks'].append(row)
        trace = dict(task_id=ident['case_id'], seed=12, implementation_sha256='engine', inputs_sha256='input',
            network_sha256='network', scenario_sha256='scenario', initial_observation={'resources': resources},
            queries=[], rejected_calls=[], transitions=[{'terminated': True, 'truncated': False}])
        original = dict(trajectory=trace, audit=audit, reports=reports, orders=[], outcome=independent,
            calls=[{'name': 'advance_time'}], verification=dict(verification, outcomes=independent),
            episode_seed=12, resume_checks=checks, findings=[detail] if adverse else [])
        source_cell = dict(ident, status='blocked' if adverse else 'complete', minute=1440,
            pins=dict(inputs_sha256='input', network_sha256='network', scenario_sha256='scenario'),
            outcome=independent, verification=verification, resume_checks=checks,
            query_independence={'passed': True}, score=None if adverse else .7)
        source['cells'].append(source_cell)
        proof = dict(passed=True, policy_executed=False, calls=1, transitions=1, queries=0,
            **{k + '_sha256': rv.checksum(v) for k, v in [('trajectory', trace), ('audit', audit),
                ('reports', reports), ('orders', []), ('outcome', independent), ('independent_reward', independent)]})
        result = dict(copy.deepcopy(source_cell), split='evaluation', training_eligible=False,
            operation_coverage=copy.deepcopy(coverage), movement_evidence=dict(ticks_observed=1440,
                capacity_ticks_checked=1440, timed_positions_checked=1),
            recorded_call_revalidation=proof,
            inherited_policy_checks=dict(reexecuted=False, resume_checks_sha256=rv.checksum(checks),
                query_independence_sha256=rv.checksum({'passed': True})),
            findings=rv.runner.aggregate_findings(original['findings']))
        rv.apply_reporting(result, original['findings'], view.projection(original), source_cell, 'withhold')
        strict_cells.append(result)
        raw_source = gzip.compress(json.dumps(original).encode())
        name = rv.filename(ident)
        (root / 'source/private' / name).write_bytes(raw_source)
        source_hashes['private/' + name] = rv.sha(raw_source)
        payloads.append(dict(result=result, corrected_findings=original['findings'], original_findings=original['findings'],
            corrected_observer={'coverage': result['movement_evidence'], 'findings': {}}, source_cell_sha256=rv.sha(raw_source)))
    for name, value in [('report.json', source), ('freeze.json', freeze)]:
        raw = json.dumps(value).encode(); (root / 'source' / name).write_bytes(raw); source_hashes[name] = rv.sha(raw)
    manifest = dict(version=rv.VERSION, adverse_outcome_policy='withhold', code=code,
        source_hashes=source_hashes, expected_cells=expected, snapshot_finished_cells=48)
    manifest['sha256'] = rv.checksum(manifest)
    (root / 'source_manifest.json').write_text(json.dumps(manifest))
    artifacts = {}
    for ident, payload in zip(expected, payloads):
        payload['source_snapshot_sha256'] = manifest['sha256']; payload['sha256'] = rv.checksum(payload)
        name = rv.filename(ident) + '.json'; raw = json.dumps(payload).encode()
        (root / 'cells' / name).write_bytes(raw); artifacts[name] = rv.sha(raw)
    strict = dict(version=rv.VERSION, revalidation=True, split='evaluation', training_eligible=False,
        expected_cells=expected, cells=strict_cells, policies=list(rv.runner.POLICIES),
        adverse_outcome_policy='withhold', source_snapshot_sha256=manifest['sha256'],
        source_snapshot_finished_cells=48, source_pins=pins, code=code,
        pins=dict(pins, validator_sha256='corrected'), cell_artifact_sha256=artifacts)
    rv.refresh_revalidation_report(strict)
    (root / 'report.json').write_text(json.dumps(strict))
    monkeypatch.setattr(rv, 'implementation_digest', lambda: 'engine')
    monkeypatch.setattr(rv, 'registry', lambda: {'sha256': 'life'})
    monkeypatch.setattr(rv.runner, 'validator_digest', lambda: 'corrected')
    monkeypatch.setattr(rv, 'recompute', lambda *a, **k: independent)
    def forbidden(*a, **k): raise AssertionError('Simulation or policy invoked')
    monkeypatch.setattr(rv, 'make_environment', forbidden)
    monkeypatch.setattr(rv, 'replay_cell', forbidden)
    monkeypatch.setattr(rv.runner, 'make_policy', forbidden)
    return root, strict, payloads[0], source, freeze


def report_hash(root):
    return rv.sha((root / 'report.json').read_bytes())


def test_final_view_retains_raw_reward_and_separate_adverse_lineage(finished):
    root, strict, *_ = finished
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = view.write_view(root, root.parent / 'scores.json', report_hash(root))
    assert result['strict_status'] == 'blocked'
    assert result['reporting_status'] == 'complete_with_adverse_outcomes'
    assert result['policy_lineage']['original_revalidation_policy'] == 'withhold'
    first = result['cells'][0]
    assert first['status'] == 'blocked' and first['score'] is None
    assert first['reported_score'] == first['outcome']['graded_score'] == .7
    assert first['operational_status'] == 'adverse'
    assert len(result['cells']) == 48
    assert (root.parent / 'scores.json').stat().st_mode & 0o777 == 0o600
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(FileExistsError): view.write_view(root, root.parent / 'scores.json', report_hash(root))


@pytest.mark.parametrize('fault', ['incomplete', 'report_hash', 'cell_hash', 'source_hash', 'missing_cell_hash', 'code_hash'])
def test_unfinished_or_changed_evidence_is_refused(finished, monkeypatch, fault):
    root, strict, *_ = finished
    if fault == 'incomplete':
        strict['cells'][1]['status'] = 'not_run'; (root / 'report.json').write_text(json.dumps(strict))
    elif fault == 'cell_hash': next((root / 'cells').iterdir()).write_text('{}')
    elif fault == 'source_hash': next((root / 'source/private').iterdir()).write_bytes(b'changed')
    elif fault == 'missing_cell_hash':
        strict['cell_artifact_sha256'].pop(next(iter(strict['cell_artifact_sha256'])))
        (root / 'report.json').write_text(json.dumps(strict))
    elif fault == 'code_hash': monkeypatch.setattr(rv.runner, 'validator_digest', lambda: 'changed')
    with pytest.raises(ValueError): view.build_view(root, 'wrong' if fault == 'report_hash' else report_hash(root))


def test_missing_resource_evidence_is_never_exempted(finished):
    _, _, payload, source, freeze = finished
    original = rv.evidence(next((finished[0] / 'source/private').iterdir()).read_bytes())
    original['trajectory']['initial_observation']['resources'] = []
    with pytest.raises(ValueError): view.projection(original)


def test_arbitrary_unsupported_is_still_unscored(finished):
    root, _, payload, source, freeze = finished
    original = rv.evidence((root / 'source/private' / rv.filename(source['cells'][0])).read_bytes())
    payload['corrected_findings'] = copy.deepcopy(payload['corrected_findings'])
    payload['corrected_findings'][0]['code'] = 'uninspectable_route'
    payload['result']['findings'] = rv.runner.aggregate_findings(payload['corrected_findings'])
    payload['result']['adverse_recovery_evidence'] = []
    payload['result']['operational_status'] = 'unresolved'
    derived = view.verify_cell(payload, original, source['cells'][0], freeze['tasks'][0], freeze)
    assert derived['reported_score'] is None
    assert derived['operational_status'] == 'unresolved'


def test_output_cannot_touch_revalidation(finished):
    root, *_ = finished
    with pytest.raises(ValueError, match='separate'):
        view.write_view(root, root / 'view.json', report_hash(root))


@pytest.fixture
def terminal():
    audit = [{'minute': i, 'resources': {'crew': {'state': 'needs_assistance', 'position': [100, 0],
               'duty_end_at': 960}}, 'applied_work': []} for i in range(1441)]
    env = view.SimpleNamespace(_scenario={'resources': [{'id': 'crew', 'kind': 'crew', 'duty_end_at': 960,
                                      'base_position': [0, 0]}]}, _audit=audit,
        bus=view.SimpleNamespace(reports=[{'kind': 'egress_unavailable', 'observed_at': 661,
                            'payload': {'resource_id': 'crew', 'position': [100, 0]}}]))
    result = dict(status='blocked', operation_coverage={k: {'status': 'complete', 'observed': 1}
        for k in rv.runner.uninspectable_coverage.__globals__['REQUIRED_COVERAGE']},
        movement_evidence={'ticks_observed': 1440, 'capacity_ticks_checked': 1440, 'timed_positions_checked': 5})
    findings = [{'category': 'unsupported', 'code': 'terminal_assistance_after_shift', 'resource': 'crew', 'minute': 1440}]
    return result, findings, env


def test_terminal_stationary_assistance_is_negative_outcome_not_recovery(terminal):
    result, findings, env = terminal
    proof = view.terminal_assistance_proof(result, findings, env)
    assert proof == [{'resource_id': 'crew', 'egress_unavailable_at': 661, 'duty_end_at': 960,
        'observed_through_minute': 1440, 'terminal_state': 'needs_assistance', 'recovery_status': 'unresolved_at_horizon'}]
    assert 'arrived_off_duty_at' not in proof[0]
    assert 'safe_success' not in proof[0]


@pytest.mark.parametrize('fault', ['work', 'movement', 'reassignment', 'missing_report', 'late_report',
    'wrong_position', 'wrong_code', 'contradiction', 'ticks', 'coverage', 'unknown_path', 'recovery_inferred', 'missing_audit'])
def test_terminal_view_rejects_unexplained_or_missing_evidence(terminal, fault):
    result, findings, env = terminal
    if fault == 'work': env._audit[1000]['applied_work'] = [{'resource_id': 'crew'}]
    elif fault == 'movement': env._audit[1000]['resources']['crew']['position'] = [101, 0]
    elif fault == 'reassignment': env._audit[1000]['resources']['crew']['state'] = 'available'
    elif fault == 'missing_report': env.bus.reports = []
    elif fault == 'late_report': env.bus.reports[0]['observed_at'] = 1000
    elif fault == 'wrong_position': env.bus.reports[0]['payload']['position'] = [99, 0]
    elif fault == 'wrong_code': findings[0]['code'] = 'arbitrary_unsupported'
    elif fault == 'contradiction': findings[0]['category'] = 'contradiction'
    elif fault == 'ticks': result['movement_evidence']['ticks_observed'] = 1439
    elif fault == 'coverage': result['operation_coverage'] = {}
    elif fault == 'unknown_path': result['movement_evidence']['journey_unverifiable'] = 1
    elif fault == 'recovery_inferred': env._audit[-1]['resources']['crew']['state'] = 'off_duty'
    elif fault == 'missing_audit': env._audit.pop(1000)
    assert view.terminal_assistance_proof(result, findings, env) == []


def test_verified_terminal_view_keeps_reward_but_never_claims_recovery(finished):
    root, _, payload, source, freeze = finished
    original = rv.evidence((root / 'source/private' / rv.filename(source['cells'][0])).read_bytes())
    for row in original['audit'][1:]:
        row['resources']['crew'].update(state='needs_assistance', position=[100, 0])
    detail = dict(category='unsupported', code='terminal_assistance_after_shift', resource='crew', minute=1440)
    original['findings'] = [detail]
    payload['original_findings'] = [detail]
    payload['corrected_findings'] = [detail]
    payload['result']['findings'] = rv.runner.aggregate_findings([detail])
    payload['result']['adverse_recovery_evidence'] = []
    payload['result']['operational_status'] = 'unresolved'
    payload['result']['recorded_call_revalidation']['audit_sha256'] = rv.checksum(original['audit'])
    result = view.verify_cell(payload, original, source['cells'][0], freeze['tasks'][0], freeze)
    assert result['status'] == 'blocked' and result['score'] is None
    assert result['reported_score'] == result['outcome']['graded_score'] == .7
    assert result['operational_status'] == 'unresolved_at_horizon' and result['adverse'] is True
    proof = result['terminal_assistance_evidence'][0]
    assert proof['observed_through_minute'] == 1440
    assert 'arrived_off_duty_at' not in proof and 'safe_success' not in proof
