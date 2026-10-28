"""Additive eight-hour shift exercises; synthetic assumptions, not validation."""

import copy
import math

from ..regional.ops.generator import digest
from .routing import HazardRouter


SHIFT_CASE_IDS = tuple(f'incident-day-shift-v1-{index:03}' for index in range(6))
SHIFT_PROFILE_VERSION = 'wildfire-incident-day-shift-v1'
SHIFT_SCENARIO_VERSION = 'wildfire-incident-day-shift-scenarios-v1'
SHIFT_MINUTES = 480
GROUND_WORK_REACH_CELLS = 3
EGRESS_POLICY = 'return-only-buffer-exit-v1'


class NoSafeStaging(ValueError):
    pass


def _handover_burning(inputs, scenario):
    from .scenarios import DayFire

    fire = DayFire(inputs, scenario['seed'])
    fire.set_wind(x=scenario['wind'][0], y=scenario['wind'][1])
    for column, row in scenario['initial_fire']:
        fire.add_fire(column, row)
    for unused in range(scenario['warmup_minutes']):
        fire.step()
    return {(column, row) for row, cells in enumerate(fire.cells) for column, cell in enumerate(cells)
            if fire.burning(cell)}


def _staging(router, unit, center, burning, wind, prefer_north=False):
    from .scenarios import grid_point

    result = router.options(unit['base_position'], center, mode=unit['kind'], burning=burning,
                            wind=wind, limit=32)
    eligible = []
    for option in result['options']:
        point = option['approach_grid']
        if (router.safe_position(option['approach_point'], burning, wind, mode=unit['kind'])
                and any(math.dist(cell, center) <= GROUND_WORK_REACH_CELLS
                        and math.dist(cell, point) <= GROUND_WORK_REACH_CELLS for cell in burning)):
            return_options = router.options(option['approach_point'], grid_point(router.inputs, unit['base_position']),
                mode=unit['kind'], burning=burning, wind=wind, revision=0,
                target_metric=unit['base_position'], limit=1)['options']
            if return_options and return_options[0]['journey']['duration_minutes'] < unit['duty_end_at']:
                candidate = copy.deepcopy(option)
                candidate['return_journey'] = return_options[0]['journey']
                eligible.append(candidate)
    if not eligible:
        raise NoSafeStaging(f"No reachable safe staging with useful work reach and legal return for {unit['id']}")
    if prefer_north:
        north = [option for option in eligible if option['approach_grid'][1] < center[1]]
        if north:
            eligible = north
        eligible.sort(key=lambda option: (router.safe_position(option['approach_point'], burning, [0., -2.], mode=unit['kind']),
                                          option['approach_grid'][1], option['travel_minutes'], option['id']))
    else:
        eligible.sort(key=lambda option: (option['travel_minutes'], option['id']))
    return eligible[0]


def _recovery_fallback(router, unit, burning, wind):
    from .scenarios import grid_point

    base = unit['base_position']
    candidates = sorted((math.dist(position, base), identifier, position)
                        for identifier, position in router.network.nodes.items()
                        if 50 <= math.dist(position, base) <= 2000)
    for distance, identifier, position in candidates:
        if not router.safe_position(position, burning, wind, mode=unit['kind']):
            continue
        result = router.options(position, grid_point(router.inputs, base), mode=unit['kind'],
                                burning=burning, wind=wind, revision=0, target_metric=base, limit=1)
        if not result['options']:
            continue
        journey = copy.deepcopy(result['options'][0]['journey'])
        if journey['duration_minutes'] >= unit['duty_end_at'] or journey['distance_m'] <= 0:
            continue
        journey.update(departed_at=0, arrives_at=journey['duration_minutes'])
        return dict(node_id=identifier, position=position[:], distance_from_base_m=distance, journey=journey)
    raise ValueError(f"No reachable safe staging or legal mapped recovery for {unit['id']}")


