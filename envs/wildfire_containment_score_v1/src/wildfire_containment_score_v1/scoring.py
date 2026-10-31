"""Independent accounting from the private minute audit and a pinned fixed mask."""
import hashlib
import json
import math
from collections.abc import Mapping

REWARD_VERSION = 'wildfire-extended-incident-day-outcomes-v1'


def _cells(value):
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError('Invalid cell collection')
    if any(type(cell) is not int or cell < 0 for cell in value):
        raise ValueError('Invalid cell identifier')
    result = set(value)
    if len(result) != len(value):
        raise ValueError('Duplicate cell identifier')
    return result


def mask_sha256(mask):
    """Hash sorted row-major cell IDs as compact ASCII JSON (including empty mask)."""
    encoded = json.dumps(sorted(_cells(mask)), separators=(',', ':')).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def recompute(records, *, scoring_mask, terminal, abandoned, censored):
    """Return private outcome diagnostics; the caller must keep them evaluator-only.

    scoring_mask is the fixed, trusted set of initially burnable cell IDs, not
    an episode-derived or evolving mask. The audit uses the core v2 record shape.
    """
    if any(type(flag) is not bool for flag in (terminal, abandoned, censored)):
        raise ValueError('Invalid terminal flags')
    if not isinstance(records, (list, tuple)) or not records or len(records) > 1441:
        raise ValueError('Invalid audit length')
    mask = _cells(scoring_mask)
    first = records[0]
    if not isinstance(first, Mapping):
        raise ValueError('Invalid handover')
    initial = _cells(first['footprint'])
    burnable = _cells(first['burnable'])
    if not mask <= burnable:
        raise ValueError('Scoring mask includes a non-burnable cell')
    area = first['cell_area_hectares']
    if type(area) not in (int, float) or not math.isfinite(area) or area <= 0:
        raise ValueError('Invalid cell area')
    initial_population = first['population']
    if not isinstance(initial_population, Mapping) or any(type(k) is not str for k in initial_population):
        raise ValueError('Invalid population roster')
    previous = {}
    footprint = set(initial)
    last_population = None
    for minute, record in enumerate(records):
        if not isinstance(record, Mapping) or type(record.get('minute')) is not int or record['minute'] != minute:
            raise ValueError('Audit minutes are not contiguous')
        population = record['population']
        if not isinstance(population, Mapping) or set(population) != set(initial_population):
            raise ValueError('Population roster changed')
        for identifier, group in population.items():
            if not isinstance(group, Mapping):
                raise ValueError('Invalid group')
            count, exposed = group['count'], group['exposed']
            if (type(count) is not int or count < 0 or type(exposed) is not int
                    or not 0 <= exposed <= count or type(group['status']) is not str
                    or type(group.get('protection_pending', False)) is not bool):
                raise ValueError('Invalid population inventory')
            if identifier in previous and (count != previous[identifier][0] or exposed < previous[identifier][1]):
                raise ValueError('Population inventory/exposure is inconsistent')
            previous[identifier] = (count, exposed)
        new = _cells(record['new_footprint'])
        if minute == 0 and new:
            raise ValueError('Handover new footprint must be empty')
        if minute and new & footprint:
            raise ValueError('New footprint repeats an earlier cell')
        footprint.update(new)
        last_population = population
    original_exposure = sum(group['exposed'] for group in initial_population.values())
    population_count = sum(group['count'] for group in initial_population.values())
    new_exposure = sum(group['exposed'] for group in last_population.values()) - original_exposure
    denominator = population_count - original_exposure
    protected = 1 - new_exposure / denominator if denominator else 1.
    eligible = mask - initial
    additional = (footprint - initial) & eligible
    retained = 1 - len(additional) / len(eligible) if eligible else 1.
    unresolved = sum(group['status'] in ('evacuating', 'blocked', 'unable_to_comply')
                     or group.get('protection_pending', False) for group in last_population.values())
    minute = len(records) - 1
    complete = terminal and minute == 1440 and not censored and not abandoned
    return dict(reward_version=REWARD_VERSION,
        graded_score=.8 * protected + .2 * retained if complete else 0.,
        protected_population_fraction=protected,
        additional_burned_hectares=len(additional) * area,
        inherited_burned_hectares=len(initial) * area,
        inherited_exposed_population=original_exposure, new_exposed_population=new_exposure,
        unresolved_evacuations=unresolved, safe_success=complete and not new_exposure and not unresolved,
        score_eligible=complete or abandoned, boundary_censored=censored,
        invalid_abandonment=abandoned, completed_minutes=minute)
