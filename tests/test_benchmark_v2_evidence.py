"""Synthetic terminal-accounting cases for CI; no simulator or runtime imports."""
import copy
import hashlib
import json

import pytest

from wildfire_benchmark_v2.evidence import extract_verified_outcome, REQUIRED_COVERAGE
from wildfire_benchmark_v2.grading import Outcome, cost


@pytest.fixture
def evidence():
    expected = dict(task_id='incident-day-evaluation-v1-000', seed=42,
        scenario_sha256='c' * 64, inputs_sha256='d' * 64, network_sha256='e' * 64,
        implementation_sha256='a' * 64, observation_sha256='b' * 64,
        interface_version='wildfire-incident-day-ops-v3')
    audit = []
    for minute in range(1441):
        row = dict(minute=minute, population={
            'g0': dict(count=10, exposed=2 if minute < 100 else 5, status='at_home', protection_pending=False),
            'g1': dict(count=20, exposed=0, status='at_home', protection_pending=False)},
            new_footprint=[2] if minute == 10 else [3] if minute == 20 else [],
            resources={'crew': dict(state='off_duty' if minute >= 5 else 'available',
                                  position=[0, 0], duty_end_at=2)}, applied_work=[])
        audit.append(row)
    audit[0].update(footprint=[0, 1], burnable=list(range(10)), cell_area_hectares=25.)
    raw = dict(reward_version='wildfire-incident-day-outcomes-v2', graded_score=1 - (.8 * 3 / 28 + .2 * 2 / 8),
        protected_population_fraction=25 / 28, additional_burned_hectares=50., inherited_burned_hectares=50.,
        inherited_exposed_population=2, new_exposed_population=3, unresolved_evacuations=0,
        safe_success=False, score_eligible=True, boundary_censored=False, invalid_abandonment=False,
        completed_minutes=1440)
    checks = [dict(boundary='initial', passed=True)]
    verification = {k: True for k in ('passed', 'exact_replay', 'independent_reward_agreement')}
    coverage = {k: dict(status='not_exercised', observed=0) for k in REQUIRED_COVERAGE}
    coverage['capacity_each_tick'] = dict(status='complete', observed=1440)
    coverage['facility_conservation'] = dict(status='complete', observed=1)
    coverage['movement_paths'] = dict(status='complete', observed=5)
    coverage['physical_access_and_real_clearance'] = dict(status='unsupported', observed=0)
    movement = dict(ticks_observed=1440, capacity_ticks_checked=1440, timed_positions_checked=5)
    result = dict(case_id=expected['task_id'], status='complete', minute=1440, split='evaluation',
        training_eligible=False, pins={k: expected[k] for k in ('inputs_sha256', 'network_sha256',
                    'scenario_sha256', 'implementation_sha256', 'observation_sha256')},
        verification=verification, outcome=raw, resume_checks=checks,
        query_independence={'passed': True}, findings=[], operation_coverage=coverage, movement_evidence=movement)
    saved = dict(trajectory=dict(task_id=expected['task_id'], seed=42,
        scenario_sha256=expected['scenario_sha256'], inputs_sha256=expected['inputs_sha256'],
        network_sha256=expected['network_sha256'], implementation_sha256=expected['observation_sha256'],
        interface_version=expected['interface_version'], initial_observation={
            'task_id': 'incident-day-evaluation-v1', 'interface_version': expected['interface_version'], 'minute': 0,
            'resources': [{'id': 'crew', 'kind': 'crew', 'base_position': [0, 0], 'duty_end_at': 2}]},
        transitions=[dict(start_minute=0, minute=1440, terminated=True, truncated=False, reward=raw['graded_score'])]),
        episode_seed=42, audit=audit, verification=dict(verification, outcomes=copy.deepcopy(raw)),
        outcome=copy.deepcopy(raw), resume_checks=copy.deepcopy(checks), reports=[], findings=[],
        movement_observer={'findings': {}, 'coverage': copy.deepcopy(movement)})
    return result, saved, expected


def extract(evidence):
    return extract_verified_outcome(*evidence)