def build_calibrated_scenario(inputs, network, identifier, seed=None):
    from .scenarios import CASE_IDS, build_scenario

    if identifier not in SHIFT_CASE_IDS:
        raise ValueError('Unknown shift exercise scenario')
    index = SHIFT_CASE_IDS.index(identifier)
    fixed_seed = 3866400 + index
    if seed is not None and seed != fixed_seed:
        raise ValueError('Shift exercise seeds are pinned')
    scenario = copy.deepcopy(build_scenario(inputs, network, CASE_IDS[index]))
    scenario.update(id=identifier, version=SHIFT_SCENARIO_VERSION, seed=fixed_seed,
                    historical_replay=False, synthetic=True)
    profile = scenario['profile']
    profile.update(version=SHIFT_PROFILE_VERSION,
                   evidence='Eight-hour shifts and inherited operational values are synthetic exercise assumptions, not labour or aviation law or physical calibration',
                   shift_minutes=SHIFT_MINUTES, roster_policy='finite_predeclared_shift_slots',
                   require_sortie_within_shift=True, require_return_within_shift=True)
    original = {unit['id']: unit for unit in scenario['resources']}
    roster = []
    for wave in range(3):
        for base_index in range(2):
            for kind in ('engine', 'crew'):
                unit = copy.deepcopy(original[f'{kind}-{base_index}'])
                unit.update(id=f'{kind}-{base_index}' if wave == 0 else f'{kind}-{base_index}-shift-{wave + 1}',
                            position=unit['base_position'][:], available_at=wave * SHIFT_MINUTES,
                            state='available' if wave == 0 else 'unavailable',
                            duty_end_at=(wave + 1) * SHIFT_MINUTES, total_work_remaining=SHIFT_MINUTES)
                for field in ('sector_id', 'target_grid', 'due_at'):
                    unit.pop(field, None)
                if wave:
                    unit['work_remaining'] = profile['duty_minutes']
                    unit['water_litres'] = unit['capacity_litres']
                if index == 5 and wave == 0 and unit['id'] == 'engine-1':
                    unit.update(state='unavailable', available_at=360)
                roster.append(unit)
    for wave in range(2):
        unit = copy.deepcopy(original['plane-0'])
        unit.update(id=f'plane-{wave}', available_at=wave * SHIFT_MINUTES,
                    state='available' if wave == 0 else 'unavailable',
                    duty_end_at=(wave + 1) * SHIFT_MINUTES, total_work_remaining=SHIFT_MINUTES)
        roster.append(unit)
    scenario['resources'] = roster
    burning = _handover_burning(inputs, scenario)
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['walk_mask'])
    center = [inputs['grid']['width'] // 2, inputs['grid']['height'] // 2]
    staged = [next(unit for unit in roster if unit['id'] == 'engine-0')]
    if index == 1:
        staged.append(next(unit for unit in roster if unit['id'] == 'crew-0'))
    scenario['handover_staging'] = []
    fallback_units = []
    for unit in staged:
        try:
            option = _staging(router, unit, center, burning, scenario['wind'], prefer_north=unit['kind'] == 'crew')
        except NoSafeStaging:
            recovery = _recovery_fallback(router, unit, burning, scenario['wind'])
            unit.update(state='returning', position=recovery['position'], journey=recovery['journey'])
            scenario['handover_staging'].append(dict(resource_id=unit['id'], kind='synthetic_failed_access_recovery',
                node_id=recovery['node_id'], distance_from_base_m=recovery['distance_from_base_m'],
                return_travel_minutes=recovery['journey']['duration_minutes'], return_distance_m=recovery['journey']['distance_m'],
                inherited_before_handover=True, risk='Synthetic mapped recovery; no ground suppression access is established'))
            fallback_units.append(unit['id'])
            continue
        unit.update(state='on_scene', position=option['approach_point'][:], sector_id='incident',
                    target_grid=center[:], due_at=0)
        scenario['handover_staging'].append(dict(resource_id=unit['id'], kind='useful_ground_staging', approach_grid=option['approach_grid'],
            travel_minutes=option['travel_minutes'], route_distance_m=option['distance_m'],
            return_travel_minutes=option['return_journey']['duration_minutes'],
            return_distance_m=option['return_journey']['distance_m'],
            inherited_before_handover=True, risk='Exercise routing assumptions; not tactical safety certification'))
    if fallback_units:
        scenario['handover_notice'] = ('EXERCISE. Partial mapped ground access; ' + ', '.join(fallback_units) +
            ' are returning from a synthetic failed-access reconnaissance. No incident-ground suppression access is established.')
    scenario['sha256'] = digest({key: value for key, value in scenario.items() if key != 'sha256'})
    return scenario


def build_egress_scenario(inputs, network, identifier, seed=None):
    from .scenarios import EGRESS_CASE_IDS

    if identifier not in EGRESS_CASE_IDS:
        raise ValueError('Unknown emergency-egress exercise scenario')
    index = EGRESS_CASE_IDS.index(identifier)
    scenario = copy.deepcopy(build_calibrated_scenario(inputs, network, SHIFT_CASE_IDS[index], seed))
    scenario.update(id=identifier, version='wildfire-incident-day-egress-scenarios-v1',
                    parent_scenario_sha256=scenario['sha256'])
    scenario['profile'].update(version='wildfire-incident-day-egress-profile-v1',
                               emergency_egress_policy=EGRESS_POLICY)
    scenario['handover_notice'] = (scenario.get('handover_notice', '') +
        ' EXERCISE. Returning ground units may leave a protective fire/wind buffer '
        'through a supported route. They cannot cross burning cells or re-enter the buffer '
        'after exit. This withdrawal rule is a synthetic assumption, not tactical safety certification.').strip()
    scenario['sha256'] = digest({key: value for key, value in scenario.items() if key != 'sha256'})
    return scenario
