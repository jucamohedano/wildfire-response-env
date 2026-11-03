"""Independent terminal accounting for trusted evaluator evidence, never a model tool.

The caller binds the current report pins into the cell's result['pins'] and
verifies its source-file hashes. expected_identity requires task_id, seed,
scenario_sha256, inputs_sha256, network_sha256, implementation_sha256 (CORE),
observation_sha256 and interface_version. A v3 trajectory's implementation hash
is the OBSERVATION hash, not the core hash. No runtime imports or simulations.
"""
import copy
import hashlib
import json
import math
import re
from collections import Counter

from . import _capture_source
from .grading import Outcome

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)

IDENTITY_KEYS = ('task_id', 'seed', 'scenario_sha256', 'inputs_sha256', 'network_sha256',
                 'implementation_sha256', 'observation_sha256', 'interface_version')
REWARD_VERSION = 'wildfire-incident-day-outcomes-v2'
OPERATIONAL_ADVERSE = frozenset(('recovery_after_shift_end_egress_unavailable',
                               'terminal_assistance_after_shift'))
LIMITATIONS = frozenset(('synthetic_physics', 'land_mask_missing', 'initial_fire_land_unknown',
                        'initial_footprint_land_unknown', 'fire_land_unknown', 'fire_front_screen'))
REQUIRED_COVERAGE = frozenset(('get_situation', 'get_reports', 'route_options', 'evacuation_options',
    'check_action', 'commit_orders', 'request_status', 'advance_time', 'requests', 'authorizations',
    'releases', 'shelter', 'evacuate', 'status_requests', 'waits', 'movement_paths',
    'capacity_each_tick', 'facility_conservation'))
UNVERIFIABLE_MOVEMENT = ('journey_unverifiable', 'position_unverifiable',
                         'multi_minute_ticks_unobserved', 'road_geometry_unverifiable')
HASH = re.compile(r'[0-9a-f]{64}\Z')
DEPRECATED_ROUTE_COVERAGE = 'actual_routes_and_clearance'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _int(value, label):
    _require(type(value) is int and value >= 0, label + ' must be a nonnegative integer')
    return value


def _number(value, label, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value)
             and (value > 0 if positive else value >= 0), label + ' must be finite and nonnegative')
    return value


def _cells(value, label):
    _require(isinstance(value, list), label + ' must be a cell list')
    cells = {_int(cell, label) for cell in value}
    _require(len(cells) == len(value), label + ' contains duplicate cell IDs')
    return cells


def _point(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(x) in (int, float) and math.isfinite(x) for x in value))


def _close(actual, expected, label):
    _require(type(actual) in (int, float) and math.isfinite(actual)
             and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12), label + ' differs from audited counts')


def _identity(result, saved, expected):
    _require(isinstance(expected, dict) and all(k in expected for k in IDENTITY_KEYS), 'Expected identity is incomplete')
    _require(isinstance(expected['task_id'], str) and bool(expected['task_id'])
             and isinstance(expected['interface_version'], str) and bool(expected['interface_version']), 'Invalid expected task/interface')
    _int(expected['seed'], 'Expected seed')
    for key in IDENTITY_KEYS[2:-1]:
        _require(type(expected[key]) is str and HASH.fullmatch(expected[key]) is not None, 'Invalid expected hash: ' + key)
    trace, pins = saved['trajectory'], result['pins']
    _require(result['case_id'] == trace['task_id'] == expected['task_id'], 'Actual task ID differs')
    if 'task_id' in result:
        _require(result['task_id'] == expected['task_id'], 'Result task ID differs')
    _require(type(trace['seed']) is int and type(saved['episode_seed']) is int
             and trace['seed'] == saved['episode_seed'] == expected['seed'], 'Episode seed differs')
    for key in ('scenario_sha256', 'inputs_sha256', 'network_sha256'):
        _require(trace[key] == pins[key] == expected[key], key + ' differs')
    _require(pins['implementation_sha256'] == expected['implementation_sha256'], 'Core identity differs')
    _require(pins['observation_sha256'] == trace['implementation_sha256'] == expected['observation_sha256'],
             'Observation identity differs (trajectory implementation must be observation hash)')
    _require(trace['interface_version'] == expected['interface_version'], 'Trajectory interface differs')
    initial = trace['initial_observation']
    _require(initial['minute'] == 0 and type(initial['minute']) is int
             and initial['interface_version'] == expected['interface_version'], 'Initial observation identity differs')
    _require(initial['task_id'] in (expected['task_id'], 'incident-day-evaluation-v1'), 'Initial public task label differs')
    return trace


