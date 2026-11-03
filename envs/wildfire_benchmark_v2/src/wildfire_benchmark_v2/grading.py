"""Pure grading for wildfire-benchmark-protocol-v2, new-harm-cost-v1.

Callers establish replay, completion and evidence eligibility before passing
outcomes here. Scoring reads no data files, runs no policy and awards no credit
for time elapsed, actions, inherited harm or an unchanged world. Reference
quality is best-observed, never a claim of optimality or physical calibration.
Import captures this module's source for the evaluator's loaded-code guard.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

from . import _capture_source

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)


PROTOCOL_VERSION = 'wildfire-benchmark-protocol-v2'
OBJECTIVE_VERSION = 'new-harm-cost-v1'
GRADE_VERSION = 'reference-improvement-v2'
REFERENCE_KIND = 'best_observed_scripted_online'
REFERENCE_PROVEN_OPTIMAL = False
POPULATION_WEIGHT = 0.8
BURNED_AREA_WEIGHT = 0.2
EXPECTED_TASK_COUNT = 24
MAXIMUM_WEAK_ACTIVE_GRADE = 0.6


@dataclass(frozen=True, slots=True)
class Outcome:
    eligible_population: int
    new_exposed: int
    eligible_burnable_cells: int
    new_burned_cells: int
    cell_area_hectares: float

    def __post_init__(self):
        for field in ('eligible_population', 'new_exposed',
                      'eligible_burnable_cells', 'new_burned_cells'):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(field + ' must be a nonnegative integer, excluding bool')
        if self.new_exposed > self.eligible_population:
            raise ValueError('new_exposed exceeds eligible_population')
        if self.new_burned_cells > self.eligible_burnable_cells:
            raise ValueError('new_burned_cells exceeds eligible_burnable_cells')
        if type(self.cell_area_hectares) not in (int, float):
            raise ValueError('cell_area_hectares must be positive and finite, excluding bool')
        try:
            area = float(self.cell_area_hectares)
        except (OverflowError, ValueError) as error:
            raise ValueError('cell_area_hectares must be positive and finite') from error
        if not math.isfinite(area) or area <= 0:
            raise ValueError('cell_area_hectares must be positive and finite')
        object.__setattr__(self, 'cell_area_hectares', area)


OutcomeLike = Outcome | Mapping[str, int | float]
_OUTCOME_FIELDS = frozenset(Outcome.__dataclass_fields__)


def _outcome(value: OutcomeLike) -> Outcome:
    if type(value) is Outcome:
        return value
    if not isinstance(value, Mapping) or set(value) != _OUTCOME_FIELDS:
        raise ValueError('Outcome requires exactly: ' + ', '.join(sorted(_OUTCOME_FIELDS)))
    return Outcome(**dict(value))


def _compatible(*outcomes: Outcome) -> None:
    identities = {(o.eligible_population, o.eligible_burnable_cells, o.cell_area_hectares)
                  for o in outcomes}
    if len(identities) != 1:
        raise ValueError('Compared outcomes must have identical eligible denominators and cell area')


def weighted_components(outcome: OutcomeLike) -> dict:
    """Return fractions and weighted costs; a zero denominator contributes zero."""
    o = _outcome(outcome)
    population = o.new_exposed / o.eligible_population if o.eligible_population else 0.0
    burned = o.new_burned_cells / o.eligible_burnable_cells if o.eligible_burnable_cells else 0.0
    return dict(population_fraction=population, burned_fraction=burned,
                population=POPULATION_WEIGHT * population,
                burned_area=BURNED_AREA_WEIGHT * burned)


def cost(outcome: OutcomeLike) -> float:
    components = weighted_components(outcome)
    return math.fsum((components['population'], components['burned_area']))


def qualify(passive: OutcomeLike, reference: OutcomeLike) -> dict:
    """Assess reference headroom; incompatible worlds raise ValueError.

    Nonqualification is explicit and never supplies an invented score. Exposure
    is the primary qualification constraint even when area could offset it.
    """
    p, r = _outcome(passive), _outcome(reference)
    _compatible(p, r)
    passive_cost, reference_cost = cost(p), cost(r)
    headroom = passive_cost - reference_cost
    people, cells = p.new_exposed - r.new_exposed, p.new_burned_cells - r.new_burned_cells
    if people < 0:
        reason = 'reference_increases_exposure'
    elif headroom <= 0:
        reason = 'no_positive_cost_headroom'
    elif not (people >= 1 or (people == 0 and cells >= 1)):
        reason = 'insufficient_reference_benefit'
    else:
        reason = None
    return dict(qualified=reason is None, reason=reason, passive_cost=passive_cost,
                reference_cost=reference_cost, cost_gap=headroom,
                exposure_improvement_people=people, burn_improvement_cells=cells,
                reference_kind=REFERENCE_KIND, reference_proven_optimal=REFERENCE_PROVEN_OPTIMAL)


def _flags(value: Sequence[str]) -> list[str]:
    if (not isinstance(value, (list, tuple))
            or any(type(flag) is not str or not flag.strip() for flag in value)
            or len(set(value)) != len(value)):
        raise ValueError('adverse_flags must be a list/tuple of distinct nonempty strings')
    return list(value)


def grade(agent: OutcomeLike, passive: OutcomeLike, reference: OutcomeLike, *,
          adverse_flags: Sequence[str] = ()) -> dict:
    """Bound normalized gain to [0, 1], retaining its signed unclipped value.

    ``raw_gain`` is (passive_cost-agent_cost)/cost_gap, not absolute cost saved.
    ``cost_improvement`` retains the latter. Unqualified references give None
    for grade, raw_gain and reference_exceeded; adverse flags never alter cost.
    """
    a, p, r = _outcome(agent), _outcome(passive), _outcome(reference)
    _compatible(a, p, r)
    qualification = qualify(p, r)
    agent_cost = cost(a)
    improvement = qualification['passive_cost'] - agent_cost
    gain = None
    if qualification['qualified']:
        gain = improvement / qualification['cost_gap']
        if not math.isfinite(gain):
            raise ValueError('Normalized gain must be finite')
    return dict(version=GRADE_VERSION, qualified=qualification['qualified'],
        qualification_reason=qualification['reason'], grade=None if gain is None else min(1.0, max(0.0, gain)),
        raw_gain=gain, reference_exceeded=None if gain is None else agent_cost < qualification['reference_cost'],
        cost_improvement=improvement, cost_gap=qualification['cost_gap'],
        costs=dict(agent=agent_cost, passive=qualification['passive_cost'], reference=qualification['reference_cost']),
        weighted_components=dict(agent=weighted_components(a), passive=weighted_components(p), reference=weighted_components(r)),
        adverse_flags=_flags(adverse_flags), reference_kind=REFERENCE_KIND,
        reference_proven_optimal=REFERENCE_PROVEN_OPTIMAL)


def _task_mapping(value, expected: set[str], label: str) -> Mapping:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(label + ' must be a mapping of task IDs')
    if set(value) - expected:
        raise ValueError(label + ' contains unexpected tasks: ' + ', '.join(sorted(set(value) - expected)))
    return value


def aggregate(agents: Mapping[str, OutcomeLike], passives: Mapping[str, OutcomeLike],
              references: Mapping[str, OutcomeLike], *, expected_tasks: Sequence[str],
              weak_active: Mapping[str, OutcomeLike] | None = None,
              adverse_flags: Mapping[str, Sequence[str]] | None = None,
              protocol_released: bool = False) -> dict:
    """Aggregate the complete 24-task protocol domain without dropping rows.

    ``candidate_mean`` exists only if all expected agent/passive/reference
    triples qualify. ``official_mean`` additionally requires every weak active
    baseline grade to be at most 0.6 and explicit protocol_released=True.
    Numeric readiness is returned separately as qualification_ready. This is
    scoring status, not permission to publish. Missing/unqualified cases and absent or
    too-strong weak baselines remain explicit; no mean over a subset is emitted.
    The caller supplies the frozen expected IDs, not a selection from results.
    """
    if type(protocol_released) is not bool:
        raise ValueError('protocol_released must be bool')
    if (not isinstance(expected_tasks, (list, tuple)) or len(expected_tasks) != EXPECTED_TASK_COUNT
            or any(type(task) is not str or not task.strip() for task in expected_tasks)
            or len(set(expected_tasks)) != len(expected_tasks)):
        raise ValueError('expected_tasks must contain exactly 24 distinct nonempty task IDs')
    expected = set(expected_tasks)
    agents = _task_mapping(agents, expected, 'agents')
    passives = _task_mapping(passives, expected, 'passives')
    references = _task_mapping(references, expected, 'references')
    weak_active = _task_mapping({} if weak_active is None else weak_active, expected, 'weak_active')
    flags = _task_mapping({} if adverse_flags is None else adverse_flags, expected, 'adverse_flags')
    # Validate even orphan inputs: a missing companion cannot hide malformed data.
    agents = {key: _outcome(value) for key, value in agents.items()}
    passives = {key: _outcome(value) for key, value in passives.items()}
    references = {key: _outcome(value) for key, value in references.items()}
    weak_active = {key: _outcome(value) for key, value in weak_active.items()}
    flags = {key: _flags(value) for key, value in flags.items()}
    missing = {name: sorted(expected - set(values)) for name, values in (
        ('agents', agents), ('passives', passives), ('references', references), ('weak_active', weak_active))}
    by_task, weak_grades, unqualified, weak_failures = {}, {}, {}, {}
    for task in expected_tasks:
        if task not in agents or task not in passives or task not in references:
            by_task[task] = None
        else:
            by_task[task] = grade(agents[task], passives[task], references[task], adverse_flags=flags.get(task, ()))
            if not by_task[task]['qualified']:
                unqualified[task] = by_task[task]['qualification_reason']
        if task in weak_active and task in passives and task in references:
            weak = grade(weak_active[task], passives[task], references[task])
            weak_grades[task] = weak
            if not weak['qualified']:
                weak_failures[task] = weak['qualification_reason']
            elif weak['grade'] > MAXIMUM_WEAK_ACTIVE_GRADE:
                weak_failures[task] = 'weak_active_grade_exceeds_0.6'
        else:
            weak_grades[task] = None
    complete = not any(missing[key] for key in ('agents', 'passives', 'references'))
    qualified = complete and not unqualified
    candidate_mean = math.fsum(row['grade'] for row in by_task.values()) / EXPECTED_TASK_COUNT if qualified else None
    qualification_ready = qualified and not missing['weak_active'] and not weak_failures
    ready = qualification_ready and protocol_released
    return dict(protocol_version=PROTOCOL_VERSION, reference_kind=REFERENCE_KIND,
        reference_proven_optimal=REFERENCE_PROVEN_OPTIMAL, expected_tasks=list(expected_tasks),
        expected_count=EXPECTED_TASK_COUNT, complete=complete, qualified=qualified,
        qualification_ready=qualification_ready, protocol_released=protocol_released, release_ready=ready,
        candidate_mean=candidate_mean, official_mean=candidate_mean if ready else None,
        missing_tasks=missing, unqualified_tasks=unqualified, weak_active_failures=weak_failures,
        by_task=by_task, weak_active_by_task=weak_grades)
