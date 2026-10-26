"""Versioned synthetic population and unique burned-footprint accounting."""
import copy
import math


VERSION = 'wildfire-benchmark-outcomes-v1'
RULES = dict(horizon_minutes=120, population_weight=0.8, area_weight=0.2,
             population_basis='Unexposed synthetic population; exposure is not death',
             safety_basis='No exposed population and no unresolved evacuation',
             area_basis='Unique additionally burned initially burnable, initially unburned cells',
             ranking='Descending safe_success, protected_population_fraction, negative additional_burned_hectares; abandonment and censoring rank below valid episodes',
             boundary_policy='Unscored, diagnostic score zero, excluded from training',
             abandonment_policy='Scored failure with zero reward')


def score_outcomes(initial, final, ever_burned, *, terminal, abandoned=False, boundary_censored=False):
    initial_fire = {tuple(point) for point in initial['burning_cells']}
    eligible = {tuple(point) for point in initial['burnable_cells']} - initial_fire
    additional = len(set(ever_burned) & eligible)
    population = sum(group['population'] for group in final['population'].values())
    exposed = sum(group['exposed'] for group in final['population'].values())
    unresolved = sum(group['population'] > 0 and group['status'] in ('evacuating', 'blocked')
                     for group in final['population'].values())
    protected = (population - exposed) / population if population else 1.0
    unburned = 1.0 - additional / len(eligible) if eligible else 1.0
    eligible_score = terminal and not boundary_censored
    graded = 0.8 * protected + 0.2 * unburned if population else unburned
    score = graded if eligible_score and not abandoned else 0.0
    safe = exposed == 0 and unresolved == 0
    return dict(safe_success=bool(safe and eligible_score and not abandoned),
                protected_population_fraction=protected, population=population, exposed_population=exposed,
                initial_burned_hectares=len(initial_fire) * initial['cell_area_hectares'],
                additional_burned_hectares=additional * initial['cell_area_hectares'],
                initially_burnable_unburned_cells=len(eligible), additional_burned_cells=additional,
                unburned_fraction=unburned, active_fire_cells=len(final['burning_cells']),
                active_fire=bool(final['burning_cells']), unresolved_evacuations=unresolved,
                graded_score=score, diagnostic_graded_score=graded, score_eligible=eligible_score,
                suitable_for_training=eligible_score, boundary_censored=boundary_censored,
                invalid_abandonment=abandoned,
                ranking_key=([int(safe), protected, -additional * initial['cell_area_hectares']]
                             if eligible_score and not abandoned else [-1, 0.0, 0.0]), outcome_version=VERSION)


def recompute_outcomes(records, *, terminal=True, abandoned=False, boundary_censored=False):
    """Independent evaluator over minute records; never calls the production scorer."""
    if not isinstance(records, list) or not records or records[0].get('minute') != 0:
        raise ValueError('Expected initial and contiguous per-minute evaluator records')
    first = records[0]
    for minute, record in enumerate(records):
        if record.get('minute') != minute or not isinstance(record.get('population'), dict):
            raise ValueError('Evaluator records must be contiguous')
        if any(group['population'] < 0 or not 0 <= group['exposed'] <= group['population']
               for group in record['population'].values()):
            raise ValueError('Invalid synthetic population accounting')
        if {key: group['population'] for key, group in record['population'].items()} != {
                key: group['population'] for key, group in first['population'].items()}:
            raise ValueError('Population inventory changed')
    area = first['cell_area_hectares']
    if type(area) not in (float, int) or not math.isfinite(area) or area <= 0:
        raise ValueError('Expected a positive geographic cell area')
    starting = set(map(tuple, first['burning_cells']))
    available = set(map(tuple, first['burnable_cells'])).difference(starting)
    footprint = set()
    for record in records:
        footprint.update(map(tuple, record['burning_cells']))
        footprint.update(map(tuple, record.get('burning_before_suppression', [])))
        footprint.update(map(tuple, record['burned_cells']))
    count = len(available.intersection(footprint))
    last = records[-1]
    total = sum(value['population'] for value in last['population'].values())
    lost = sum(value['exposed'] for value in last['population'].values())
    moving = len([value for value in last['population'].values()
                  if value['population'] > 0 and value['status'] in ('evacuating', 'blocked')])
    fraction = (total - lost) / total if total else 1.0
    remaining = 1 - count / len(available) if available else 1.0
    diagnostic = .8 * fraction + .2 * remaining if total else remaining
    scored = bool(terminal and not boundary_censored)
    success = lost == 0 and moving == 0 and scored and not abandoned
    return dict(safe_success=success, protected_population_fraction=fraction,
                population=total, exposed_population=lost,
                initial_burned_hectares=len(starting) * area,
                additional_burned_hectares=count * area,
                initially_burnable_unburned_cells=len(available), additional_burned_cells=count,
                unburned_fraction=remaining, active_fire_cells=len(last['burning_cells']),
                active_fire=bool(last['burning_cells']), unresolved_evacuations=moving,
                graded_score=diagnostic if scored and not abandoned else 0.0,
                diagnostic_graded_score=diagnostic, score_eligible=scored, suitable_for_training=scored,
                boundary_censored=boundary_censored, invalid_abandonment=abandoned,
                ranking_key=([int(success), fraction, -count * area]
                             if scored and not abandoned else [-1, 0.0, 0.0]), outcome_version=VERSION)


def verify_outcomes(expected, actual):
    if set(expected) != set(actual):
        raise ValueError('Outcome fields differ from independent recomputation')
    for key, value in expected.items():
        if isinstance(value, float):
            if not math.isclose(value, actual[key], rel_tol=0, abs_tol=1e-12):
                raise ValueError('Independent outcome mismatch: ' + key)
        elif value != actual[key]:
            raise ValueError('Independent outcome mismatch: ' + key)
    return copy.deepcopy(actual)
