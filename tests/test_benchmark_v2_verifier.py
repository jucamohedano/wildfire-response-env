"""Synthetic evaluator-only verifier tests for remote CI; no runtime imports."""
import copy
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_benchmark_v2/src'))

from wildfire_benchmark_v2 import BenchmarkVerifier, Outcome, cost
from wildfire_benchmark_v2.evidence import REQUIRED_COVERAGE
from wildfire_benchmark_v2.verifier import grading_sources

TASKS = [f'incident-day-evaluation-v1-{i:03}' for i in range(24)]
PASSIVE = Outcome(20, 4, 9, 4, 1.0)
REFERENCE = Outcome(20, 0, 9, 0, 1.0)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def seal(manifest):
    manifest['sha256'] = sha({k: v for k, v in manifest.items() if k != 'sha256'})
    return manifest


def metadata(identity, initial_hash, outcome):
    return dict(identity=copy.deepcopy(identity), initial_audit_sha256=initial_hash,
                source_execution_verified=True, terminal_counts_verified=True,
                cost=cost(outcome), raw_reward=1 - cost(outcome), adverse_flags=[])


@pytest.fixture
def terminal():
    identity = dict(task_id=TASKS[0], seed=42, scenario_sha256='c' * 64,
                    inputs_sha256='d' * 64, network_sha256='e' * 64,
                    implementation_sha256='a' * 64, observation_sha256='b' * 64,
                    interface_version='wildfire-incident-day-ops-v3')
    audit = [dict(minute=i, population={'g': dict(count=20, exposed=0 if i < 10 else 2,
        status='at_home', protection_pending=False)}, new_footprint=[1, 2] if i == 10 else [],
        resources={'crew': dict(state='available', position=[0, 0], duty_end_at=960)}, applied_work=[])
        for i in range(1441)]
    audit[0].update(footprint=[0], burnable=list(range(10)), cell_area_hectares=1.0)
    outcome = Outcome(20, 2, 9, 2, 1.0)
    raw = dict(reward_version='wildfire-incident-day-outcomes-v2', graded_score=1 - cost(outcome),
        protected_population_fraction=.9, additional_burned_hectares=2.0, inherited_burned_hectares=1.0,
        inherited_exposed_population=0, new_exposed_population=2, unresolved_evacuations=0,
        safe_success=False, score_eligible=True, boundary_censored=False,
        invalid_abandonment=False, completed_minutes=1440)
    checks = [dict(boundary='initial', passed=True)]
    verified = {k: True for k in ('passed', 'exact_replay', 'independent_reward_agreement')}
    coverage = {k: dict(status='not_exercised', observed=0) for k in REQUIRED_COVERAGE}
    coverage.update(capacity_each_tick=dict(status='complete', observed=1440),
                    facility_conservation=dict(status='complete', observed=1),
                    movement_paths=dict(status='complete', observed=5))
    movement = dict(ticks_observed=1440, capacity_ticks_checked=1440, timed_positions_checked=5)
    result = dict(case_id=TASKS[0], status='complete', minute=1440, split='evaluation', training_eligible=False,
        pins={k: v for k, v in identity.items() if k.endswith('_sha256')},
        verification=copy.deepcopy(verified), query_independence={'passed': True},
        resume_checks=copy.deepcopy(checks), outcome=copy.deepcopy(raw),
        operation_coverage=coverage, movement_evidence=movement, findings=[])
    saved = dict(episode_seed=42, audit=audit, reports=[], findings=[],
        outcome=copy.deepcopy(raw), verification=dict(verified, outcomes=copy.deepcopy(raw)),
        resume_checks=copy.deepcopy(checks), movement_observer=dict(findings={}, coverage=copy.deepcopy(movement)),
        trajectory=dict(task_id=TASKS[0], seed=42, scenario_sha256=identity['scenario_sha256'],
            inputs_sha256=identity['inputs_sha256'], network_sha256=identity['network_sha256'],
            implementation_sha256=identity['observation_sha256'], interface_version=identity['interface_version'],
            initial_observation=dict(task_id='incident-day-evaluation-v1', minute=0,
                interface_version=identity['interface_version'], resources=[dict(id='crew', kind='crew',
                    base_position=[0, 0], duty_end_at=960)]),
            transitions=[dict(start_minute=0, minute=1440, terminated=True, truncated=False,
                              reward=raw['graded_score'])]))
    return identity, result, saved


