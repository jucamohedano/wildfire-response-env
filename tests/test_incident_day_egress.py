import copy
import math

import pytest

from envs.wildfire_response.incident_day.operations import Operations, _movement_safe
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork, make_leg


def center(column, row):
    return [column * 100 + 50, 1450 - row * 100]


def make_router(nodes, edges, mask=None):
    positions = {name: center(*cell) for name, cell in nodes.items()}
    document = dict(version='coastal-road-network-v1', crs='EPSG:25830',
                    nodes=[dict(id=name, position=value) for name, value in positions.items()],
                    edges=[dict(id=name, source=start, target=end,
                                coordinates=[positions[start], positions[end]],
                                length_m=math.dist(positions[start], positions[end]),
                                speed_kph=3, **extra)
                           for name, start, end, extra in edges])
    document['sha256'] = digest(document)
    roads = RoadNetwork(document, document['sha256'])
    inputs = dict(grid=dict(width=15, height=15, crs='EPSG:25830',
                            transform=[100, 0, 0, 0, -100, 1500]),
                  fuel_classes=[[1] * 15 for _ in range(15)],
                  fuel_valid_mask=[[1] * 15 for _ in range(15)],
                  study_mask=[[1] * 15 for _ in range(15)])
    return HazardRouter(roads, inputs, [[100] * 15 for _ in range(15)], walk_mask=mask)


def options(router, origin, destination, *, mode='engine', burning={(5, 7)},
            wind=(1, 0), excluded=(), egress=True):
    return router.options(center(*origin), list(destination), mode=mode, burning=burning,
                          wind=wind, excluded_cells=excluded, target_metric=center(*destination),
                          emergency_egress=egress, limit=1)


@pytest.mark.parametrize('mode', ['engine', 'crew'])
def test_return_escapes_buffer_over_multiple_minutes_and_normal_mode_stays_blocked(mode):
    router = make_router({'start': (6, 7), 'base': (10, 7)}, [('out', 'start', 'base', {})])
    assert not options(router, (6, 7), (10, 7), mode=mode, egress=False)['options']
    journey = options(router, (6, 7), (10, 7), mode=mode)['options'][0]['journey']
    assert journey['emergency_egress']['version'] == 'return-only-buffer-exit-v1'
    states = []
    for minute in range(journey['duration_minutes']):
        result = router.safe_journey_segment(journey, minute, {(5, 7)}, [1, 0],
                                             mode=mode, egress_status=True)
        assert result['safe']
        journey['emergency_egress']['exited'] = result['egress_exited']
        states.append(result['egress_exited'])
    assert states.count(False) >= 2
    assert states[-1] and states == sorted(states)


def test_excludes_burning_excluded_destination_and_unsupported_walking():
    router = make_router({'start': (6, 7), 'base': (10, 7)}, [('out', 'start', 'base', {})])
    assert not options(router, (6, 7), (10, 7), burning={(6, 7)})['options']
    assert not options(router, (6, 7), (7, 7))['options']
    assert not options(router, (6, 7), (10, 7), excluded={(7, 7)})['options']
    mask = [[1] * 15 for _ in range(15)]
    mask[7][6] = 0
    walk_router = make_router({'access': (7, 7), 'base': (10, 7)},
                              [('out', 'access', 'base', {})], mask)
    assert not options(walk_router, (6, 7), (10, 7), mode='crew')['options']


def test_crew_can_exit_on_supported_walk_to_directed_road():
    router = make_router({'access': (7, 7), 'base': (10, 7)},
                         [('out', 'access', 'base', {})], [[1] * 15 for _ in range(15)])
    journey = options(router, (6, 7), (10, 7), mode='crew')['options'][0]['journey']
    assert journey['legs'][0]['mode'] == 'walk'
    assert journey['legs'][1]['edge_id'] == 'out'
    for minute in range(journey['duration_minutes']):
        result = router.safe_journey_segment(journey, minute, {(5, 7)}, [1, 0],
                                             mode='crew', egress_status=True)
        assert result['safe']
        journey['emergency_egress']['exited'] = result['egress_exited']
    assert journey['emergency_egress']['exited']


def test_direction_modes_and_reentry_remain_forbidden():
    reverse = make_router({'start': (6, 7), 'base': (10, 7)}, [('wrong', 'base', 'start', {})])
    assert not options(reverse, (6, 7), (10, 7))['options']
    restricted = make_router({'start': (6, 7), 'base': (10, 7)},
                             [('crew-only', 'start', 'base', {'allowed_modes': ['crew']})])
    assert not options(restricted, (6, 7), (10, 7), mode='engine')['options']
    assert options(restricted, (6, 7), (10, 7), mode='crew')['options']
    loop = make_router({'start': (6, 7), 'outside': (8, 8), 'back': (6, 8), 'base': (6, 10)},
                       [('a', 'start', 'outside', {}), ('b', 'outside', 'back', {}),
                        ('c', 'back', 'base', {})])
    assert not options(loop, (6, 7), (6, 10))['options']