def test_counts_exclude_inherited_harm_and_accumulate_unique_eligible_cells(evidence):
    before = copy.deepcopy(evidence)
    outcome, meta = extract(evidence)
    assert outcome == Outcome(28, 3, 8, 2, 25.)
    assert meta['raw_reward'] == pytest.approx(1 - cost(outcome))
    assert meta['inherited_exposed'] == 2 and meta['inherited_burned_cells'] == 2
    assert meta['adverse_flags'] == []
    assert evidence == before
    assert len(json.dumps(meta)) < 2500
    assert not any(key in meta for key in ('audit', 'trajectory', 'reports'))
    meta['identity']['task_id'] = 'changed'
    assert evidence[2]['task_id'] != 'changed'


@pytest.mark.parametrize('fault', ['gap', 'short', 'bool_minute', 'cohort_count', 'cohort_roster',
    'reversed_exposure', 'overexposure', 'negative_cell', 'bool_cell', 'bad_area', 'nan_area'])
def test_corrupt_audit_is_rejected(evidence, fault):
    audit = evidence[1]['audit']
    if fault == 'gap': audit[10]['minute'] = 11
    elif fault == 'short': audit.pop()
    elif fault == 'bool_minute': audit[0]['minute'] = False
    elif fault == 'cohort_count': audit[10]['population']['g0']['count'] = 11
    elif fault == 'cohort_roster': audit[10]['population'].pop('g1')
    elif fault == 'reversed_exposure': audit[101]['population']['g0']['exposed'] = 4
    elif fault == 'overexposure': audit[10]['population']['g0']['exposed'] = 11
    elif fault == 'negative_cell': audit[10]['new_footprint'] = [-1]
    elif fault == 'bool_cell': audit[10]['new_footprint'] = [True]
    elif fault == 'bad_area': audit[0]['cell_area_hectares'] = 0
    elif fault == 'nan_area': audit[0]['cell_area_hectares'] = float('nan')
    with pytest.raises(ValueError): extract(evidence)


@pytest.mark.parametrize('fault', ['core', 'observation', 'scenario', 'input', 'network', 'seed',
    'actual_task', 'public_task', 'interface', 'missing_expected'])
def test_identity_is_explicit_even_when_initial_public_task_is_generic(evidence, fault):
    result, saved, expected = evidence
    if fault == 'core': result['pins']['implementation_sha256'] = 'f' * 64
    elif fault == 'observation': saved['trajectory']['implementation_sha256'] = expected['implementation_sha256']
    elif fault == 'scenario': result['pins']['scenario_sha256'] = 'f' * 64
    elif fault == 'input': saved['trajectory']['inputs_sha256'] = 'f' * 64
    elif fault == 'network': saved['trajectory']['network_sha256'] = 'f' * 64
    elif fault == 'seed': saved['episode_seed'] += 1
    elif fault == 'actual_task': saved['trajectory']['task_id'] = 'incident-day-evaluation-v1'
    elif fault == 'public_task': saved['trajectory']['initial_observation']['task_id'] = 'another-case'
    elif fault == 'interface': saved['trajectory']['interface_version'] = 'v2'
    elif fault == 'missing_expected': expected.pop('observation_sha256')
    with pytest.raises(ValueError): extract(evidence)


@pytest.mark.parametrize('fault', ['report_verify', 'saved_verify', 'resume', 'query', 'missing_coverage',
    'unsupported_coverage', 'ticks', 'capacity', 'unverifiable', 'observer_finding', 'missing_observer',
    'truncation', 'censor', 'abandon', 'ineligible', 'unknown_unsupported', 'contradiction', 'failed_status'])
