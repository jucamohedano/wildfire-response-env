"""Source-pinned synthetic incident and temporary posts on cached real geography."""

import copy
import math

from ..regional.ops.generator import digest
from .calibrated_scenarios import build_egress_scenario
from .data import _check_hash
from .routing import HazardRouter
from .scenarios import DayFire


PACK_PIN = '50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d'
NETWORK_PIN = '12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16'
SNAPSHOT_PIN = 'fd276b8a4a799cffedd268f485a97aaff5ff88d83d11726be0e23894d282f323'
SECTOR = (16, 28)
POST_NODES = ('osm:n321315386', 'osm:n6119295366')
HOME_NODES = ('osm:n595570962', 'osm:n4341595138')
ALLOWED_ROAD_EDGES = frozenset(
    f'osm:w{way}:{part}:{direction}'
    for way, parts in ((374742043, ('0000', '0001', '0002', '0003')),
                       (425501364, ('0000',)), (47052550, ('0010',)), (652843749, ('0000',)))
    for part in parts for direction in ('f', 'r'))
WIND_ROAD_EDGES = ALLOWED_ROAD_EDGES | frozenset(
    f'osm:w29211939:{part}:{direction}' for part in ('0009', '0010', '0011')
    for direction in ('f', 'r'))


def _burning(fire):
    return {(column, row) for row, cells in enumerate(fire.cells)
            for column, cell in enumerate(cells) if fire.burning(cell)}


def _warm_handover(inputs, initial_fire, seed, wind):
    fire = DayFire(inputs, seed)
    fire.set_wind(x=wind[0], y=wind[1])
    unknown = {(cell['column'], cell['row']) for cell in
               inputs['provenance']['fuel_resolution']['document']['cells']
               if cell['land_kind'] == 'unresolved'}
    for column, row in initial_fire:
        if not inputs['fuel_known_mask'][row][column]:
            raise ValueError('Initial incident footprint requires source-known fuel')
        fire.add_fire(column, row)
    for unused in range(60):
        active = _burning(fire)
        if any(max(abs(x - column), abs(y - row)) <= 1
               for x, y in unknown for column, row in active):
            raise ValueError('Warm handover reaches unresolved land or water')
        fire.step()
        if fire.boundary_contact_at is not None:
            raise ValueError('Warm handover reaches the domain boundary')
    active = _burning(fire)
    if any(max(abs(x - column), abs(y - row)) <= 1
           for x, y in unknown for column, row in active):
        raise ValueError('Warm handover reaches unresolved land or water')
    footprint = sum(cell['burned'] > 0 or fire.burning(cell)
                    for row in fire.cells for cell in row)
    if footprint * inputs['grid']['cell_km'] ** 2 * 100 <= 500:
        raise ValueError('Warm handover must exceed 500 hectares')
    return active


def _supported_journey(router, journey, hazards, allowed_edges=ALLOWED_ROAD_EDGES):
    if not journey['legs']:
        return False
    for leg in journey['legs']:
        if leg['mode'] == 'road':
            if leg['edge_id'] not in allowed_edges:
                return False
            segment_mode = 'engine'
        elif leg['mode'] == 'walk':
            segment_mode = 'crew'
        else:
            return False
        if not router._safe_line(leg['coordinates'], hazards, segment_mode):
            return False
    return True


