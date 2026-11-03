"""Pure scorer regressions for CI; no environment, policy or simulation imports."""
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_benchmark_v2/src'))

from wildfire_benchmark_v2 import Outcome, aggregate, cost, grade, qualify, weighted_components
from wildfire_benchmark_v2 import grading


PASSIVE = Outcome(100, 20, 1000, 100, 0.25)
REFERENCE = Outcome(100, 0, 1000, 0, 0.25)
TASKS = tuple(f'evaluation-{index:02}' for index in range(24))


def full_rows(outcome):
    return dict.fromkeys(TASKS, outcome)


def test_constants_match_the_candidate_protocol():
    protocol = json.loads((ROOT / 'data/benchmark_v2/protocol.json').read_text())
    assert grading.PROTOCOL_VERSION == protocol['version']
    assert grading.OBJECTIVE_VERSION == protocol['objective']['version']
    assert grading.GRADE_VERSION == protocol['grade']['version']
    assert grading.POPULATION_WEIGHT == protocol['objective']['population_weight'] == .8
    assert grading.BURNED_AREA_WEIGHT == protocol['objective']['burned_area_weight'] == .2
    assert grading.REFERENCE_KIND == protocol['reference_kind'] == 'best_observed_scripted_online'
    assert grading.REFERENCE_PROVEN_OPTIMAL is protocol['reference_proven_optimal'] is False
    assert grading.EXPECTED_TASK_COUNT == protocol['expected_tasks'] == 24
    assert grading.MAXIMUM_WEAK_ACTIVE_GRADE == protocol['qualification']['maximum_weak_active_grade'] == .6


def test_cost_and_components_are_new_harm_only():
    components = weighted_components(PASSIVE)
    assert components == pytest.approx(dict(population_fraction=.2, burned_fraction=.1,
                                            population=.16, burned_area=.02))
    assert cost(PASSIVE) == pytest.approx(.18)
    assert cost(REFERENCE) == 0
    assert cost(asdict(PASSIVE)) == cost(PASSIVE)
    assert cost(Outcome(0, 0, 0, 0, 1)) == 0


def test_passive_reference_partial_and_worse_grades():
    null = grade(PASSIVE, PASSIVE, REFERENCE)
    assert null['qualified'] and null['grade'] == null['raw_gain'] == 0
    assert null['reference_exceeded'] is False
    best = grade(REFERENCE, PASSIVE, REFERENCE)
    assert best['grade'] == best['raw_gain'] == 1
    assert best['reference_exceeded'] is False
    partial = grade(replace(PASSIVE, new_exposed=10, new_burned_cells=50), PASSIVE, REFERENCE)
    assert partial['grade'] == partial['raw_gain'] == pytest.approx(.5)
    assert partial['cost_improvement'] == pytest.approx(.09)
    worse = grade(replace(PASSIVE, new_exposed=30, new_burned_cells=150), PASSIVE, REFERENCE)
    assert worse['grade'] == 0 and worse['raw_gain'] == pytest.approx(-.5)


def test_better_than_reference_keeps_unclipped_gain_and_honest_reference_label():
    reference = replace(PASSIVE, new_exposed=5, new_burned_cells=25)
    result = grade(REFERENCE, PASSIVE, reference)
    assert result['grade'] == 1
    assert result['raw_gain'] == pytest.approx(4 / 3)
    assert result['reference_exceeded'] is True
    assert result['reference_kind'] == 'best_observed_scripted_online'
    assert result['reference_proven_optimal'] is False


def test_adverse_flags_do_not_change_cost_or_gain_and_are_not_aliased():
    flags = ['unresolved_at_horizon']
    plain = grade(REFERENCE, PASSIVE, REFERENCE)
    adverse = grade(REFERENCE, PASSIVE, REFERENCE, adverse_flags=flags)
    assert {key: value for key, value in adverse.items() if key != 'adverse_flags'} == {
        key: value for key, value in plain.items() if key != 'adverse_flags'}
    flags.append('caller mutation')
    assert adverse['adverse_flags'] == ['unresolved_at_horizon']


def test_reference_cannot_trade_more_exposed_people_for_less_burned_land():
    reference = replace(PASSIVE, new_exposed=21, new_burned_cells=0)
    assert cost(reference) < cost(PASSIVE)
    assert qualify(PASSIVE, reference)['reason'] == 'reference_increases_exposure'
    result = grade(REFERENCE, PASSIVE, reference)
    assert result['qualified'] is False
    assert result['grade'] is result['raw_gain'] is result['reference_exceeded'] is None


@pytest.mark.parametrize('passive,reference', [
    (PASSIVE, replace(PASSIVE, new_exposed=19)),
    (PASSIVE, replace(PASSIVE, new_burned_cells=99)),
])
def test_one_person_or_one_cell_equal_exposure_is_sufficient(passive, reference):
    assert qualify(passive, reference)['qualified']
    assert grade(reference, passive, reference)['grade'] == 1