def test_execution_and_coverage_gates_are_not_rewarded_away(evidence, fault):
    result, saved, _ = evidence
    if fault == 'report_verify': result['verification']['exact_replay'] = False
    elif fault == 'saved_verify': saved['verification']['passed'] = False
    elif fault == 'resume': result['resume_checks'][0]['passed'] = False
    elif fault == 'query': result['query_independence']['passed'] = False
    elif fault == 'missing_coverage': result['operation_coverage'].pop('get_reports')
    elif fault == 'unsupported_coverage': result['operation_coverage']['route_options']['status'] = 'unsupported'
    elif fault == 'ticks': result['movement_evidence']['ticks_observed'] = 1439
    elif fault == 'capacity': result['movement_evidence']['capacity_ticks_checked'] = 1439
    elif fault == 'unverifiable': result['movement_evidence']['journey_unverifiable'] = 1
    elif fault == 'observer_finding': saved['movement_observer']['findings'] = {'off_timed_path': {'count': 1}}
    elif fault == 'missing_observer': saved.pop('movement_observer')
    elif fault == 'truncation': saved['trajectory']['transitions'][-1]['truncated'] = True
    elif fault == 'censor': result['outcome']['boundary_censored'] = True
    elif fault == 'abandon': result['outcome']['invalid_abandonment'] = True
    elif fault == 'ineligible': result['outcome']['score_eligible'] = False
    elif fault in ('unknown_unsupported', 'contradiction'):
        result['findings'] = saved['findings'] = [{'code': 'invented', 'category': 'unsupported' if fault == 'unknown_unsupported' else 'contradiction'}]
    elif fault == 'failed_status': result['status'] = 'failed'
    with pytest.raises(ValueError): extract(evidence)


@pytest.mark.parametrize('target,key', [('report', 'graded_score'), ('saved', 'additional_burned_hectares'),
    ('independent', 'new_exposed_population'), ('report', 'inherited_exposed_population')])
def test_report_saved_and_independent_totals_must_match_audit(evidence, target, key):
    result, saved, _ = evidence
    mapping = {'report': result['outcome'], 'saved': saved['outcome'], 'independent': saved['verification']['outcomes']}
    mapping[target][key] += 1
    with pytest.raises(ValueError): extract(evidence)


def make_adverse(evidence, terminal):
    result, saved, _ = evidence
    code = 'terminal_assistance_after_shift' if terminal else 'recovery_after_shift_end_egress_unavailable'
    finding = dict(category='unsupported', code=code, resource='crew', minute=1440 if terminal else 4)
    result['status'] = 'blocked'
    result['findings'] = [dict(category='unsupported', code=code, count=1)]
    saved['findings'] = [finding]
    saved['reports'] = [{'kind': 'egress_unavailable', 'observed_at': 1,
                         'payload': {'resource_id': 'crew', 'position': [100, 0]}}]
    for row in saved['audit'][1:]:
        minute = row['minute']
        row['resources']['crew'].update(
            state='needs_assistance' if terminal or minute < 4 else 'returning' if minute == 4 else 'off_duty',
            position=[100, 0] if terminal or minute < 4 else [50, 0] if minute == 4 else [0, 0])


@pytest.mark.parametrize('terminal,label', [(False, 'adverse'), (True, 'unresolved_at_horizon')])
def test_known_fully_verified_operational_adversity_keeps_counts_and_flags(evidence, terminal, label):
    baseline, _ = extract(evidence)
    make_adverse(evidence, terminal)
    outcome, metadata = extract(evidence)
    assert outcome == baseline
    assert metadata['operational_status'] == label
    assert metadata['adverse_flags'] == [evidence[1]['findings'][0]['code']]
    assert 'safe_success' not in metadata and 'optimal' not in metadata


@pytest.mark.parametrize('fault', ['missing_report', 'late_report', 'moved', 'work', 'reassignment', 'not_recovered'])
def test_adverse_label_requires_causal_stationary_and_terminal_evidence(evidence, fault):
    make_adverse(evidence, terminal=False)
    saved = evidence[1]
    if fault == 'missing_report': saved['reports'] = []
    elif fault == 'late_report': saved['reports'][0]['observed_at'] = 3
    elif fault == 'moved': saved['audit'][2]['resources']['crew']['position'] = [99, 0]
    elif fault == 'work': saved['audit'][2]['applied_work'] = [{'resource_id': 'crew'}]
    elif fault == 'reassignment': saved['audit'][6]['resources']['crew']['state'] = 'available'
    elif fault == 'not_recovered': saved['audit'][-1]['resources']['crew']['position'] = [1, 0]
    with pytest.raises(ValueError): extract(evidence)