@pytest.fixture
def anchors(terminal):
    identity, _, saved = terminal
    initial_hash = sha(saved['audit'][0])
    protocol = json.loads((ROOT / 'data/benchmark_v2/protocol.json').read_text())
    families = protocol.get('families', [f'family-{i}' for i in range(6)])
    cases = {}
    for i, task in enumerate(TASKS):
        expected = dict(identity, task_id=task)
        cases[task] = dict(identity=expected, family=families[i // 4], initial_audit_sha256=initial_hash,
            passive_outcome=asdict(PASSIVE), reference_outcome=asdict(REFERENCE),
            reference_policy='dispatcher', reference_kind='best_observed_scripted_online',
            reference_proven_optimal=False,
            passive_metadata=metadata(expected, initial_hash, PASSIVE),
            reference_metadata=metadata(expected, initial_hash, REFERENCE),
            source_evidence_sha256={'passive': '1' * 64, 'dispatcher': '2' * 64})
    return seal(dict(version='wildfire-benchmark-anchors-v2',
        protocol=protocol, protocol_sha256=sha(protocol),
        expected_tasks=TASKS[:], cases=cases, source_report_sha256='3' * 64,
        grading_sources=grading_sources()))


def ready(anchors, *, released=True):
    registration = anchors['protocol']['weak_active_registration']
    identity = anchors['cases'][TASKS[0]]['identity']
    for key in ('implementation_sha256', 'observation_sha256', 'interface_version'):
        registration[key] = identity[key]
    for case in anchors['cases'].values():
        case['weak_active_outcome'] = asdict(PASSIVE)
        case['weak_active_metadata'] = metadata(case['identity'], case['initial_audit_sha256'], PASSIVE)
        case['weak_active_metadata']['operational_order_count'] = 1
        case['weak_active_metadata']['policy_registration'] = copy.deepcopy(registration)
        case['weak_active_metadata']['policy_registration_sha256'] = sha(registration)
        case['weak_active_evidence_sha256'] = '4' * 64
        case['weak_active_policy'] = registration['name']
    anchors['protocol']['status'] = 'released' if released else 'candidate_not_released'
    anchors['protocol_sha256'] = sha(anchors['protocol'])
    return seal(anchors)


def test_default_candidate_is_unscored_and_explicit_candidate_is_diagnostic_only(anchors, terminal):
    _, result, saved = terminal
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    assert not verifier.release_ready
    default = verifier.verify_run(TASKS[0], result, saved)
    assert default['benchmark_score'] is default['diagnostic_grade'] is default['raw_gain'] is None
    assert default['unscored_reason'] == 'benchmark_not_released'
    diagnostic = verifier.verify_run(TASKS[0], result, saved, allow_candidate=True)
    assert diagnostic['candidate'] and not diagnostic['released']
    assert diagnostic['benchmark_score'] is None and diagnostic['diagnostic_grade'] == pytest.approx(.5)
    assert diagnostic['raw_reward_diagnostic'] == result['outcome']['graded_score']
    assert diagnostic['raw_gain'] == pytest.approx(.5)
    assert diagnostic['reference_kind'] == 'best_observed_scripted_online'
    assert diagnostic['reference_proven_optimal'] is False
    assert 'audit' not in diagnostic and 'trajectory' not in diagnostic


def test_complete_release_gate_allows_official_score(anchors, terminal):
    ready(anchors)
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    assert verifier.release_ready
    result = verifier.verify_run(TASKS[0], terminal[1], terminal[2])
    assert result['released'] and not result['candidate']
    assert result['benchmark_score'] == pytest.approx(.5) and result['diagnostic_grade'] is None
    assert result['manifest_sha256'] == anchors['sha256']


def test_numerically_qualified_candidate_still_has_no_official_score(anchors, terminal):
    ready(anchors, released=False)
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    assert verifier.qualification_ready and not verifier.release_ready
    assert verifier.release_gate['qualification_ready']
    assert verifier.release_gate['official_mean'] is None
    result = verifier.verify_run(TASKS[0], terminal[1], terminal[2], allow_candidate=True)
    assert result['benchmark_score'] is None and result['diagnostic_grade'] == pytest.approx(.5)
    assert result['candidate'] and not result['released']


@pytest.mark.parametrize('status', ['ready', 'candidate', True, None])
def test_protocol_release_status_must_be_known(anchors, status):
    anchors['protocol']['status'] = status
    anchors['protocol_sha256'] = sha(anchors['protocol'])
    seal(anchors)
    with pytest.raises(ValueError, match='protocol release status'):
        BenchmarkVerifier(anchors, anchors['sha256'])


def test_cached_ready_boolean_does_not_replace_missing_weak_evidence(anchors):
    anchors['release_ready'] = True
    anchors['qualification'] = {'release_ready': True}
    seal(anchors)
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    assert not verifier.release_ready
    assert verifier.release_gate['missing_tasks']['weak_active'] == sorted(TASKS)


def test_outcome_only_weak_active_cannot_make_the_benchmark_released(anchors):
    for case in anchors['cases'].values():
        case['weak_active_outcome'] = asdict(PASSIVE)
    seal(anchors)
    with pytest.raises(ValueError, match='Weak active outcome requires'):
        BenchmarkVerifier(anchors, anchors['sha256'])


@pytest.mark.parametrize('fault', ['metadata', 'hash', 'policy', 'same_passive', 'same_reference',
    'unverified', 'wrong_identity', 'wrong_initial', 'wrong_cost', 'invalid_hash'])
def test_weak_active_provenance_is_required_and_bound(anchors, fault):
    ready(anchors)
    case = anchors['cases'][TASKS[0]]
    if fault in ('metadata', 'hash', 'policy'):
        key = {'metadata': 'weak_active_metadata', 'hash': 'weak_active_evidence_sha256',
               'policy': 'weak_active_policy'}[fault]
        del case[key]
    elif fault == 'same_passive': case['weak_active_policy'] = 'passive'
    elif fault == 'same_reference': case['weak_active_policy'] = 'dispatcher'
    elif fault == 'unverified': case['weak_active_metadata']['source_execution_verified'] = False
    elif fault == 'wrong_identity': case['weak_active_metadata']['identity']['seed'] += 1
    elif fault == 'wrong_initial': case['weak_active_metadata']['initial_audit_sha256'] = '0' * 64
    elif fault == 'wrong_cost': case['weak_active_metadata']['cost'] = 0
    elif fault == 'invalid_hash': case['weak_active_evidence_sha256'] = 'invalid'
    seal(anchors)
    with pytest.raises(ValueError):
        BenchmarkVerifier(anchors, anchors['sha256'])


@pytest.mark.parametrize('count', [0, -1, True, False, 1.0, '1', None])
def test_weak_active_requires_actual_operational_orders(anchors, count):
    ready(anchors)
    anchors['cases'][TASKS[0]]['weak_active_metadata']['operational_order_count'] = count
    seal(anchors)
    with pytest.raises(ValueError, match='positive integer operational_order_count'):
        BenchmarkVerifier(anchors, anchors['sha256'])


def test_weak_active_missing_operational_order_count_is_rejected(anchors):
    ready(anchors)
    del anchors['cases'][TASKS[0]]['weak_active_metadata']['operational_order_count']
    seal(anchors)
    with pytest.raises(ValueError, match='positive integer operational_order_count'):
        BenchmarkVerifier(anchors, anchors['sha256'])


@pytest.mark.parametrize('fault', ['missing_registration', 'missing_source', 'bad_source', 'missing_baselines',
    'bad_dependency', 'bad_factory', 'wrong_configuration', 'bad_core', 'bad_observation', 'missing_interface'])
def test_protocol_requires_complete_well_formed_weak_registration(anchors, fault):
    registration = anchors['protocol']['weak_active_registration']
    if fault == 'missing_registration': del anchors['protocol']['weak_active_registration']
    elif fault == 'missing_source': del registration['source_sha256']
    elif fault == 'bad_source': registration['source_sha256'] = 'not-a-sha'
    elif fault == 'missing_baselines': registration['dependency_sha256'] = {}
    elif fault == 'bad_dependency': registration['dependency_sha256']['baselines.py'] = True
    elif fault == 'bad_factory': registration['factory'] = 'not a callable reference'
    elif fault == 'wrong_configuration': registration['configuration']['name'] = 'renamed'
    elif fault == 'bad_core': registration['implementation_sha256'] = 'bad'
    elif fault == 'bad_observation': registration['observation_sha256'] = 1
    elif fault == 'missing_interface': del registration['interface_version']
    anchors['protocol_sha256'] = sha(anchors['protocol'])
    seal(anchors)
    with pytest.raises(ValueError):
        BenchmarkVerifier(anchors, anchors['sha256'])


@pytest.mark.parametrize('fault', ['missing_receipt', 'missing_receipt_hash', 'malformed_receipt_hash',
    'wrong_receipt_hash', 'wrong_source', 'wrong_configuration', 'wrong_dependency',
    'different_policy', 'core_mismatch', 'observation_mismatch', 'interface_mismatch'])
def test_weak_execution_must_match_frozen_policy_registration(anchors, fault):
    ready(anchors)
    case = anchors['cases'][TASKS[0]]
    receipt = case['weak_active_metadata']['policy_registration']
    if fault == 'missing_receipt': del case['weak_active_metadata']['policy_registration']
    elif fault == 'missing_receipt_hash': del case['weak_active_metadata']['policy_registration_sha256']
    elif fault == 'malformed_receipt_hash': case['weak_active_metadata']['policy_registration_sha256'] = 'bad'
    elif fault == 'wrong_receipt_hash': case['weak_active_metadata']['policy_registration_sha256'] = '0' * 64
    elif fault == 'wrong_source': receipt['source_sha256'] = '0' * 64
    elif fault == 'wrong_configuration': receipt['configuration']['name'] = 'renamed'
    elif fault == 'wrong_dependency': receipt['dependency_sha256']['baselines.py'] = '0' * 64
    elif fault == 'different_policy': case['weak_active_policy'] = 'another_active_policy'
    elif fault in ('core_mismatch', 'observation_mismatch', 'interface_mismatch'):
        key = {'core_mismatch': 'implementation_sha256', 'observation_mismatch': 'observation_sha256',
               'interface_mismatch': 'interface_version'}[fault]
        anchors['protocol']['weak_active_registration'][key] = 'other-interface' if key == 'interface_version' else 'f' * 64
        for item in anchors['cases'].values():
            item['weak_active_metadata']['policy_registration'] = copy.deepcopy(anchors['protocol']['weak_active_registration'])
            item['weak_active_metadata']['policy_registration_sha256'] = sha(anchors['protocol']['weak_active_registration'])
    if fault in ('wrong_source', 'wrong_configuration', 'wrong_dependency'):
        case['weak_active_metadata']['policy_registration_sha256'] = sha(receipt)
    anchors['protocol_sha256'] = sha(anchors['protocol'])
    seal(anchors)
    with pytest.raises(ValueError):
        BenchmarkVerifier(anchors, anchors['sha256'])


def test_one_zero_headroom_task_blocks_whole_release_and_has_no_diagnostic_grade(anchors, terminal):
    case = anchors['cases'][TASKS[0]]
    case['reference_outcome'] = asdict(PASSIVE)
    case['reference_metadata'] = metadata(case['identity'], case['initial_audit_sha256'], PASSIVE)
    ready(anchors)
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    assert not verifier.release_ready
    assert verifier.release_gate['unqualified_tasks'] == {TASKS[0]: 'no_positive_cost_headroom'}
    default = verifier.verify_run(TASKS[0], terminal[1], terminal[2])
    assert default['unscored_reason'] == default['qualification_reason'] == 'no_positive_cost_headroom'
    diagnostic = verifier.verify_run(TASKS[0], terminal[1], terminal[2], allow_candidate=True)
    assert not diagnostic['qualified']
    assert diagnostic['qualification_reason'] == diagnostic['unscored_reason'] == 'no_positive_cost_headroom'
    assert diagnostic['benchmark_score'] is diagnostic['diagnostic_grade'] is diagnostic['raw_gain'] is None


@pytest.mark.parametrize('fault', ['wrong_expected_hash', 'stale_hash', 'bad_protocol_hash', 'bad_version',
    'missing_case', 'extra_case', 'duplicate_task', 'wrong_family', 'wrong_core', 'wrong_observation',
    'wrong_input', 'wrong_network', 'wrong_interface', 'wrong_task', 'bool_seed', 'malformed_hash',
    'fake_optimum', 'missing_provenance', 'bad_source_hash', 'missing_metadata', 'unverified_anchor',
    'wrong_anchor_identity', 'wrong_anchor_initial', 'wrong_anchor_cost', 'changed_denominator',
    'missing_grading_sources', 'wrong_grading_sources', 'partial_grading_sources', 'changed_weights'])
def test_anchor_domain_protocol_sources_and_invariance_are_strict(anchors, fault):
    first, other = anchors['cases'][TASKS[0]], anchors['cases'][TASKS[1]]
    expected_hash = None
    if fault == 'wrong_expected_hash': expected_hash = '0' * 64
    elif fault == 'stale_hash':
        expected_hash = anchors['sha256']; first['reference_policy'] = 'changed'
    elif fault == 'bad_protocol_hash': anchors['protocol_sha256'] = '0' * 64
    elif fault == 'bad_version': anchors['version'] = 'other'
    elif fault == 'missing_case': anchors['cases'].pop(TASKS[-1])
    elif fault == 'extra_case': anchors['cases']['extra'] = copy.deepcopy(first)
    elif fault == 'duplicate_task': anchors['expected_tasks'][-1] = TASKS[0]
    elif fault == 'wrong_family': other['family'] = 'family-5'
    elif fault in ('wrong_core', 'wrong_observation', 'wrong_input', 'wrong_network', 'wrong_interface'):
        key = {'wrong_core': 'implementation_sha256', 'wrong_observation': 'observation_sha256',
               'wrong_input': 'inputs_sha256', 'wrong_network': 'network_sha256',
               'wrong_interface': 'interface_version'}[fault]
        other['identity'][key] = 'different' if key == 'interface_version' else 'f' * 64
        for name in ('passive_metadata', 'reference_metadata'):
            other[name]['identity'] = copy.deepcopy(other['identity'])
    elif fault == 'wrong_task': first['identity']['task_id'] = TASKS[1]
    elif fault == 'bool_seed': first['identity']['seed'] = True
    elif fault == 'malformed_hash': first['initial_audit_sha256'] = 'invalid'
    elif fault == 'fake_optimum': first['reference_proven_optimal'] = True
    elif fault == 'missing_provenance': first['source_evidence_sha256'] = {}
    elif fault == 'bad_source_hash': first['source_evidence_sha256']['passive'] = 'invalid'
    elif fault == 'missing_metadata': del first['reference_metadata']
    elif fault == 'unverified_anchor': first['reference_metadata']['source_execution_verified'] = False
    elif fault == 'wrong_anchor_identity': first['reference_metadata']['identity']['seed'] += 1
    elif fault == 'wrong_anchor_initial': first['reference_metadata']['initial_audit_sha256'] = '0' * 64
    elif fault == 'wrong_anchor_cost': first['reference_metadata']['cost'] = .5
    elif fault == 'changed_denominator': first['reference_outcome']['eligible_population'] = 21
    elif fault == 'missing_grading_sources': del anchors['grading_sources']
    elif fault == 'wrong_grading_sources': anchors['grading_sources']['grading.py'] = '0' * 64
    elif fault == 'partial_grading_sources': del anchors['grading_sources']['verifier.py']
    elif fault == 'changed_weights':
        anchors['protocol']['objective']['population_weight'] = .9
        anchors['protocol_sha256'] = sha(anchors['protocol'])
    if fault not in ('stale_hash',): seal(anchors)
    with pytest.raises(ValueError):
        BenchmarkVerifier(anchors, anchors['sha256'] if expected_hash is None else expected_hash)


@pytest.mark.parametrize('fault', ['wrong_case', 'wrong_seed', 'wrong_core', 'wrong_observation',
    'wrong_initial', 'missing_audit', 'incomplete', 'abandoned', 'replay_failure', 'observer_failure'])
def test_missing_mismatched_or_incomplete_run_never_becomes_zero_or_ranked(anchors, terminal, fault):
    _, result, saved = terminal
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    if fault == 'wrong_case': result['case_id'] = TASKS[1]
    elif fault == 'wrong_seed': saved['episode_seed'] += 1
    elif fault == 'wrong_core': result['pins']['implementation_sha256'] = 'f' * 64
    elif fault == 'wrong_observation': saved['trajectory']['implementation_sha256'] = 'f' * 64
    elif fault == 'wrong_initial': saved['audit'][0]['extra'] = 'changed initial world'
    elif fault == 'missing_audit': del saved['audit']
    elif fault == 'incomplete': saved['audit'].pop()
    elif fault == 'abandoned': result['outcome']['invalid_abandonment'] = True
    elif fault == 'replay_failure': result['verification']['exact_replay'] = False
    elif fault == 'observer_failure': saved['movement_observer']['findings'] = {'off_path': {'count': 1}}
    with pytest.raises(ValueError):
        verifier.verify_run(TASKS[0], result, saved)


def test_verified_terminal_adverse_flag_is_separate_from_cost(anchors, terminal):
    _, result, saved = terminal
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    normal = verifier.verify_run(TASKS[0], result, saved)
    for row in saved['audit'][600:]:
        row['resources']['crew'].update(state='needs_assistance', position=[10, 0])
    saved['reports'] = [dict(kind='egress_unavailable', observed_at=600,
        payload=dict(resource_id='crew', position=[10, 0]))]
    finding = dict(category='unsupported', code='terminal_assistance_after_shift', resource='crew', minute=1440)
    saved['findings'] = [finding]
    result.update(status='blocked', findings=[dict(category='unsupported', code=finding['code'], count=1)])
    adverse = verifier.verify_run(TASKS[0], result, saved)
    assert adverse['benchmark_score'] == normal['benchmark_score']
    assert adverse['costs'] == normal['costs']
    assert adverse['adverse_flags'] == ['terminal_assistance_after_shift']
    assert adverse['operational_status'] == 'unresolved_at_horizon'


def test_manifest_and_release_gate_are_detached_from_callers(anchors, terminal):
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    gate = verifier.release_gate
    gate['release_ready'] = False
    anchors['cases'].clear()
    assert verifier.release_ready
    before = copy.deepcopy(terminal)
    result = verifier.verify_run(TASKS[0], terminal[1], terminal[2])
    assert result['benchmark_score'] == pytest.approx(.5)
    assert terminal == before
    result['identity']['task_id'] = 'caller mutation'
    assert verifier.verify_run(TASKS[0], terminal[1], terminal[2])['task_id'] == TASKS[0]


def test_mid_session_source_change_is_rejected(anchors, terminal, monkeypatch):
    import wildfire_benchmark_v2.verifier as module
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    changed = dict(grading_sources(), **{'evidence.py': '0' * 64})
    monkeypatch.setattr(module, 'grading_sources', lambda: changed)
    with pytest.raises(ValueError, match='source identity changed'):
        verifier.verify_run(TASKS[0], terminal[1], terminal[2])


@pytest.fixture
def abandonment(terminal):
    _, result, saved = terminal
    raw = dict(result['outcome'], graded_score=0.0, invalid_abandonment=True, completed_minutes=5,
               protected_population_fraction=1.0, additional_burned_hectares=0.0, new_exposed_population=0)
    result.update(status='blocked', minute=5, outcome=copy.deepcopy(raw))
    saved['outcome'] = copy.deepcopy(raw)
    saved['verification']['outcomes'] = copy.deepcopy(raw)
    saved['audit'] = saved['audit'][:6]
    saved['trajectory']['transitions'] = [
        dict(start_minute=0, minute=2, valid=True, terminated=False, truncated=False, reward=0.0),
        dict(start_minute=2, minute=3, valid=False, terminated=False, truncated=False, reward=0.0),
        dict(start_minute=3, minute=4, valid=False, terminated=False, truncated=False, reward=0.0),
        dict(start_minute=4, minute=5, valid=False, terminated=False, truncated=True, reward=0.0),
    ]
    result['operation_coverage']['capacity_each_tick']['observed'] = 5
    result['movement_evidence'].update(ticks_observed=5, capacity_ticks_checked=5)
    saved['movement_observer']['coverage'] = copy.deepcopy(result['movement_evidence'])
    return result, saved


def test_verified_abandonment_is_zero_only_under_applicable_release_or_candidate_gate(anchors, abandonment):
    result, saved = abandonment
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    default = verifier.verify_run(TASKS[0], result, saved)
    assert default['benchmark_score'] is default['diagnostic_grade'] is None
    diagnostic = verifier.verify_run(TASKS[0], result, saved, allow_candidate=True)
    assert diagnostic['candidate'] and not diagnostic['released']
    assert diagnostic['benchmark_score'] is None and diagnostic['diagnostic_grade'] == 0.0
    assert diagnostic['policy_failure']['failure_kind'] == 'invalid_action_abandonment'
    assert diagnostic['policy_failure']['invalid_action_minutes'] == [3, 4, 5]
    assert diagnostic['policy_failure']['observed_through_minute'] == 5
    assert diagnostic['raw_reward_diagnostic'] == 0.0
    assert diagnostic['outcome'] is diagnostic['costs']['agent'] is diagnostic['weighted_components']['agent'] is None
    assert diagnostic['raw_gain'] is diagnostic['cost_improvement'] is diagnostic['reference_exceeded'] is None
    released = BenchmarkVerifier(ready(anchors), anchors['sha256']).verify_run(TASKS[0], result, saved)
    assert released['benchmark_score'] == 0.0 and released['diagnostic_grade'] is None
    assert released['operational_status'] == 'policy_failure'


def test_policy_failure_does_not_create_a_score_for_unqualified_reference(anchors, abandonment):
    case = anchors['cases'][TASKS[0]]
    case['reference_outcome'] = asdict(PASSIVE)
    case['reference_metadata'] = metadata(case['identity'], case['initial_audit_sha256'], PASSIVE)
    ready(anchors)
    verifier = BenchmarkVerifier(anchors, anchors['sha256'])
    result = verifier.verify_run(TASKS[0], *abandonment, allow_candidate=True)
    assert result['benchmark_score'] is result['diagnostic_grade'] is None
    assert not result['qualified'] and result['qualification_reason'] == 'no_positive_cost_headroom'


@pytest.mark.parametrize('fault', ['provider_crash', 'missing_saved', 'replay', 'raw_reward', 'last_valid',
    'two_minute_invalid', 'not_truncated', 'audit_gap', 'identity', 'initial_audit', 'inconsistent_claim'])
def test_no_other_failure_can_be_converted_to_policy_zero(anchors, abandonment, fault):
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    result, saved = abandonment
    if fault == 'provider_crash':
        for outcome in (result['outcome'], saved['outcome'], saved['verification']['outcomes']):
            outcome['invalid_abandonment'] = False
    elif fault == 'missing_saved': saved = {}
    elif fault == 'replay': saved['verification']['exact_replay'] = False
    elif fault == 'raw_reward': result['outcome']['graded_score'] = .1
    elif fault == 'last_valid': saved['trajectory']['transitions'][-2]['valid'] = True
    elif fault == 'two_minute_invalid': saved['trajectory']['transitions'][-2]['minute'] = 5
    elif fault == 'not_truncated': saved['trajectory']['transitions'][-1]['truncated'] = False
    elif fault == 'audit_gap': saved['audit'][3]['minute'] = 4
    elif fault == 'identity': saved['episode_seed'] += 1
    elif fault == 'initial_audit': saved['audit'][0]['changed'] = True
    elif fault == 'inconsistent_claim': result['outcome']['invalid_abandonment'] = False
    with pytest.raises(ValueError):
        verifier.verify_run(TASKS[0], result, saved, allow_candidate=True)


def test_future_failure_extensions_are_not_silently_accepted(anchors, terminal, monkeypatch):
    import wildfire_benchmark_v2.evidence as module
    verifier = BenchmarkVerifier(ready(anchors), anchors['sha256'])
    monkeypatch.setattr(module, 'extract_verified_policy_failure', lambda *args: {
        'version': 'wildfire-verified-policy-failure-v1', 'failure_kind': 'provider_crash',
        'verified_policy_failure': True})
    with pytest.raises(ValueError, match='verified abandonment evidence'):
        verifier.verify_run(TASKS[0], terminal[1], terminal[2])


@pytest.mark.parametrize('module_name', ['wildfire_benchmark_v2', 'wildfire_benchmark_v2.grading',
    'wildfire_benchmark_v2.evidence', 'wildfire_benchmark_v2.verifier'])
@pytest.mark.parametrize('attribute,value', [('_LOADED_CODE_MATCH', False),
                                          ('_LOADED_SOURCE_SHA256', '0' * 64)])
def test_all_four_loaded_modules_must_match_source(monkeypatch, module_name, attribute, value):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, attribute, value)
    with pytest.raises(ValueError, match='loaded code or source differs'):
        grading_sources()