def test_reference_with_person_benefit_still_needs_positive_weighted_headroom():
    reference = replace(PASSIVE, new_exposed=19, new_burned_cells=1000)
    assert qualify(PASSIVE, reference)['reason'] == 'no_positive_cost_headroom'
    assert grade(reference, PASSIVE, reference)['grade'] is None


@pytest.mark.parametrize('unchanged', [REFERENCE, PASSIVE, Outcome(0, 0, 0, 0, 1)])
def test_zero_headroom_is_unqualified_not_an_invented_perfect_grade(unchanged):
    result = grade(unchanged, unchanged, unchanged)
    assert result['qualified'] is False
    assert result['qualification_reason'] == 'no_positive_cost_headroom'
    assert result['cost_gap'] == 0
    assert result['grade'] is result['raw_gain'] is None


@pytest.mark.parametrize('passive,reference', [
    (Outcome(0, 0, 100, 10, .25), Outcome(0, 0, 100, 0, .25)),
    (Outcome(100, 10, 0, 0, .25), Outcome(100, 0, 0, 0, .25)),
])
def test_zero_denominator_contributes_zero_without_free_credit(passive, reference):
    assert qualify(passive, reference)['qualified']
    assert grade(passive, passive, reference)['grade'] == 0
    assert grade(reference, passive, reference)['grade'] == 1


def test_monotonicity_and_bounds_over_population_land_grid():
    passive = Outcome(10, 10, 10, 10, .25)
    reference = Outcome(10, 0, 10, 0, .25)
    for exposed in range(11):
        for burned in range(11):
            agent = replace(passive, new_exposed=exposed, new_burned_cells=burned)
            score = grade(agent, passive, reference)
            assert 0 <= cost(agent) <= 1
            assert 0 <= score['grade'] <= 1 and math.isfinite(score['raw_gain'])
            if exposed:
                improved = replace(agent, new_exposed=exposed - 1)
                assert cost(improved) < cost(agent)
                assert grade(improved, passive, reference)['grade'] >= score['grade']
            if burned:
                improved = replace(agent, new_burned_cells=burned - 1)
                assert cost(improved) < cost(agent)
                assert grade(improved, passive, reference)['grade'] >= score['grade']


def test_cost_is_invariant_to_consistent_population_land_and_grid_unit_scaling():
    scaled = Outcome(1000, 200, 10000, 1000, .025)
    assert cost(scaled) == cost(PASSIVE)
    assert cost(replace(PASSIVE, cell_area_hectares=5)) == cost(PASSIVE)
    assert grade(scaled, scaled, replace(scaled, new_exposed=0, new_burned_cells=0))['grade'] == 0


@pytest.mark.parametrize('field,value', [
    ('eligible_population', 101), ('eligible_burnable_cells', 1001), ('cell_area_hectares', .5),
])
def test_comparisons_reject_changed_denominators_or_grid(field, value):
    changed = replace(REFERENCE, **{field: value})
    with pytest.raises(ValueError, match='identical eligible denominators'):
        qualify(PASSIVE, changed)
    with pytest.raises(ValueError, match='identical eligible denominators'):
        grade(changed, PASSIVE, REFERENCE)


@pytest.mark.parametrize('field', ['eligible_population', 'new_exposed', 'eligible_burnable_cells', 'new_burned_cells'])
@pytest.mark.parametrize('value', [True, False, -1, 1.0, float('nan'), float('inf'), '1', None])
def test_malformed_counts_are_rejected(field, value):
    values = asdict(PASSIVE); values[field] = value
    with pytest.raises(ValueError):
        Outcome(**values)
    with pytest.raises(ValueError):
        cost(values)


@pytest.mark.parametrize('area', [True, False, 0, -1, float('nan'), float('inf'), float('-inf'), '1', None])
def test_grid_area_must_be_positive_finite_numeric(area):
    with pytest.raises(ValueError):
        replace(PASSIVE, cell_area_hectares=area)


@pytest.mark.parametrize('values', [
    dict(eligible_population=0, new_exposed=1),
    dict(new_exposed=101), dict(eligible_burnable_cells=0, new_burned_cells=1), dict(new_burned_cells=1001),
])
def test_harm_cannot_exceed_eligible_counts(values):
    with pytest.raises(ValueError):
        replace(PASSIVE, **values)


def test_no_action_count_or_extra_fields_can_affect_scoring():
    values = asdict(PASSIVE)
    for invalid in (None, [], {'new_exposed': 0}, dict(values, action_count=100), dict(values, completion_bonus=1)):
        with pytest.raises(ValueError):
            cost(invalid)
    with pytest.raises(TypeError):
        grade(PASSIVE, PASSIVE, REFERENCE, action_count=100)


@pytest.mark.parametrize('flags', [True, 'adverse', [False], [''], ['same', 'same'], {'adverse': True}])
def test_malformed_adverse_flags_are_rejected(flags):
    with pytest.raises(ValueError):
        grade(REFERENCE, PASSIVE, REFERENCE, adverse_flags=flags)