def test_terminal_adversity_at_shift_horizon_remains_explicit(evidence):
    make_adverse(evidence, terminal=True)
    saved = evidence[1]
    saved['trajectory']['initial_observation']['resources'][0]['duty_end_at'] = 1440
    for row in saved['audit']:
        row['resources']['crew']['duty_end_at'] = 1440
    outcome, metadata = extract(evidence)
    assert outcome == Outcome(28, 3, 8, 2, 25.)
    assert metadata['operational_status'] == 'unresolved_at_horizon'


def test_no_eligible_population_or_land_contributes_zero_cost(evidence):
    result, saved, _ = evidence
    for row in saved['audit']:
        for group in row['population'].values():
            group['count'] = group['exposed'] = 0
        row['new_footprint'] = []
    saved['audit'][0]['burnable'] = [0, 1]  # Entirely inherited.
    for reported in (result['outcome'], saved['outcome'], saved['verification']['outcomes']):
        reported.update(graded_score=1., protected_population_fraction=1., additional_burned_hectares=0.,
            inherited_exposed_population=0, new_exposed_population=0, safe_success=True)
    saved['trajectory']['transitions'][-1]['reward'] = 1.
    outcome, metadata = extract(evidence)
    assert outcome == Outcome(0, 0, 0, 0, 25.)
    assert metadata['cost'] == 0. and metadata['raw_reward'] == 1.
    # Reference qualification remains grading's job; this API asserts no optimum.
    assert 'qualified' not in metadata and 'optimal' not in metadata


def test_optional_failed_coverage_is_not_silently_ignored(evidence):
    evidence[0]['operation_coverage']['unknown_checker'] = {'status': 'failed', 'observed': 1}
    with pytest.raises(ValueError): extract(evidence)


def test_wholly_missing_terminal_evidence_is_value_error(evidence):
    with pytest.raises(ValueError): extract_verified_outcome(evidence[0], {}, evidence[2])



