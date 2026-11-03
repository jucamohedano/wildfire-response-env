"""Synthetic controls qualification integration; no simulator/model imports.

The full-domain tests stub only the already-tested terminal-file extractor.
The bounded gzip test below keeps the actual extractor and receipt binding.
"""
import copy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import qualify_benchmark_v2 as cli
from wildfire_benchmark_v2 import Outcome, cost
from test_benchmark_v2_verifier import terminal  # noqa: F401

TASKS = [f'incident-day-evaluation-v1-{i:03}' for i in range(24)]
PASSIVE = Outcome(20, 4, 9, 4, 1.)
REFERENCE = Outcome(20, 0, 9, 0, 1.)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = cli.canonical(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def qualification(tmp_path, monkeypatch):
    identity = dict(seed=42, scenario_sha256='c' * 64, inputs_sha256='d' * 64,
        network_sha256='e' * 64, implementation_sha256='a' * 64, observation_sha256='b' * 64,
        interface_version='wildfire-incident-day-ops-v3')
    protocol = json.loads((cli.ROOT / 'data/benchmark_v2/protocol.json').read_text())
    protocol['reference_candidates'] = ['dispatcher', 'stress']
    for key in ('implementation_sha256', 'observation_sha256', 'interface_version'):
        protocol['weak_active_registration'][key] = identity[key]
    package = tmp_path / 'package'
    package.mkdir()
    asset = b'synthetic inventory asset'
    (package / 'asset.json').write_bytes(asset)
    manifest = dict(engine_implementation_sha256=identity['implementation_sha256'], files=[
        dict(path='asset.json', bytes=len(asset), sha256=hashlib.sha256(asset).hexdigest())],
        runtimes={'es-ma-2021': dict(inputs_sha256=identity['inputs_sha256'], network_sha256=identity['network_sha256'])})
    manifest_sha = write_json(package / 'manifest.json', manifest)
    rows = [dict(task_id=task, runtime_id='es-ma-2021', split='evaluation', training_eligible=False,
        episode_seed=42, scenario_sha256=identity['scenario_sha256'], configuration_group=f'evaluation-v1-family-{i // 4}')
        for i, task in enumerate(TASKS)]
    freeze = dict(manifest_sha256=manifest_sha, package_sha256=cli.seal(manifest), tasks=rows)
    pins = dict(implementation_sha256=identity['implementation_sha256'], observation_sha256=identity['observation_sha256'],
        package_sha256=cli.seal(manifest), lifecycle_sha256='f' * 64, observation_version='v3', validator_sha256='7' * 64)
    cells = []
    outcomes = {}
    for task in TASKS:
        outcomes[(task, 'passive')] = PASSIVE
        outcomes[(task, 'dispatcher')] = PASSIVE if task == TASKS[7] else REFERENCE
        outcomes[(task, 'conservative')] = PASSIVE
    outcomes[(TASKS[7], 'stress')] = REFERENCE
    def cell(task, policy):
        return dict(case_id=task, policy=policy, runtime_id='es-ma-2021', status='complete',
                    outcome={'graded_score': 1 - cost(outcomes[(task, policy)])})
    for task in TASKS:
        cells.extend(cell(task, policy) for policy in ('passive', 'dispatcher'))
    report = dict(version='wildfire-packaged-evaluation-v1', split='evaluation', training_eligible=False,
                  pins=pins, cells=cells, baseline_coverage_contract={'unobserved_required': []})
    controls = dict(version='wildfire-benchmark-v2-controls-v1', split='evaluation', training_eligible=False,
        status='complete', pins=copy.deepcopy(pins), cells=[cell(task, 'conservative') for task in TASKS] +
        [cell(TASKS[7], 'stress')])
    controls['policy_registrations'] = {policy: cli.control_registration(protocol, policy)
                                      for policy in ('conservative', 'stress')}
    controls['pins']['policy_registrations_sha256'] = cli.seal(controls['policy_registrations'])
    controls['expected_cells'] = [{key: item[key] for key in ('runtime_id', 'case_id', 'policy')}
                                  for item in controls['cells']]
    controls['summary'] = dict(expected=25, finished=25, statuses={'complete': 25})
    report_path = tmp_path / 'baseline/report.json'
    controls_path = tmp_path / 'controls/report.json'
    freeze_path, protocol_path = tmp_path / 'freeze.json', tmp_path / 'protocol.json'
    wrong = {}
    def extracted(path, source, row, record, expected):
        task, policy = row['task_id'], record['policy']
        assert expected == dict(identity, task_id=task)
        outcome = outcomes[(task, policy)]
        meta = dict(identity=copy.deepcopy(expected), initial_audit_sha256='1' * 64,
            source_execution_verified=True, terminal_counts_verified=True,
            cost=cost(outcome), raw_reward=1 - cost(outcome), adverse_flags=[], operational_order_count=1)
        if policy in ('conservative', 'stress'):
            receipt = cli.control_registration(protocol, policy)
            meta.update(policy_registration=receipt, policy_registration_sha256=cli.seal(receipt))
            for key, value in wrong.items():
                meta[key] = copy.deepcopy(value)
        name = f"es-ma-2021-{task}-{policy}.json.gz"
        return outcome, meta, name, hashlib.sha256(name.encode()).hexdigest()
    monkeypatch.setattr(cli, 'read_execution', extracted)
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda *args, **kwargs: 'synthetic-commit\n')
    def build(*, with_controls=True, output='qualified', expected_controls=None, reconcile=True):
        report_sha = write_json(report_path, report)
        freeze_sha = write_json(freeze_path, freeze)
        write_json(protocol_path, protocol)
        controls['pins']['protocol_sha256'] = cli.seal(protocol)
        if reconcile:
            controls['expected_cells'] = [{key: item[key] for key in ('runtime_id', 'case_id', 'policy')}
                                          for item in controls['cells']]
            statuses = {}
            for item in controls['cells']:
                statuses[item['status']] = statuses.get(item['status'], 0) + 1
            controls['summary'] = dict(expected=len(controls['expected_cells']),
                finished=sum(item['status'] in ('complete', 'blocked', 'failed') for item in controls['cells']),
                statuses=statuses)
        controls_sha = write_json(controls_path, controls)
        kwargs = dict(expected_report_sha256=report_sha, expected_freeze_sha256=freeze_sha)
        if with_controls:
            kwargs.update(controls_report=controls_path, expected_controls_sha256=expected_controls or controls_sha)
        return cli.build(report_path, freeze_path, package, protocol_path, tmp_path / output, **kwargs)
    return SimpleNamespace(build=build, report=report, controls=controls, protocol=protocol,
        outcomes=outcomes, wrong=wrong, tmp_path=tmp_path, cell=cell)