def test_changed_disk_bytes_are_rejected_without_writing_package_sources(monkeypatch):
    import wildfire_benchmark_v2.verifier as module
    source = Path(module.__file__).parent / 'grading.py'
    read = Path.read_bytes
    def changed(path):
        data = read(path)
        return data + b'\n# synthetic disk drift\n' if path == source else data
    monkeypatch.setattr(Path, 'read_bytes', changed)
    with pytest.raises(ValueError, match='loaded code or source differs'):
        grading_sources()


@pytest.mark.parametrize('target', ['wildfire_benchmark_v2', 'wildfire_benchmark_v2.grading',
    'wildfire_benchmark_v2.evidence', 'wildfire_benchmark_v2.verifier'])
def test_stale_loader_code_cannot_receive_current_source_pins(tmp_path, target):
    code = f'''
import importlib, importlib.machinery, json
original = importlib.machinery.SourceFileLoader.get_code
def mismatched(loader, name):
    if name == {target!r}:
        return compile(loader.get_source(name) + "\\n_STALE_CODE_FOR_IDENTITY_TEST = True\\n", loader.path, 'exec')
    return original(loader, name)
importlib.machinery.SourceFileLoader.get_code = mismatched
from wildfire_benchmark_v2.verifier import grading_sources
assert importlib.import_module({target!r})._LOADED_CODE_MATCH is False
try:
    grading_sources()
except ValueError as error:
    assert 'loaded code or source differs' in str(error)
    print(json.dumps({{'rejected': True}}))
else:
    raise AssertionError('Stale loaded code received current source pins')
'''
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=str(ROOT / 'envs/wildfire_benchmark_v2/src'))
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, check=True, timeout=20)
    assert json.loads(result.stdout) == {'rejected': True}