def _validate_posts(inputs, network, burning, wind):
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'])
    hazards = router._hazards(burning, wind)
    for post_id in POST_NODES:
        if post_id not in network.nodes:
            raise ValueError('Declared temporary post is not in the pinned road graph')
        position = network.nodes[post_id]
        grid = router._cell(position)
        if (not router._safe_cell(grid, hazards, 'crew') or math.dist(grid, SECTOR) <= 5):
            raise ValueError('Temporary post lacks source-known safe terrain outside the restriction radius')
        for mode in ('engine', 'crew'):
            result = router.options(position, list(SECTOR), mode=mode, burning=burning, wind=wind, limit=32)
            options = result['options']
            if not options:
                raise ValueError('Declared post has no mapped incident approach')
            option = options[0]
            point = option['approach_grid']
            if not any(math.dist(cell, SECTOR) <= 3 and math.dist(cell, point) <= 3 for cell in burning):
                raise ValueError('First approach is not within useful work reach')
            back = router.options(option['approach_point'], grid, mode=mode, burning=burning,
                                  wind=wind, target_metric=position, limit=1)
            if not back['options']:
                raise ValueError('Declared approach has no mapped recovery to its post')
            returned = back['options'][0]
            if (not _supported_journey(router, option['journey'], hazards)
                    or not _supported_journey(router, returned['journey'], hazards)
                    or math.ceil(option['travel_minutes']) + math.ceil(returned['travel_minutes']) + 7 >= 480):
                raise ValueError('Approach or recovery exceeds the bounded exercise route contract')
    for home_id in HOME_NODES:
        if home_id not in network.nodes:
            raise ValueError('Declared synthetic home is not in the pinned road graph')
        home = network.nodes[home_id]
        if not router._safe_cell(router._cell(home), hazards, 'crew'):
            raise ValueError('Declared synthetic home is not on source-known safe terrain')
        for post_id in POST_NODES:
            destination = network.nodes[post_id]
            result = router.options(home, router._cell(destination), mode='crew', burning=burning,
                wind=wind, target_metric=destination, limit=1)
            if not result['options'] or not _supported_journey(router, result['options'][0]['journey'], hazards):
                raise ValueError('Synthetic home lacks a supported route to a declared destination')


def build_real_map_scenario(inputs, network, identifier, seed=None):
    from .scenarios import EGRESS_CASE_IDS, REAL_MAP_CASE_IDS

    if identifier not in REAL_MAP_CASE_IDS:
        raise ValueError('Unknown real-map exercise scenario')
    if _check_hash(inputs, 'real-map exercise pack') != PACK_PIN or network.sha256 != NETWORK_PIN:
        raise ValueError('Real-map exercise requires the pinned estimated pack and road network')
    case_index = REAL_MAP_CASE_IDS.index(identifier)
    scenario = copy.deepcopy(build_egress_scenario(inputs, network, EGRESS_CASE_IDS[case_index], seed))
    initial_fire = [[column, row] for row in range(SECTOR[1] - 2, SECTOR[1] + 3)
                    for column in range(SECTOR[0] - 2, SECTOR[0] + 3)]
    burning = _warm_handover(inputs, initial_fire, scenario['seed'], scenario['wind'])
    _validate_posts(inputs, network, burning, scenario['wind'])
    parent_sha256 = scenario['sha256']
    scenario.update(id=identifier, version='wildfire-incident-day-realmap-scenarios-v1',
                    parent_scenario_sha256=parent_sha256, sector_grid=list(SECTOR),
                    initial_fire=initial_fire, handover_staging=[])
    facilities = scenario['profile']['facilities']
    for index, node in enumerate(POST_NODES):
        position = network.nodes[node][:]
        facilities[f'center-{index}']['position'] = position[:]
        destination = next(item for item in scenario['destinations'] if item['id'] == f'destination-{index}')
        destination['position'] = position[:]
    for unit in scenario['resources']:
        if unit['kind'] not in ('engine', 'crew'):
            continue
        base_index = int(unit['home_base'].split('-')[1])
        unit['position'] = network.nodes[POST_NODES[base_index]][:]
        unit['base_position'] = unit['position'][:]
        unit['state'] = 'available' if unit['available_at'] == 0 else 'unavailable'
        unit['journey'] = None
        for field in ('sector_id', 'target_grid', 'due_at', 'blocked_state'):
            unit.pop(field, None)
    for index, group in enumerate(scenario['groups']):
        position = network.nodes[HOME_NODES[index]][:]
        group.update(home=position[:], position=position[:], status='unwarned', destination_id=None)
    scenario['profile']['version'] = 'wildfire-incident-day-realmap-profile-v1'
    scenario['temporary_posts'] = [dict(center_id=f'center-{index}', node_id=node,
        role='Synthetic exercise post on mapped road; not a historical base')
        for index, node in enumerate(POST_NODES)]
    scenario['handover_notice'] = (
        'EXERCISE. Synthetic relocated incident and temporary posts on source geography; '
        'not historical ignition, deployment, road clearance or operational validation. '
        'Short crew walking segments use supported terrain assumptions, not verified paths.')
    if case_index == 4:
        destination = next(item for item in scenario['destinations'] if item['id'] == 'destination-0')
        group = next(item for item in scenario['groups'] if item['id'] == 'population-1')
        group.update(position=destination['position'][:], status='arrived', destination_id=destination['id'])
        scenario['profile'].update(causal_coverage=True, closure_affects_occupants=True)
        scenario['population_handover'] = dict(group_id=group['id'], count=group['count'],
            destination_id=destination['id'], arrived_before_handover=True,
            role='Synthetic initial occupancy, not a historical journey or demonstration')
        scenario['handover_notice'] += (' EXERCISE. 150 people are already at the first reception centre; '
                                        'their prior journey is a declared initialization assumption.')
    scenario['source_context'] = dict(pack_sha256=PACK_PIN, network_sha256=NETWORK_PIN,
        road_snapshot_raw_sha256=SNAPSHOT_PIN, status='estimated_exercise_not_historical_replay')
    scenario['sha256'] = digest({key: value for key, value in scenario.items() if key != 'sha256'})
    return scenario