def test_default_keeps_dispatcher_and_no_weak_release(qualification):
    result, diagnostic = qualification.build(with_controls=False)
    assert result['cases'][TASKS[7]]['reference_policy'] == 'dispatcher'
    assert not diagnostic[TASKS[7]]['qualification']['qualified']
    assert result['qualification']['missing_tasks']['weak_active'] == TASKS
    assert result['qualification']['official_mean'] is None


def test_verified_conservative_and_sparse_stress_select_best_found_without_rewriting_history(qualification):
    result, diagnostic = qualification.build()
    case, row = result['cases'][TASKS[7]], diagnostic[TASKS[7]]
    assert case['candidate_reference_policies'] == ['dispatcher', 'stress']
    assert case['reference_policy'] == row['selected_reference_policy'] == 'stress'
    assert case['reference_proven_optimal'] is False
    assert set(case['source_evidence_sha256']) == {'passive', 'stress'}
    assert row['historical_dispatcher_raw_reward'] == 1 - cost(PASSIVE)
    assert row['selected_reference_raw_reward'] == row['legacy_reference_reward'] == 1 - cost(REFERENCE)
    assert result['qualification']['qualification_ready']
    assert result['qualification']['official_mean'] is None
    assert all('weak_active_outcome' in item for item in result['cases'].values())
    page = (qualification.tmp_path / 'qualified/report.html').read_text()
    assert '24 / 24' in page and 'Historical dispatcher raw' in page and 'Selected reference policy' in page
    assert '0 / 24</div>Verified weak-active controls' not in page