def _verification(result, saved):
    for source in (result['verification'], saved['verification']):
        _require(all(source.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement')),
                 'Execution verification did not pass')
    _require(result['query_independence'].get('passed') is True, 'Query independence did not pass')
    if 'query_independence' in saved:
        _require(saved['query_independence'] == result['query_independence'], 'Saved query verification differs')
    checks = result['resume_checks']
    _require(isinstance(checks, list) and checks and all(c.get('passed') is True for c in checks),
             'Checkpoint continuation did not pass')
    _require(saved['resume_checks'] == checks, 'Saved continuation checks differ')
    coverage = result['operation_coverage']
    _require(isinstance(coverage, dict) and REQUIRED_COVERAGE <= set(coverage), 'Required operation coverage missing')
    for name, entry in coverage.items():
        _require(isinstance(entry, dict), 'Malformed coverage entry')
        status = entry.get('status')
        if name in REQUIRED_COVERAGE:
            _require(status in ('complete', 'not_exercised'), 'Required coverage unsupported: ' + name)
            observed = _int(entry.get('observed'), name + ' observed')
            _require(status != 'not_exercised' or observed == 0, 'Unexercised coverage has observations')
        else:
            _require(status in ('complete', 'not_exercised')
                     or (name in ('physical_access_and_real_clearance', DEPRECATED_ROUTE_COVERAGE) and status == 'unsupported'),
                     'Unknown unsupported/failed coverage: ' + name)
    for name in ('capacity_each_tick', 'facility_conservation'):
        _require(coverage[name]['status'] == 'complete', name + ' must be fully checked')
    _require(coverage['capacity_each_tick']['observed'] == 1440, 'Capacity tick coverage incomplete')
    movement = result['movement_evidence']
    _require(type(movement.get('ticks_observed')) is int and movement['ticks_observed'] == 1440
             and type(movement.get('capacity_ticks_checked')) is int and movement['capacity_ticks_checked'] == 1440,
             'Movement/capacity observations must cover all 1440 ticks')
    for name in UNVERIFIABLE_MOVEMENT:
        _require(_int(movement.get(name, 0), name) == 0, 'Unverifiable movement evidence')
    timed = _int(movement.get('timed_positions_checked', 0), 'Timed positions checked')
    _require(coverage['movement_paths']['observed'] == timed, 'Movement path coverage count differs')
    observer = saved['movement_observer']
    _require(isinstance(observer.get('findings'), dict) and not observer['findings'], 'Observer invariant findings remain')
    _require(observer['coverage'] == movement, 'Saved observer coverage differs')
    deprecated = coverage.get(DEPRECATED_ROUTE_COVERAGE, {})
    if deprecated.get('status') == 'unsupported':
        # This legacy minute-audit limitation is not replacement path evidence.
        # The checks above remain mandatory, including saved live-observer
        # agreement, all ticks, capacity and zero unverifiable movement.
        _require(coverage['movement_paths']['status'] == ('complete' if timed else 'not_exercised'),
                 'Deprecated route limitation requires consistent replacement path coverage')
        units = saved['trajectory']['initial_observation']['resources']
        _require(isinstance(units, list) and len({u['id'] for u in units}) == len(units),
                 'Resource descriptors needed for modeled aircraft coverage')
        kinds = {u['id']: u['kind'] for u in units}
        _require(all(kind in ('aircraft', 'engine', 'crew') for kind in kinds.values()),
                 'Unknown resource kind in observer evidence')
        audit = saved['audit']
        _require(isinstance(audit, list) and len(audit) == 1441, 'Replacement observer needs complete audit')
        _require(all(set(row['resources']) == set(kinds) for row in audit),
                 'Resource roster differs from replacement observer evidence')
        departures = sum(before['resources'][identifier]['state'] == 'mobilizing'
                         and after['resources'][identifier]['state'] == 'travel'
                         for before, after in zip(audit, audit[1:])
                         for identifier, kind in kinds.items() if kind == 'aircraft')
        drops = 0
        for row in audit:
            for work in row['applied_work']:
                _require(work['resource_id'] in kinds, 'Unknown work resource in observer evidence')
                drops += kinds[work['resource_id']] == 'aircraft'
        _require(_int(movement.get('modeled_aircraft_departures_checked', 0), 'Modeled aircraft departures') == departures
                 and _int(movement.get('modeled_aircraft_drops_checked', 0), 'Modeled aircraft drops') == drops,
                 'Modeled aircraft clearance/drop coverage missing or inconsistent')


def _findings(result, saved):
    def tally(items):
        _require(isinstance(items, list), 'Findings are missing')
        counts = Counter()
        for item in items:
            _require(item.get('category') == 'unsupported', 'Contradiction/error or unknown finding category')
            code = item.get('code')
            _require(code in OPERATIONAL_ADVERSE | LIMITATIONS, 'Unknown unsupported finding: ' + str(code))
            count = item.get('count', 1)
            _require(type(count) is int and count > 0, 'Invalid finding count')
            counts[code] += count
        return counts
    _require(tally(result['findings']) == tally(saved['findings']), 'Report/saved findings differ')
    adverse = [item for item in saved['findings'] if item['code'] in OPERATIONAL_ADVERSE]
    _require(result['status'] in ('complete', 'blocked'), 'Only completed or known-adverse blocked executions are eligible')
    _require((result['status'] == 'blocked') == bool(adverse), 'Execution status/adverse evidence differs')
    return adverse


def _counts(audit):
    _require(isinstance(audit, list) and len(audit) == 1441, 'Audit must cover minutes 0..1440')
    first = audit[0]
    population = first['population']
    _require(isinstance(population, dict) and population and all(isinstance(k, str) and k for k in population),
             'Initial cohort roster missing')
    cohorts = {key: _int(group['count'], 'Cohort count') for key, group in population.items()}
    initial_exposed = {key: _int(group['exposed'], 'Inherited exposure') for key, group in population.items()}
    previous = dict(initial_exposed)
    inherited = _cells(first['footprint'], 'Inherited footprint')
    burnable = _cells(first['burnable'], 'Burnable cells')
    area = float(_number(first['cell_area_hectares'], 'Cell area', positive=True))
    # Initial burnable cells may overlap the inherited footprint; those cells
    # are excluded from eligible new harm. Every later delta must be genuinely
    # new, not an overlapping or duplicated claim silently collapsed by a set.
    eligible = burnable - inherited
    seen = set(inherited)
    footprint = set()
    for minute, record in enumerate(audit):
        _require(type(record.get('minute')) is int and record['minute'] == minute, 'Audit clock is not contiguous')
        groups = record['population']
        _require(isinstance(groups, dict) and set(groups) == set(cohorts), 'Cohort roster changed')
        for key, group in groups.items():
            _require(type(group.get('count')) is int and group['count'] == cohorts[key], 'Cohort count changed')
            exposed = _int(group['exposed'], 'Cohort exposure')
            _require(previous[key] <= exposed <= cohorts[key], 'Exposure reversed or exceeds cohort')
            previous[key] = exposed
        delta = _cells(record['new_footprint'], 'New footprint')
        _require(minute != 0 or not delta, 'Initial audit cannot contain a new-footprint delta')
        _require(delta <= eligible, 'New footprint contains inherited or nonburnable cells')
        _require(delta.isdisjoint(seen), 'New footprint repeats an already burned cell')
        footprint.update(delta)
        seen.update(delta)
        if 'footprint' in record:
            _require(_cells(record['footprint'], 'Footprint snapshot') == seen,
                     'Footprint snapshot changed existing cells or disagrees with deltas')
        if 'burnable' in record:
            _require(_cells(record['burnable'], 'Burnable snapshot') == burnable, 'Burnable cell universe changed')
        if 'cell_area_hectares' in record:
            _require(_number(record['cell_area_hectares'], 'Cell area', positive=True) == area, 'Cell area changed')
    return (dict(eligible_population=sum(cohorts.values()) - sum(initial_exposed.values()),
                 new_exposed=sum(previous.values()) - sum(initial_exposed.values()),
                 eligible_burnable_cells=len(eligible), new_burned_cells=len(footprint & eligible),
                 cell_area_hectares=area), len(inherited), sum(initial_exposed.values()))


def _operational_proof(adverse, saved):
    if not adverse:
        return []
    audit = saved['audit']
    units = saved['trajectory']['initial_observation']['resources']
    _require(isinstance(units, list) and len({u['id'] for u in units}) == len(units), 'Initial resource descriptors missing/duplicated')
    specs = {u['id']: u for u in units}
    proofs, seen = [], set()
    for finding in adverse:
        rid, minute = finding.get('resource'), finding.get('minute')
        _require(rid in specs and type(minute) is int and 0 < minute <= 1440, 'Adverse resource/time evidence missing')
        key = (finding['code'], rid, minute)
        _require(key not in seen and finding.get('count', 1) == 1, 'Duplicate or aggregated adverse evidence')
        seen.add(key)
        unit = specs[rid]
        _require(unit.get('kind') in ('engine', 'crew') and _point(unit.get('base_position')), 'Adverse resource descriptor invalid')
        deadline = _int(unit.get('duty_end_at'), 'Resource duty end')
        terminal = finding['code'] == 'terminal_assistance_after_shift'
        _require((deadline <= minute if terminal else deadline < minute)
                 and (not terminal or minute == 1440), 'Adverse event is not at/after duty/horizon')
        end_wait = 1441 if terminal else minute
        matches = []
        for report in saved['reports']:
            observed, payload = report.get('observed_at'), report.get('payload', {})
            if (report.get('kind') != 'egress_unavailable' or payload.get('resource_id') != rid
                    or type(observed) is not int or not 0 <= observed <= deadline
                    or not _point(payload.get('position'))):
                continue
            waiting = audit[observed:end_wait]
            stationary = bool(waiting) and waiting[0]['resources'][rid]['state'] == 'needs_assistance'
            for record in waiting:
                state = record['resources'][rid]
                preflight = (not terminal and state['state'] == 'blocked' and state.get('blocked_state') == 'returning'
                    and type(state.get('return_recovery_preflight_at')) is int
                    and state['return_recovery_preflight_at'] == record['minute'])
                stationary = (stationary and _point(state['position'])
                    and math.dist(state['position'], payload['position']) <= 1e-6
                    and (state['state'] == 'needs_assistance' or preflight))
            if stationary:
                matches.append(observed)
        _require(matches, 'Stationary unavailable-egress evidence missing')
        for record in audit[min(matches):]:
            state = record['resources'][rid]
            _require(state['duty_end_at'] == deadline, 'Resource duty identity changed')
            _require(not any(w.get('resource_id') == rid for w in record['applied_work']), 'Work occurred during unresolved recovery')
            _require(state['state'] in ('needs_assistance', 'blocked', 'returning', 'off_duty'), 'Resource was reassigned during recovery')
        if terminal:
            label = 'unresolved_at_horizon'
        else:
            final = audit[-1]['resources'][rid]
            _require(final['state'] == 'off_duty' and _point(final['position'])
                     and math.dist(final['position'], unit['base_position']) <= .01, 'Late recovery did not reach base')
            label = 'late_recovery'
        proofs.append(dict(code=finding['code'], resource=rid, minute=minute, operational_status=label))
    return proofs



def _operational_order_count(transitions):
    """Count valid submitted operational orders, without asserting their effects."""
    total = 0
    for transition in transitions:
        if transition.get('valid') is not True:
            continue
        action = transition.get('action', {})
        _require(isinstance(action, dict), 'Valid transition action must be an object')
        for kind in ('requests', 'authorizations', 'releases', 'protection_orders'):
            orders = action.get(kind, [])
            _require(isinstance(orders, list), 'Valid operational order field must be a list')
            total += len(orders)
    return total



def _policy_registration(result, saved, expected):
    """Bind an optional evaluator policy receipt; no code/config is inferred."""
    in_result, in_saved = 'policy_registration' in result, 'policy_registration' in saved
    pin_present = 'policy_registration_sha256' in result['pins']
    if not in_result and not in_saved:
        _require(not pin_present, 'Policy registration pin has no receipt')
        return {}
    _require(in_result and in_saved, 'Report and saved policy receipts are both required')
    required = {'name', 'factory', 'configuration', 'source_sha256', 'dependency_sha256',
                'implementation_sha256', 'observation_sha256', 'interface_version'}
    encoded = []
    for receipt in (result['policy_registration'], saved['policy_registration']):
        _require(isinstance(receipt, dict) and required <= set(receipt)
                 and all(type(key) is str for key in receipt), 'Malformed policy registration receipt')
        _require(type(receipt['name']) is str and bool(receipt['name'].strip())
                 and type(receipt['factory']) is str and bool(receipt['factory'].strip())
                 and isinstance(receipt['configuration'], dict), 'Policy name/factory/configuration missing')
        dependencies = receipt['dependency_sha256']
        _require(isinstance(dependencies, dict) and dependencies
                 and all(type(name) is str and bool(name) and type(value) is str
                         and HASH.fullmatch(value) is not None for name, value in dependencies.items()),
                 'Policy dependency identities missing or malformed')
        _require(type(receipt['source_sha256']) is str and HASH.fullmatch(receipt['source_sha256']) is not None,
                 'Policy source identity missing or malformed')
        for field in ('implementation_sha256', 'observation_sha256', 'interface_version'):
            _require(receipt[field] == expected[field], 'Policy registration identity differs: ' + field)
        encoded.append(json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8'))
    _require(encoded[0] == encoded[1], 'Report/saved policy registration receipts differ')
    receipt = result['policy_registration']
    _require(result.get('policy') == receipt['name'], 'Policy label differs from registered receipt')
    receipt_sha256 = hashlib.sha256(encoded[0]).hexdigest()
    _require(result['pins'].get('policy_registration_sha256') == receipt_sha256, 'Policy registration pin differs')
    return dict(policy_registration=copy.deepcopy(receipt), policy_registration_sha256=receipt_sha256)


def _extract(result, saved, expected):
    _require(isinstance(result, dict) and isinstance(saved, dict), 'Result and saved evidence must be dictionaries')
    _require(result.get('split') == 'evaluation' and result.get('training_eligible') is False,
             'Evidence must be evaluation-only')
    trace = _identity(result, saved, expected)
    _verification(result, saved)
    adverse = _findings(result, saved)
    _require(type(result.get('minute')) is int and result['minute'] == 1440, 'Report is not terminal')
    transitions = trace['transitions']
    _require(isinstance(transitions, list) and transitions, 'Terminal transition missing')
    previous = 0
    for index, transition in enumerate(transitions):
        minute = transition['minute']
        _require(type(transition['start_minute']) is int and transition['start_minute'] == previous
                 and type(minute) is int and previous < minute <= 1440, 'Transition clock differs')
        terminal = index == len(transitions) - 1
        _require(transition.get('terminated') is terminal and transition.get('truncated') is False,
                 'Transition is unfinished, censored or truncated')
        if not terminal:
            _close(transition['reward'], 0., 'Nonterminal reward')
        previous = minute
    _require(previous == 1440, 'Terminal transition does not reach horizon')
    values, inherited_burned, inherited_exposed = _counts(saved['audit'])
    pop_cost = values['new_exposed'] / values['eligible_population'] if values['eligible_population'] else 0.
    burn_cost = values['new_burned_cells'] / values['eligible_burnable_cells'] if values['eligible_burnable_cells'] else 0.
    cost = .8 * pop_cost + .2 * burn_cost
    final_groups = saved['audit'][-1]['population']
    for group in final_groups.values():
        _require(isinstance(group.get('status'), str) and type(group.get('protection_pending')) is bool,
                 'Terminal population/protection evidence missing')
    unresolved = sum(g['status'] in ('evacuating', 'blocked', 'unable_to_comply') or g['protection_pending']
                     for g in final_groups.values())
    raw = dict(reward_version=REWARD_VERSION, graded_score=1. - cost,
        protected_population_fraction=1. - pop_cost,
        additional_burned_hectares=values['new_burned_cells'] * values['cell_area_hectares'],
        inherited_burned_hectares=inherited_burned * values['cell_area_hectares'],
        inherited_exposed_population=inherited_exposed, new_exposed_population=values['new_exposed'],
        unresolved_evacuations=unresolved, safe_success=not values['new_exposed'] and not unresolved,
        score_eligible=True, boundary_censored=False, invalid_abandonment=False, completed_minutes=1440)
    for name, reported in (('Report outcome', result['outcome']), ('Saved outcome', saved['outcome']),
                           ('Independent verification outcome', saved['verification']['outcomes'])):
        _require(isinstance(reported, dict) and set(raw) <= set(reported), name + ' fields missing')
        for key, wanted in raw.items():
            if type(wanted) is float:
                _close(reported[key], wanted, name + '.' + key)
            else:
                _require(type(reported[key]) is type(wanted) and reported[key] == wanted, name + '.' + key + ' differs')
    _close(transitions[-1]['reward'], raw['graded_score'], 'Terminal transition reward')
    operational = _operational_proof(adverse, saved)
    initial_audit_sha256 = hashlib.sha256(json.dumps(saved['audit'][0], sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    if 'initial_audit_sha256' in expected:
        _require(expected['initial_audit_sha256'] == initial_audit_sha256, 'Initial audit identity differs')
    coverage_limitations = [dict(name=name, source=copy.deepcopy(result['operation_coverage'][name]))
        for name in (DEPRECATED_ROUTE_COVERAGE, 'physical_access_and_real_clearance')
        if result['operation_coverage'].get(name, {}).get('status') == 'unsupported']
    metadata = dict(identity={k: copy.deepcopy(expected[k]) for k in IDENTITY_KEYS},
        initial_audit_sha256=initial_audit_sha256,
        terminal_counts_verified=True, source_execution_verified=True, cost=cost,
        operational_order_count=_operational_order_count(transitions),
        raw_reward=raw['graded_score'], inherited_exposed=inherited_exposed,
        inherited_burned_cells=inherited_burned,
        operational_status=('unresolved_at_horizon' if any(p['operational_status'] == 'unresolved_at_horizon' for p in operational)
                            else 'adverse' if operational else 'no_blocking_findings'),
        adverse_flags=sorted({p['code'] for p in operational}), operational_evidence=operational,
        coverage_limitations=coverage_limitations,
        limitations=sorted({f['code'] for f in saved['findings'] if f['code'] in LIMITATIONS}
                           | {entry['name'] for entry in coverage_limitations}))
    metadata.update(_policy_registration(result, saved, expected))
    return Outcome(**values), metadata


def extract_verified_outcome(result: dict, saved: dict, expected_identity: dict) -> tuple[Outcome, dict]:
    """Validate terminal evidence and return counts plus compact, detached metadata.

    Raises ValueError for missing, malformed, mismatched or ineligible evidence.
    Execution replay is a source-verification requirement, not reexecuted here.
    Metadata always includes the canonical SHA256 of the complete audit[0]. If
    expected_identity also supplies initial_audit_sha256, it must match exactly.
    Raw safe_success is checked only as the legacy population contract; neither
    operational safety nor an optimal/reference plan is asserted by this API.
    """
    try:
        return _extract(result, saved, expected_identity)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
        raise ValueError('Missing or malformed terminal evidence: ' + str(error)) from error


def _policy_failure(result, saved, expected):
    _require(isinstance(result, dict) and isinstance(saved, dict), 'Policy failure evidence must be dictionaries')
    trace = _identity(result, saved, expected)
    for source in (result['verification'], saved['verification']):
        _require(all(source.get(key) is True for key in ('passed', 'exact_replay', 'independent_reward_agreement')),
                 'Policy abandonment replay verification did not pass')
    minute = _int(result['minute'], 'Abandonment minute')
    _require(3 <= minute <= 1440, 'Invalid abandonment prefix length')
    outcomes = [result['outcome'], saved['outcome'], saved['verification']['outcomes']]
    if 'outcomes' in result['verification']:
        outcomes.append(result['verification']['outcomes'])
    for outcome in outcomes:
        _require(outcome.get('invalid_abandonment') is True and outcome.get('boundary_censored') is False,
                 'Abandonment flag missing/inconsistent or source censored')
        _require(outcome.get('score_eligible') is True
                 and type(outcome.get('completed_minutes')) is int and outcome['completed_minutes'] == minute,
                 'Abandonment outcome clock or raw reward eligibility differs')
        _require(outcome.get('reward_version') == REWARD_VERSION, 'Abandonment reward contract differs')
        _require(type(outcome.get('graded_score')) in (int, float) and outcome['graded_score'] == 0,
                 'Abandonment raw reward must be exactly zero')
    for source in (result, saved, trace):
        _require(source.get('censored') is not True and source.get('boundary_censored') is not True
                 and source.get('_input_censored') is not True, 'Censored source cannot prove policy abandonment')
    transitions = trace['transitions']
    _require(isinstance(transitions, list) and len(transitions) >= 3, 'Three invalid transitions are required')
    previous = 0
    for index, transition in enumerate(transitions):
        end = transition['minute']
        final = index == len(transitions) - 1
        _require(type(transition['start_minute']) is int and transition['start_minute'] == previous
                 and type(end) is int and previous < end <= minute, 'Abandonment transition clock differs')
        _require(type(transition.get('valid')) is bool
                 and transition.get('terminated') is False
                 and transition.get('truncated') is final, 'Abandonment terminal/validity flags differ')
        _require(type(transition.get('reward')) in (int, float) and transition['reward'] == 0,
                 'Abandonment transition reward must be exactly zero')
        previous = end
    _require(previous == minute, 'Abandonment transition does not reach reported minute')
    last_three = transitions[-3:]
    _require(len(transitions) == 3 or transitions[-4]['valid'] is True,
             'Abandonment should have stopped at the first three-invalid streak')
    _require(all(t['valid'] is False and t['minute'] - t['start_minute'] == 1 for t in last_three),
             'Abandonment needs three consecutive one-minute invalid actions')
    audit = saved['audit']
    _require(isinstance(audit, list) and len(audit) == minute + 1
             and all(type(row.get('minute')) is int and row['minute'] == index for index, row in enumerate(audit)),
             'Abandonment audit prefix is incomplete or noncontiguous')
    initial_hash = hashlib.sha256(json.dumps(audit[0], sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    if 'initial_audit_sha256' in expected:
        _require(expected['initial_audit_sha256'] == initial_hash, 'Initial audit identity differs')
    failure = dict(version='wildfire-verified-policy-failure-v1', failure_kind='invalid_action_abandonment',
        verified_policy_failure=True, source_execution_verified=True,
        identity={key: copy.deepcopy(expected[key]) for key in IDENTITY_KEYS},
        initial_audit_sha256=initial_hash, observed_through_minute=minute,
        invalid_action_minutes=[t['minute'] for t in last_three], raw_reward=0.,
        terminated=False, truncated=True)
    failure.update(_policy_registration(result, saved, expected))
    return failure


def extract_verified_policy_failure(result: dict, saved: dict, expected_identity: dict) -> dict | None:
    """Recognize only explicit, exactly replayed invalid-action abandonment.

    With no explicit source flag, return None (including missing/crashed runs).
    A claimed but malformed abandonment raises ValueError. This is prefix failure
    evidence only: it never creates a full-day Outcome or projects future harm.
    """
    flags = []
    for source in (result, saved):
        if not isinstance(source, dict):
            continue
        candidates = [source.get('outcome')]
        verification = source.get('verification')
        if isinstance(verification, dict):
            candidates.append(verification.get('outcomes'))
        for outcome in candidates:
            if isinstance(outcome, dict) and 'invalid_abandonment' in outcome:
                flag = outcome['invalid_abandonment']
                _require(type(flag) is bool, 'Malformed source abandonment flag')
                flags.append(flag)
    if not any(flags):
        return None
    try:
        return _policy_failure(result, saved, expected_identity)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
        raise ValueError('Missing or malformed claimed policy abandonment: ' + str(error)) from error