def build_real_map_v2_scenario(inputs, network, identifier, seed=None):
    from .scenarios import REAL_MAP_CASE_IDS

    identifiers = tuple(f'incident-day-realmap-v2-{index:03}' for index in range(6))
    if identifier not in identifiers:
        raise ValueError('Unknown real-map v2 exercise scenario')
    index = identifiers.index(identifier)
    scenario = copy.deepcopy(build_real_map_scenario(inputs, network, REAL_MAP_CASE_IDS[index], seed))
    parent_sha256 = scenario['sha256']
    scenario.update(id=identifier, version='wildfire-incident-day-realmap-scenarios-v2',
                    parent_scenario_sha256=parent_sha256)
    scenario['profile']['version'] = 'wildfire-incident-day-realmap-profile-v2'
    scenario['profile']['allowed_road_edge_ids'] = sorted(
        WIND_ROAD_EDGES if index == 1 else ALLOWED_ROAD_EDGES)
    if index == 1:
        scenario['initial_fire'] = [[column, row] for row in range(SECTOR[1] - 4, SECTOR[1] + 1)
                                    for column in range(SECTOR[0] - 2, SECTOR[0] + 3)]
        burning = _warm_handover(inputs, scenario['initial_fire'], scenario['seed'], scenario['wind'])
        router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'])
        base = network.nodes[POST_NODES[0]]
        before = router.options(base, list(SECTOR), mode='engine', burning=burning,
                                wind=scenario['wind'], limit=32)['options']
        after = router.options(base, list(SECTOR), mode='engine', burning=burning,
                               wind=[0., 2.], limit=32)['options']
        before_goals = {tuple(option['approach_grid']) for option in before}
        after_goals = {tuple(option['approach_grid']) for option in after}
        if not (before_goals - after_goals and after_goals):
            raise ValueError('Wind exercise requires both lost and retained approaches')
        for option, wind in ((before[0], scenario['wind']), (after[0], [0., 2.])):
            hazards = router._hazards(burning, wind)
            return_options = router.options(option['approach_point'], router._cell(base), mode='engine',
                burning=burning, wind=wind, target_metric=base, limit=1)['options']
            if (not return_options or not _supported_journey(router, option['journey'], hazards, WIND_ROAD_EDGES)
                    or not _supported_journey(router, return_options[0]['journey'], hazards, WIND_ROAD_EDGES)):
                raise ValueError('Wind exercise needs supported approach and return routes')
        scenario['events'] = [event for event in scenario['events']
                              if not (event['kind'] == 'wind' and event['minute'] == 180)]
        scenario['events'].append(dict(minute=15, kind='wind', wind=[0., 2.]))
        scenario['events'].sort(key=lambda item: item['minute'])
        scenario['profile'].update(causal_coverage=True, realmap_v2_wind_coverage=True)
    if index == 2:
        scenario['profile']['causal_coverage'] = True
    scenario['sha256'] = digest({key: value for key, value in scenario.items() if key != 'sha256'})
    return scenario