def test_release_still_requires_all_weak_cases_and_explicit_protocol_status(qualification):
    qualification.protocol['status'] = 'released'
    qualification.controls['cells'] = [cell for cell in qualification.controls['cells']
        if not (cell['case_id'] == TASKS[-1] and cell['policy'] == 'conservative')]
    result, _ = qualification.build()
    assert result['qualification']['missing_tasks']['weak_active'] == [TASKS[-1]]
    assert not result['qualification']['release_ready'] and result['qualification']['official_mean'] is None
    qualification.controls['cells'].append(qualification.cell(TASKS[-1], 'conservative'))
    result, _ = qualification.build(output='complete')
    assert result['qualification']['release_ready'] and result['qualification']['official_mean'] == 1


def test_strong_weak_control_does_not_unlock_release(qualification):
    qualification.protocol['status'] = 'released'
    qualification.outcomes[(TASKS[0], 'conservative')] = REFERENCE
    result, _ = qualification.build()
    assert result['qualification']['weak_active_failures'][TASKS[0]] == 'weak_active_grade_exceeds_0.6'
    assert result['qualification']['official_mean'] is None


@pytest.mark.parametrize('fault', ['wrong_sha', 'wrong_core', 'wrong_receipt', 'wrong_world', 'zero_orders',
    'interrupted', 'incomplete_cell', 'failed_cell', 'duplicate', 'unknown_policy', 'undeclared_stress'])
def test_controls_fail_closed_before_writing(qualification, fault):
    kwargs = {}
    if fault == 'wrong_sha': kwargs['expected_controls'] = '0' * 64
    elif fault == 'wrong_core': qualification.controls['pins']['implementation_sha256'] = '0' * 64
    elif fault == 'wrong_receipt': qualification.wrong['policy_registration'] = {'name': 'passive'}
    elif fault == 'wrong_world': qualification.wrong['initial_audit_sha256'] = '0' * 64
    elif fault == 'zero_orders': qualification.wrong['operational_order_count'] = 0
    elif fault == 'interrupted': qualification.controls['status'] = 'incomplete'
    elif fault == 'incomplete_cell': qualification.controls['cells'][0]['status'] = 'incomplete'
    elif fault == 'failed_cell': qualification.controls['cells'][0]['status'] = 'failed'
    elif fault == 'duplicate': qualification.controls['cells'].append(copy.deepcopy(qualification.controls['cells'][0]))
    elif fault == 'unknown_policy': qualification.controls['cells'][0]['policy'] = 'unregistered'
    elif fault == 'undeclared_stress': qualification.protocol['reference_candidates'] = ['dispatcher']
    with pytest.raises(ValueError): qualification.build(**kwargs)
    assert not (qualification.tmp_path / 'qualified').exists()


def test_stress_receipt_must_match_exact_derived_configuration(qualification):
    original = cli.control_registration(qualification.protocol, 'stress')
    assert original['name'] == 'stress' and original['configuration'] == {'name': 'stress'}
    assert original['source_sha256'] == qualification.protocol['weak_active_registration']['source_sha256']
    assert original['dependency_sha256'] == qualification.protocol['weak_active_registration']['dependency_sha256']
    assert qualification.protocol['weak_active_registration']['name'] == 'conservative'


def test_lower_weighted_cost_cannot_buy_more_exposure_in_reference(qualification):
    qualification.outcomes[(TASKS[7], 'stress')] = Outcome(20, 5, 9, 0, 1.)
    result, diagnostic = qualification.build()
    assert cost(qualification.outcomes[(TASKS[7], 'stress')]) < cost(PASSIVE)
    assert result['cases'][TASKS[7]]['reference_policy'] == 'dispatcher'
    assert not diagnostic[TASKS[7]]['qualification']['qualified']