def test_initial_audit_digest_is_canonical_and_optionally_bound(evidence):
    initial = evidence[1]['audit'][0]
    expected_hash = hashlib.sha256(json.dumps(initial, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    _, metadata = extract(evidence)
    assert metadata['initial_audit_sha256'] == expected_hash
    evidence[1]['audit'][0] = dict(reversed(list(initial.items())))
    assert extract(evidence)[1]['initial_audit_sha256'] == expected_hash
    evidence[2]['initial_audit_sha256'] = expected_hash
    assert extract(evidence)[1]['initial_audit_sha256'] == expected_hash
    evidence[2]['initial_audit_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Initial audit identity'):
        extract(evidence)


@pytest.mark.parametrize('field', ['footprint', 'burnable', 'population', 'resources'])
def test_initial_digest_binds_exact_state_beyond_denominators(evidence, field):
    original, metadata = extract(evidence)
    initial = evidence[1]['audit'][0]
    if field == 'footprint': initial['footprint'] = [0, 4]
    elif field == 'burnable': initial['burnable'][-1] = 10
    elif field == 'population': initial['population']['g0']['status'] = 'sheltering'
    elif field == 'resources': initial['resources']['crew']['position'] = [1, 0]
    outcome, changed = extract(evidence)
    assert outcome == original
    assert changed['initial_audit_sha256'] != metadata['initial_audit_sha256']


def deprecated_routes(evidence):
    limitation = dict(status='unsupported', observed=0,
        scope='Per-minute audit does not prove actual paths/map/clearance')
    evidence[0]['operation_coverage']['actual_routes_and_clearance'] = limitation
    return limitation


def test_deprecated_route_label_is_retained_only_with_replacement_observer(evidence):
    original = deprecated_routes(evidence)
    _, metadata = extract(evidence)
    retained = next(item for item in metadata['coverage_limitations'] if item['name'] == 'actual_routes_and_clearance')
    assert retained['source'] == original
    assert 'actual_routes_and_clearance' in metadata['limitations']
    assert evidence[0]['operation_coverage']['actual_routes_and_clearance'] == original
    assert evidence[0]['operation_coverage']['movement_paths']['status'] == 'complete'
    retained['source']['scope'] = 'changed'
    assert original['scope'] == 'Per-minute audit does not prove actual paths/map/clearance'
    assert 'physical_access_and_real_clearance' in metadata['limitations']
    assert 'safe_success' not in metadata


@pytest.mark.parametrize('fault', ['missing_observer', 'missing_paths', 'missing_ticks', 'missing_capacity',
    'unverifiable', 'observer_finding', 'coverage_mismatch', 'unsupported_paths'])
def test_deprecated_route_label_never_substitutes_for_live_observer_proof(evidence, fault):
    deprecated_routes(evidence)
    result, saved, _ = evidence
    if fault == 'missing_observer': saved.pop('movement_observer')
    elif fault == 'missing_paths': result['operation_coverage'].pop('movement_paths')
    elif fault == 'missing_ticks': result['movement_evidence']['ticks_observed'] = 1439
    elif fault == 'missing_capacity': result['movement_evidence']['capacity_ticks_checked'] = 1439
    elif fault == 'unverifiable': result['movement_evidence']['road_geometry_unverifiable'] = 1
    elif fault == 'observer_finding': saved['movement_observer']['findings'] = {'off_timed_path': {'count': 1}}
    elif fault == 'coverage_mismatch': saved['movement_observer']['coverage']['timed_positions_checked'] = 4
    elif fault == 'unsupported_paths': result['operation_coverage']['movement_paths']['status'] = 'unsupported'
    with pytest.raises(ValueError): extract(evidence)


def test_passive_unexercised_paths_still_require_full_observer_tick_coverage(evidence):
    deprecated_routes(evidence)
    result, saved, _ = evidence
    result['operation_coverage']['movement_paths'] = dict(status='not_exercised', observed=0)
    result['movement_evidence']['timed_positions_checked'] = 0
    saved['movement_observer']['coverage']['timed_positions_checked'] = 0
    assert extract(evidence)[1]['source_execution_verified'] is True


@pytest.mark.parametrize('field', ['modeled_aircraft_departures_checked', 'modeled_aircraft_drops_checked'])
def test_deprecated_label_requires_actual_modeled_aircraft_event_checks(evidence, field):
    deprecated_routes(evidence)
    result, saved, _ = evidence
    saved['trajectory']['initial_observation']['resources'].append(
        dict(id='plane', kind='aircraft', base_position=[0, 0], duty_end_at=1440))
    for row in saved['audit']:
        row['resources']['plane'] = dict(state='mobilizing' if row['minute'] == 0 else 'travel',
                                        position=[0, 0], duty_end_at=1440)
    saved['audit'][2]['applied_work'] = [{'resource_id': 'plane'}]
    result['movement_evidence'].update(modeled_aircraft_departures_checked=1, modeled_aircraft_drops_checked=1)
    saved['movement_observer']['coverage'] = copy.deepcopy(result['movement_evidence'])
    assert extract(evidence)[1]['terminal_counts_verified'] is True
    result['movement_evidence'][field] = 0
    saved['movement_observer']['coverage'][field] = 0
    with pytest.raises(ValueError, match='aircraft clearance/drop coverage'):
        extract(evidence)


@pytest.fixture
def policy_abandonment(evidence):
    result, saved, expected = evidence
    outcome = dict(result['outcome'], invalid_abandonment=True, graded_score=0., completed_minutes=5)
    result.update(status='blocked', minute=5, outcome=copy.deepcopy(outcome))
    saved['outcome'] = copy.deepcopy(outcome)
    saved['verification']['outcomes'] = copy.deepcopy(outcome)
    saved['audit'] = saved['audit'][:6]
    saved['trajectory']['transitions'] = [
        dict(start_minute=0, minute=2, valid=True, terminated=False, truncated=False, reward=0.),
        dict(start_minute=2, minute=3, valid=False, terminated=False, truncated=False, reward=0.),
        dict(start_minute=3, minute=4, valid=False, terminated=False, truncated=False, reward=0.),
        dict(start_minute=4, minute=5, valid=False, terminated=False, truncated=True, reward=0.)]
    return result, saved, expected


def test_replayed_three_invalid_actions_return_only_prefix_failure_evidence(policy_abandonment):
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    before = copy.deepcopy(policy_abandonment)
    failure = extract_verified_policy_failure(*policy_abandonment)
    assert failure['verified_policy_failure'] is True
    assert failure['failure_kind'] == 'invalid_action_abandonment'
    assert failure['observed_through_minute'] == 5
    assert failure['invalid_action_minutes'] == [3, 4, 5]
    assert failure['raw_reward'] == 0.
    assert failure['truncated'] is True and failure['terminated'] is False
    assert not any(key in failure for key in ('eligible_population', 'new_exposed', 'new_burned_cells', 'cost', 'audit'))
    assert policy_abandonment == before
    with pytest.raises(ValueError): extract_verified_outcome(*policy_abandonment)


def test_unflagged_infrastructure_failure_is_not_policy_failure():
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    assert extract_verified_policy_failure({}, {}, {}) is None
    for reason in ('missing', 'crash', 'provider_error', 'wall_budget'):
        assert extract_verified_policy_failure({'status': 'failed', 'reason': reason}, {}, {}) is None


def test_normal_full_day_does_not_create_policy_failure(evidence):
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    assert extract_verified_policy_failure(*evidence) is None


@pytest.mark.parametrize('fault', ['report_verify', 'saved_verify', 'reward_verify', 'report_reward',
    'saved_reward', 'independent_reward', 'censor', 'flag_disagrees', 'core', 'observation', 'seed',
    'only_two', 'valid_interruption', 'two_minute_invalid', 'truncated_early', 'not_truncated',
    'terminated', 'audit_gap', 'audit_short', 'clock', 'initial_hash', 'malformed_flag', 'tiny_nonzero_reward'])
def test_claimed_abandonment_requires_exact_narrow_evidence(policy_abandonment, fault):
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    result, saved, expected = policy_abandonment
    transitions = saved['trajectory']['transitions']
    if fault == 'report_verify': result['verification']['passed'] = False
    elif fault == 'saved_verify': saved['verification']['exact_replay'] = False
    elif fault == 'reward_verify': saved['verification']['independent_reward_agreement'] = False
    elif fault == 'report_reward': result['outcome']['graded_score'] = .1
    elif fault == 'saved_reward': saved['outcome']['graded_score'] = .1
    elif fault == 'independent_reward': saved['verification']['outcomes']['graded_score'] = .1
    elif fault == 'censor': saved['outcome']['boundary_censored'] = True
    elif fault == 'flag_disagrees': result['outcome']['invalid_abandonment'] = False
    elif fault == 'core': result['pins']['implementation_sha256'] = 'f' * 64
    elif fault == 'observation': saved['trajectory']['implementation_sha256'] = expected['implementation_sha256']
    elif fault == 'seed': saved['episode_seed'] += 1
    elif fault == 'only_two': transitions.pop(0); transitions.pop(0); transitions[0]['start_minute'] = 0
    elif fault == 'valid_interruption': transitions[-2]['valid'] = True
    elif fault == 'two_minute_invalid': transitions[-2]['minute'] = 5
    elif fault == 'truncated_early': transitions[-2]['truncated'] = True
    elif fault == 'not_truncated': transitions[-1]['truncated'] = False
    elif fault == 'terminated': transitions[-1]['terminated'] = True
    elif fault == 'audit_gap': saved['audit'][3]['minute'] = 4
    elif fault == 'audit_short': saved['audit'].pop()
    elif fault == 'clock': result['minute'] = 6
    elif fault == 'initial_hash': expected['initial_audit_sha256'] = '0' * 64
    elif fault == 'malformed_flag': result['outcome']['invalid_abandonment'] = 1
    elif fault == 'tiny_nonzero_reward': saved['verification']['outcomes']['graded_score'] = 1e-13
    with pytest.raises(ValueError): extract_verified_policy_failure(*policy_abandonment)



def test_three_invalid_actions_from_reset_are_a_verified_prefix(policy_abandonment):
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    result, saved, _ = policy_abandonment
    result['minute'] = 3
    for outcome in (result['outcome'], saved['outcome'], saved['verification']['outcomes']):
        outcome['completed_minutes'] = 3
    saved['audit'] = saved['audit'][:4]
    saved['trajectory']['transitions'] = [dict(start_minute=i, minute=i + 1, valid=False,
        terminated=False, truncated=i == 2, reward=0.) for i in range(3)]
    failure = extract_verified_policy_failure(*policy_abandonment)
    assert failure['observed_through_minute'] == 3
    assert failure['invalid_action_minutes'] == [1, 2, 3]



@pytest.mark.parametrize('fault', ['duplicate_delta', 'repeated_delta', 'inherited_delta', 'nonburnable_delta',
    'initial_delta', 'duplicate_inherited', 'duplicate_burnable', 'mutated_footprint',
    'unrecorded_footprint_growth', 'mutated_burnable', 'changed_cell_area'])
def test_impossible_burn_evidence_is_rejected_instead_of_collapsed(evidence, fault):
    audit = evidence[1]['audit']
    if fault == 'duplicate_delta': audit[10]['new_footprint'] = [2, 2]
    elif fault == 'repeated_delta': audit[11]['new_footprint'] = [2]
    elif fault == 'inherited_delta': audit[10]['new_footprint'] = [0, 2]
    elif fault == 'nonburnable_delta': audit[10]['new_footprint'] = [2, 99]
    elif fault == 'initial_delta': audit[0]['new_footprint'] = [2]
    elif fault == 'duplicate_inherited': audit[0]['footprint'] = [0, 1, 1]
    elif fault == 'duplicate_burnable': audit[0]['burnable'].append(9)
    elif fault == 'mutated_footprint': audit[11]['footprint'] = [0, 2]
    elif fault == 'unrecorded_footprint_growth': audit[11]['footprint'] = [0, 1, 2, 4]
    elif fault == 'mutated_burnable': audit[11]['burnable'] = list(range(9)) + [10]
    elif fault == 'changed_cell_area': audit[11]['cell_area_hectares'] = 50.
    with pytest.raises(ValueError): extract(evidence)


def test_initial_burnable_overlap_and_consistent_cumulative_snapshots_are_valid(evidence):
    audit = evidence[1]['audit']
    assert set(audit[0]['footprint']) <= set(audit[0]['burnable'])
    audit[11]['footprint'] = [0, 1, 2]
    audit[20]['footprint'] = [0, 1, 2, 3]
    audit[11]['burnable'] = list(audit[0]['burnable'])
    outcome, _ = extract(evidence)
    assert outcome == Outcome(28, 3, 8, 2, 25.)


def test_passive_and_legacy_synthetic_transitions_have_no_operational_orders(evidence):
    transition = evidence[1]['trajectory']['transitions'][0]
    assert extract(evidence)[1]['operational_order_count'] == 0
    transition.update(valid=True, action={'advance_minutes': 60})
    assert extract(evidence)[1]['operational_order_count'] == 0
    transition['action'] = {'status_requests': [{'center_id': 'center-0', 'subject': 'situation'}]}
    assert extract(evidence)[1]['operational_order_count'] == 0
    transition.pop('action')
    assert extract(evidence)[1]['operational_order_count'] == 0


def test_operational_order_count_uses_only_valid_requested_order_fields(evidence):
    transition = evidence[1]['trajectory']['transitions'][0]
    transition.update(valid=True, action={
        'requests': [{'resource_id': 'crew-0'}, {'resource_id': 'engine-0'}],
        'authorizations': ['offer-0'], 'releases': ['crew-1'],
        'protection_orders': [{'group_id': 'g0', 'action': 'shelter'}],
        'status_requests': [{'center_id': 'center-0', 'subject': 'situation'}]})
    assert extract(evidence)[1]['operational_order_count'] == 5
    transition['valid'] = False
    assert extract(evidence)[1]['operational_order_count'] == 0


def test_valid_order_field_is_not_coerced_to_invent_activity(evidence):
    transition = evidence[1]['trajectory']['transitions'][0]
    transition.update(valid=True, action={'authorizations': 'offer-0'})
    with pytest.raises(ValueError): extract(evidence)


def register_policy(evidence):
    result, saved, identity = evidence
    receipt = dict(name='conservative', factory='eval.incident_day_policies:make_policy',
        configuration={'name': 'conservative'}, source_sha256='f' * 64,
        dependency_sha256={'baselines.py': '1' * 64},
        **{key: identity[key] for key in ('implementation_sha256', 'observation_sha256', 'interface_version')})
    result['policy'] = 'conservative'
    result['policy_registration'] = copy.deepcopy(receipt)
    saved['policy_registration'] = copy.deepcopy(receipt)
    result['pins']['policy_registration_sha256'] = hashlib.sha256(json.dumps(receipt,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return receipt


def test_policy_receipt_is_optional_for_existing_passive_reference(evidence):
    _, metadata = extract(evidence)
    assert 'policy_registration' not in metadata and 'policy_registration_sha256' not in metadata


def test_policy_receipt_is_canonically_bound_and_detached(evidence):
    receipt = register_policy(evidence)
    evidence[1]['policy_registration'] = dict(reversed(list(receipt.items())))
    _, metadata = extract(evidence)
    assert metadata['policy_registration'] == receipt
    assert metadata['policy_registration_sha256'] == evidence[0]['pins']['policy_registration_sha256']
    metadata['policy_registration']['configuration']['name'] = 'changed'
    assert evidence[0]['policy_registration']['configuration']['name'] == 'conservative'


@pytest.mark.parametrize('fault', ['receipt_mismatch', 'pin_mismatch', 'missing_pin', 'label_mismatch',
    'core_identity', 'observation_identity', 'interface_identity', 'missing_saved', 'missing_result',
    'malformed_receipt', 'missing_source', 'malformed_dependencies', 'malformed_configuration', 'nonfinite_configuration'])
def test_registered_policy_cannot_be_mislabeled_or_rebound(evidence, fault):
    register_policy(evidence)
    result, saved, _ = evidence
    if fault == 'receipt_mismatch': saved['policy_registration']['configuration']['name'] = 'passive'
    elif fault == 'pin_mismatch': result['pins']['policy_registration_sha256'] = '0' * 64
    elif fault == 'missing_pin': result['pins'].pop('policy_registration_sha256')
    elif fault == 'label_mismatch': result['policy'] = 'passive'
    elif fault == 'core_identity': result['policy_registration']['implementation_sha256'] = '2' * 64
    elif fault == 'observation_identity': result['policy_registration']['observation_sha256'] = '2' * 64
    elif fault == 'interface_identity': saved['policy_registration']['interface_version'] = 'v2'
    elif fault == 'missing_saved': saved.pop('policy_registration')
    elif fault == 'missing_result': result.pop('policy_registration')
    elif fault == 'malformed_receipt': result['policy_registration'] = None
    elif fault == 'missing_source': result['policy_registration'].pop('source_sha256')
    elif fault == 'malformed_dependencies': result['policy_registration']['dependency_sha256'] = {'baselines.py': None}
    elif fault == 'malformed_configuration': result['policy_registration']['configuration'] = []
    elif fault == 'nonfinite_configuration': result['policy_registration']['configuration']['weight'] = float('nan')
    with pytest.raises(ValueError): extract(evidence)


def test_registration_pin_without_receipts_is_not_evidence(evidence):
    evidence[0]['pins']['policy_registration_sha256'] = '0' * 64
    with pytest.raises(ValueError): extract(evidence)


def test_verified_policy_failure_also_preserves_supplied_receipt(policy_abandonment):
    from wildfire_benchmark_v2.evidence import extract_verified_policy_failure
    receipt = register_policy(policy_abandonment)
    failure = extract_verified_policy_failure(*policy_abandonment)
    assert failure['policy_registration'] == receipt
    assert failure['policy_registration_sha256'] == policy_abandonment[0]['pins']['policy_registration_sha256']