@pytest.mark.parametrize('mode', ['engine', 'crew'])
def test_finds_longer_directed_detour_when_shortest_path_reenters_buffer(mode):
    nodes = {'start': (3, 6), 'bad': (3, 8), 'join': (3, 11), 'base': (5, 11),
             'west': (2, 6), 'south': (2, 11)}
    edges = [('short-a', 'start', 'bad', {}), ('short-b', 'bad', 'join', {}),
             ('finish', 'join', 'base', {}), ('detour-a', 'start', 'west', {}),
             ('detour-b', 'west', 'south', {}), ('detour-c', 'south', 'join', {})]
    router = make_router(nodes, edges)
    burning = {(5, 5), (5, 9)}
    wind = (-1, 0)
    hazards = router._hazards(burning, wind)
    hard = frozenset(burning)
    shortest = router._plan(tuple(center(3, 6)), (5, 11), mode, tuple(sorted(burning)),
                            wind, 0, 32, hard, (), tuple(center(5, 11)))
    assert shortest['options']
    assert shortest['options'][0]['journey']['legs'][0]['edge_id'] == 'short-a'
    assert not router._egress_path_valid(shortest['options'][0]['journey'], hazards, hard)
    result = options(router, (3, 6), (5, 11), mode=mode, burning=burning, wind=wind)
    journey = result['options'][0]['journey']
    assert [leg['edge_id'] for leg in journey['legs'][:3]] == ['detour-a', 'detour-b', 'detour-c']
    assert router._egress_path_valid(journey, hazards, hard)
    for minute in range(journey['duration_minutes']):
        checked = router.safe_journey_segment(journey, minute, burning, wind, mode=mode, egress_status=True)
        assert checked['safe']
        journey['emergency_egress']['exited'] = checked['egress_exited']
    assert journey['emergency_egress']['exited']
    without_detour = make_router(nodes, edges[:3])
    assert not options(without_detour, (3, 6), (5, 11), mode=mode,
                       burning=burning, wind=wind)['options']


def test_walk_legs_cannot_reenter_after_exit():
    router = make_router({'start': (3, 6), 'base': (5, 11)}, [('road', 'start', 'base', {})])
    burning = {(5, 5), (5, 9)}
    hazards = router._hazards(burning, (-1, 0))
    hard = frozenset(burning)
    walking = [make_leg('walk', [center(3, 6), center(3, 8)], 3)]
    assert router._egress_legs(walking, False, hard, hazards) is None
    final_walk = [make_leg('walk', [center(2, 8), center(3, 8)], 3)]
    assert router._egress_legs(final_walk, True, hard, hazards) is None
    assert not router._egress_path_valid({'legs': walking}, hazards, hard)


def test_changed_fire_and_exclusions_block_the_next_segment_and_no_reentry():
    router = make_router({'start': (6, 7), 'base': (10, 7)}, [('out', 'start', 'base', {})])
    journey = options(router, (6, 7), (10, 7))['options'][0]['journey']
    assert not router.safe_journey_segment(journey, 0, {(6, 7)}, [1, 0], mode='engine',
                                           egress_status=True)['safe']
    assert not router.safe_journey_segment(journey, 0, {(5, 7)}, [1, 0], mode='engine',
                                           excluded_cells={(6, 7)}, egress_status=True)['safe']
    assert not router.safe_journey_segment(journey, 1, {(5, 7)}, [1, 0], mode='engine',
                                           excluded_cells={(7, 7)}, egress_status=True)['safe']
    assert not router.safe_journey_segment(journey, 0, {(7, 7)}, [1, 0], mode='engine',
                                           egress_status=True)['safe']
    journey['emergency_egress']['exited'] = True
    assert not router.safe_journey_segment(journey, 0, {(5, 7)}, [1, 0], mode='engine',
                                           egress_status=True)['safe']


def test_operations_only_trusts_profile_and_planner_with_full_callback():
    router = make_router({'start': (6, 7), 'base': (10, 7)}, [('out', 'start', 'base', {})])
    plan = options(router, (6, 7), (10, 7))['options'][0]['journey']
    unit = dict(id='engine', kind='engine', position=center(6, 7), base_position=center(10, 7),
                home_base='base', state='working', water_litres=100, capacity_litres=100,
                work_remaining=240)
    policy = {'emergency_egress_policy': 'return-only-buffer-exit-v1'}
    planner = lambda unit, minute: copy.deepcopy(plan)
    operation = Operations([unit], [], [], policy, return_planner=planner)
    assert operation.release('engine', plan, 0)
    callback = lambda journey, minute, kind: router.safe_journey_segment(
        journey, minute - 1, {(5, 7)}, [1, 0], mode=kind, egress_status=True)
    def tick(minute, movement_check):
        operation.tick(minute, wind=0, daylight=True,
                       hazard_check=lambda position, kind: router.safe_position(position, {(5, 7)}, [1, 0]),
                       suppress=lambda unit: None, emit=lambda *args, **kwargs: None,
                       movement_check=movement_check)
    tick(1, None)
    assert operation.resources['engine']['state'] == 'blocked'
    tick(2, lambda *args: True)
    assert operation.resources['engine']['state'] == 'blocked'
    tick(3, callback)
    assert operation.resources['engine']['state'] == 'returning'
    assert operation.resources['engine']['position'] != center(6, 7)
    for minute in range(4, 13):
        tick(minute, callback)
        resource = operation.resources['engine']
        if resource['state'] == 'returning' and resource['journey']['emergency_egress']['exited']:
            break
    assert resource['journey']['emergency_egress']['exited']
    default = Operations([unit], [], [], {}, return_planner=planner)
    assert not default.release('engine', plan, 0)
    untrusted = Operations([unit], [], [], policy, return_planner=lambda unit, minute: None)
    assert not untrusted.release('engine', plan, 0)


def test_normal_movement_and_exited_egress_still_require_endpoint_check():
    journey = {'emergency_egress': {'version': 'return-only-buffer-exit-v1', 'exited': True},
               '_trusted_return_egress': True}
    unsafe = lambda position, kind: False
    checked = lambda journey, minute, kind: {'safe': True, 'egress_exited': True}
    assert not _movement_safe(journey, 1, 'engine', [0, 0], unsafe, checked,
                              returning_egress=True)
    assert not _movement_safe(journey, 1, 'engine', [0, 0], unsafe,
                              lambda *args: True, returning_egress=False)