def test_bounded_saved_reader_merges_global_pins_and_recounts_registered_orders(terminal, tmp_path, monkeypatch):
    identity, result, saved = copy.deepcopy(terminal)
    receipt = dict(name='conservative', factory='eval.incident_day_policies:make_policy',
        configuration={'name': 'conservative'}, source_sha256='1' * 64,
        dependency_sha256={'baselines.py': '2' * 64},
        **{key: identity[key] for key in ('implementation_sha256', 'observation_sha256', 'interface_version')})
    result.update(policy='conservative', policy_registration=copy.deepcopy(receipt))
    result['pins']['policy_registration_sha256'] = cli.seal(receipt)
    saved['policy_registration'] = copy.deepcopy(receipt)
    saved['trajectory']['transitions'][0].update(valid=True,
        action={'protection_orders': [dict(group_id='g', action='shelter', destination_id=None, approach_id=None)]})
    global_pins = {key: result['pins'].pop(key) for key in ('implementation_sha256', 'observation_sha256')}
    result['runtime_id'] = 'es-ma-2021'
    row = dict(task_id=identity['task_id'], runtime_id='es-ma-2021')
    path = tmp_path / 'report.json'
    (tmp_path / 'private').mkdir()
    name = f"es-ma-2021-{identity['task_id']}-conservative.json.gz"
    raw = gzip.compress(cli.canonical(saved), mtime=0)
    (tmp_path / 'private' / name).write_bytes(raw)
    monkeypatch.setattr(cli, 'headroom', lambda: None)
    _, meta, actual_name, source_sha = cli.read_execution(path, {'pins': global_pins}, row, result, identity)
    assert actual_name == name and source_sha == hashlib.sha256(raw).hexdigest()
    assert meta['operational_order_count'] == 1 and meta['policy_registration'] == receipt


@pytest.mark.parametrize('fault', [
    'validator', 'missing_validator', 'receipt_map_missing_stress', 'receipt_map_hash',
    'changed_registration_rehashed', 'declared_missing', 'declared_extra', 'declared_duplicate',
    'actual_missing', 'summary_expected', 'summary_finished', 'summary_bool', 'summary_statuses',
    'complete_with_blocked_cell',
])
def test_controls_campaign_pins_domain_and_completion_must_reconcile(qualification, fault):
    controls = qualification.controls
    if fault == 'validator':
        controls['pins']['validator_sha256'] = '0' * 64
    elif fault == 'missing_validator':
        controls['pins'].pop('validator_sha256')
    elif fault == 'receipt_map_missing_stress':
        controls['policy_registrations'].pop('stress')
        controls['pins']['policy_registrations_sha256'] = cli.seal(controls['policy_registrations'])
    elif fault == 'receipt_map_hash':
        controls['pins']['policy_registrations_sha256'] = '0' * 64
    elif fault == 'changed_registration_rehashed':
        controls['policy_registrations']['stress']['source_sha256'] = '0' * 64
        controls['pins']['policy_registrations_sha256'] = cli.seal(controls['policy_registrations'])
    elif fault == 'declared_missing':
        controls['expected_cells'].pop()
    elif fault == 'declared_extra':
        controls['expected_cells'].append(dict(runtime_id='es-ma-2021', case_id=TASKS[0], policy='stress'))
    elif fault == 'declared_duplicate':
        controls['expected_cells'][0] = copy.deepcopy(controls['expected_cells'][1])
    elif fault == 'actual_missing':
        controls['cells'].pop()
    elif fault == 'summary_expected':
        controls['summary']['expected'] -= 1
    elif fault == 'summary_finished':
        controls['summary']['finished'] -= 1
    elif fault == 'summary_bool':
        controls['summary']['finished'] = True
    elif fault == 'summary_statuses':
        controls['summary']['statuses'] = {'complete': 24, 'blocked': 1}
    elif fault == 'complete_with_blocked_cell':
        controls['cells'][0]['status'] = 'blocked'
        controls['summary']['statuses'] = {'complete': 24, 'blocked': 1}
    with pytest.raises(ValueError):
        qualification.build(reconcile=False)
    assert not (qualification.tmp_path / 'qualified').exists()


def test_genuinely_sparse_domain_still_requires_both_receipts_and_all_weak_cases(qualification):
    # A complete explicit stress-only domain supplies no conservative coverage.
    # Its runner context still carries both preregistered policy receipts.
    controls = qualification.controls
    controls['cells'] = [cell for cell in controls['cells'] if cell['policy'] == 'stress']
    controls['expected_cells'] = [{key: item[key] for key in ('runtime_id', 'case_id', 'policy')}
                                  for item in controls['cells']]
    controls['summary'] = dict(expected=1, finished=1, statuses={'complete': 1})
    qualification.protocol['status'] = 'released'
    result, _ = qualification.build(reconcile=False)
    assert result['cases'][TASKS[7]]['reference_policy'] == 'stress'
    assert result['qualification']['missing_tasks']['weak_active'] == TASKS
    assert not result['qualification']['release_ready']
    assert result['qualification']['official_mean'] is None