def test_full_aggregate_retains_each_task_and_requires_weak_active_evidence():
    agents, passives, references = full_rows(REFERENCE), full_rows(PASSIVE), full_rows(REFERENCE)
    missing = aggregate(agents, passives, references, expected_tasks=TASKS)
    assert missing['candidate_mean'] == 1 and missing['official_mean'] is None
    assert not missing['release_ready'] and missing['missing_tasks']['weak_active'] == sorted(TASKS)
    weak = full_rows(replace(PASSIVE, new_exposed=10, new_burned_cells=50))
    candidate = aggregate(agents, passives, references, expected_tasks=TASKS, weak_active=weak)
    assert candidate['qualification_ready'] and candidate['candidate_mean'] == 1
    assert not candidate['protocol_released'] and not candidate['release_ready']
    assert candidate['official_mean'] is None
    official = aggregate(agents, passives, references, expected_tasks=TASKS, weak_active=weak,
                         adverse_flags={TASKS[0]: ['unresolved_at_horizon']}, protocol_released=True)
    assert official['complete'] and official['qualified'] and official['release_ready']
    assert official['official_mean'] == 1
    assert set(official['by_task']) == set(TASKS)
    assert official['by_task'][TASKS[0]]['adverse_flags'] == ['unresolved_at_horizon']
    assert official['reference_proven_optimal'] is False


@pytest.mark.parametrize('missing_input', ['agents', 'passives', 'references', 'weak_active'])
def test_aggregate_never_silently_drops_missing_expected_tasks(missing_input):
    inputs = dict(agents=full_rows(REFERENCE), passives=full_rows(PASSIVE),
                  references=full_rows(REFERENCE), weak_active=full_rows(PASSIVE))
    del inputs[missing_input][TASKS[0]]
    result = aggregate(**inputs, expected_tasks=TASKS)
    assert result['official_mean'] is None and not result['release_ready']
    assert result['missing_tasks'][missing_input] == [TASKS[0]]
    assert len(result['by_task']) == 24
    if missing_input != 'weak_active':
        assert result['by_task'][TASKS[0]] is None and result['candidate_mean'] is None


def test_aggregate_withholds_mean_for_even_one_unqualified_task():
    references = full_rows(REFERENCE); references[TASKS[0]] = PASSIVE
    result = aggregate(full_rows(REFERENCE), full_rows(PASSIVE), references,
                       expected_tasks=TASKS, weak_active=full_rows(PASSIVE))
    assert result['candidate_mean'] is result['official_mean'] is None
    assert result['unqualified_tasks'] == {TASKS[0]: 'no_positive_cost_headroom'}
    assert result['by_task'][TASKS[0]]['grade'] is None


def test_nearly_solving_weak_active_baseline_blocks_release():
    weak = full_rows(PASSIVE); weak[TASKS[0]] = REFERENCE
    result = aggregate(full_rows(REFERENCE), full_rows(PASSIVE), full_rows(REFERENCE),
                       expected_tasks=TASKS, weak_active=weak)
    assert result['candidate_mean'] == 1 and result['official_mean'] is None
    assert result['weak_active_failures'] == {TASKS[0]: 'weak_active_grade_exceeds_0.6'}


def test_weak_active_grade_exactly_at_the_protocol_limit_is_allowed():
    # Population-only cost makes this 0.6 without a rounded mixture of weights.
    passive, reference = Outcome(10, 10, 0, 0, 1), Outcome(10, 0, 0, 0, 1)
    weak = Outcome(10, 4, 0, 0, 1)
    result = aggregate(full_rows(reference), full_rows(passive), full_rows(reference),
                       expected_tasks=TASKS, weak_active=full_rows(weak), protocol_released=True)
    assert result['release_ready']


@pytest.mark.parametrize('flag', [1, 0, 'released', None])
def test_protocol_release_flag_is_explicit_boolean(flag):
    with pytest.raises(ValueError, match='protocol_released must be bool'):
        aggregate(full_rows(REFERENCE), full_rows(PASSIVE), full_rows(REFERENCE),
                  expected_tasks=TASKS, weak_active=full_rows(PASSIVE), protocol_released=flag)


@pytest.mark.parametrize('tasks', [(), TASKS[:23], TASKS + ('extra',), TASKS[:-1] + (TASKS[0],)])
def test_official_domain_cannot_be_cherry_picked_or_duplicated(tasks):
    with pytest.raises(ValueError, match='exactly 24'):
        aggregate({}, {}, {}, expected_tasks=tasks)


def test_unexpected_or_malformed_orphan_input_is_rejected_without_mutation():
    agents = full_rows(REFERENCE)
    agents['unexpected'] = REFERENCE
    with pytest.raises(ValueError, match='unexpected tasks'):
        aggregate(agents, full_rows(PASSIVE), full_rows(REFERENCE), expected_tasks=TASKS)
    bad = {TASKS[0]: dict(asdict(PASSIVE), new_exposed=True)}
    with pytest.raises(ValueError):
        aggregate(bad, {}, {}, expected_tasks=TASKS)
    assert bad[TASKS[0]]['new_exposed'] is True