def test_cli_verifies_pinned_saved_run_writes_private_candidate_and_rejects_wrong_sha(
        anchors, terminal, tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    import gzip
    from eval import grade_benchmark_v2 as cli

    # Host memory availability is not part of this synthetic evaluator contract.
    # All parsing, hash binding, extraction, grading and output paths stay real.
    monkeypatch.setattr(cli, 'headroom', lambda: None)
    identity, cell, saved = terminal
    cell = copy.deepcopy(cell)
    cell['policy'] = 'dispatcher'
    cell['pins'] = {key: identity[key] for key in ('inputs_sha256', 'network_sha256', 'scenario_sha256')}
    report = dict(pins={key: identity[key] for key in ('implementation_sha256', 'observation_sha256')},
                  cells=[cell])
    manifest_path, report_path = tmp_path / 'anchors.json', tmp_path / 'report.json'
    saved_path, output = tmp_path / 'saved.json.gz', tmp_path / 'verdict.json'
    manifest_path.write_text(json.dumps(anchors, sort_keys=True, allow_nan=False))
    report_raw = json.dumps(report, sort_keys=True, allow_nan=False).encode()
    saved_raw = gzip.compress(json.dumps(saved, sort_keys=True, allow_nan=False).encode(), mtime=0)
    report_path.write_bytes(report_raw)
    saved_path.write_bytes(saved_raw)
    original = {path: path.read_bytes() for path in (manifest_path, report_path, saved_path)}
    args = Namespace(manifest=manifest_path, source_report=report_path, saved_run=saved_path, output=output,
        expected_manifest_sha256=anchors['sha256'], expected_source_report_sha256=hashlib.sha256(report_raw).hexdigest(),
        expected_saved_sha256=hashlib.sha256(saved_raw).hexdigest(), task_id=TASKS[0], policy='dispatcher',
        allow_candidate_diagnostic=True)

    verdict = cli.verify(args)
    assert verdict['candidate'] and not verdict['released']
    assert verdict['benchmark_score'] is None and verdict['diagnostic_grade'] == pytest.approx(.5)
    assert verdict['raw_reward_diagnostic'] == terminal[1]['outcome']['graded_score']
    assert verdict['run_report_sha256'] == args.expected_source_report_sha256
    assert verdict['saved_run_sha256'] == args.expected_saved_sha256
    assert json.loads(output.read_text()) == verdict
    assert output.stat().st_mode & 0o777 == 0o600
    printed = json.loads(capsys.readouterr().out)
    assert printed['benchmark_score'] is None and printed['diagnostic_grade'] == pytest.approx(.5)

    bad = Namespace(**vars(args))
    bad.expected_saved_sha256 = '0' * 64
    bad.output = tmp_path / 'refused.json'
    with pytest.raises(ValueError, match='Saved execution differs'):
        cli.verify(bad)
    assert not bad.output.exists()
    assert {path: path.read_bytes() for path in original} == original
    assert json.loads(output.read_text()) == verdict
