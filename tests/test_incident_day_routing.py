import math

import pytest

from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork, journey_position


def center(column, row):
    return [column * 100 + 50, 1450 - row * 100]


def network(nodes, edges):
    positions = {name: center(*cell) for name, cell in nodes.items()}
    document = dict(version='coastal-road-network-v1', crs='EPSG:25830',
                    nodes=[dict(id=name, position=position) for name, position in positions.items()],
                    edges=[dict(id=name, source=start, target=end,
                                coordinates=[positions[start], positions[end]],
                                length_m=math.dist(positions[start], positions[end]), speed_kph=30)
                           for name, start, end in edges])
    document['sha256'] = digest(document)
    return RoadNetwork(document, document['sha256'])


def router(roads, *, heights=None, mask=None):
    grid = dict(width=15, height=15, crs='EPSG:25830', transform=[100, 0, 0, 0, -100, 1500])
    inputs = dict(grid=grid, fuel_classes=[[1] * 15 for _ in range(15)],
                  fuel_valid_mask=[[1] * 15 for _ in range(15)], study_mask=[[1] * 15 for _ in range(15)])
    return HazardRouter(roads, inputs, heights or [[100] * 15 for _ in range(15)], walk_mask=mask)


@pytest.mark.parametrize('mode,cap', [('engine', 'MAX_CANDIDATES'), ('crew', 'MAX_WALK_CANDIDATES')])
def test_unsafe_nearest_nodes_do_not_exhaust_the_staging_candidate_budget(monkeypatch, mode, cap):
    from envs.wildfire_response.incident_day import routing
    roads = network({'base': (0, 5), 'unsafe': (5, 4), 'safe': (5, 2)},
                    [('unsafe-link', 'base', 'unsafe'), ('safe-link', 'base', 'safe')])
    planner = router(roads)
    monkeypatch.setattr(routing, cap, 1)
    result = planner.options(center(0, 5), [5, 5], mode=mode, burning={(5, 5)}, wind=[0, 0])
    assert result['status'] == 'reachable'
    assert result['options'][0]['journey']['access_node'] == 'safe'


def test_wind_reversal_changes_safe_approach_and_cache_is_knowledge_scoped():
    roads = network({'base': (0, 5), 'north-way': (2, 3), 'south-way': (2, 7),
                     'north': (5, 3), 'south': (5, 7)},
                    [('north-way', 'base', 'north-way'), ('north', 'north-way', 'north'),
                     ('south-way', 'base', 'south-way'), ('south', 'south-way', 'south')])
    planner = router(roads)
    north_wind = planner.options(center(0, 5), [5, 5], burning={(5, 5)}, wind=[0, -1], revision=1)
    south_wind = planner.options(center(0, 5), [5, 5], burning={(5, 5)}, wind=[0, 1], revision=1)
    assert north_wind['status'] == south_wind['status'] == 'reachable'
    assert north_wind['options'][0]['journey']['access_node'] == 'south'
    assert south_wind['options'][0]['journey']['access_node'] == 'north'
    assert north_wind['options'][0]['id'] != south_wind['options'][0]['id']
    revised = planner.options(center(0, 5), [5, 5], burning={(5, 5)}, wind=[0, -1], revision=2)
    assert revised['options'][0]['id'] != north_wind['options'][0]['id']
    north_wind['options'].clear()
    assert planner.options(center(0, 5), [5, 5], burning={(5, 5)}, wind=[0, -1], revision=1)['options']
    closed = planner.options(center(0, 5), [5, 5], burning={(5, 5), (5, 7)}, wind=[0, -1], revision=1)
    assert all(option['journey']['access_node'] != 'south' for option in closed['options'])


def test_directed_mid_edge_origin_preserves_position_and_cannot_reverse():
    roads = network({'base': (1, 7), 'end': (9, 7), 'other': (11, 7)},
                    [('forward', 'base', 'end'), ('next', 'end', 'other')])
    planner = router(roads)
    origin = center(5, 7)
    result = planner.options(origin, [11, 7], burning=set(), wind=[0, 0])
    journey = result['options'][0]['journey']
    assert result['status'] == 'reachable'
    assert journey['origin'] == origin == journey['legs'][0]['coordinates'][0]
    assert journey['legs'][0]['edge_id'] == 'forward'
    assert journey['legs'][0]['destination_node'] == 'end'
    assert journey_position(journey, 0)[0] == origin
    assert all(leg['mode'] == 'road' for leg in journey['legs'])
    reverse = planner.options(origin, [1, 7], burning=set(), wind=[0, 0])
    assert reverse['status'] == 'unreachable'
    assert planner.options([origin[0], origin[1] + 1], [11, 7], burning=set(), wind=[0, 0])['status'] == 'unmapped'


def test_disconnected_roads_do_not_gain_phantom_connectors():
    roads = network({'base': (1, 1), 'dead': (2, 1), 'isolated': (10, 10), 'far': (11, 10)},
                    [('first', 'base', 'dead'), ('second', 'isolated', 'far')])
    planner = router(roads)
    assert planner.options(center(1, 1), [10, 10], burning=set(), wind=[0, 0]) == {
        'status': 'unreachable', 'options': []}
    assert planner.options(center(1, 1), [10, 10], mode='crew', burning=set(), wind=[0, 0])['status'] == 'unreachable'
    assert planner.options(center(1, 1), [14, 1], burning=set(), wind=[0, 0])['status'] == 'unmapped'
    assert planner.options(center(1, 1), [20, 1], burning=set(), wind=[0, 0])['status'] == 'unmapped'


def test_crew_uses_passable_grid_route_and_slope_slows_walk():
    roads = network({'base': (1, 7), 'access': (6, 7)}, [('road', 'base', 'access')])
    mask = [[1] * 15 for _ in range(15)]
    mask[7][7] = 0
    heights = [[100] * 15 for _ in range(15)]
    heights[6][7] = 150
    heights[8][7] = 150
    planner = router(roads, heights=heights, mask=mask)
    result = planner.options(center(1, 7), [8, 7], mode='crew', burning=set(), wind=[0, 0])
    assert result['status'] == 'reachable'
    option = result['options'][0]
    assert option['approach_grid'] == [8, 7]
    walks = [leg for leg in option['journey']['legs'] if leg['mode'] == 'walk']
    assert len(walks) >= 4
    assert all(center(7, 7) not in leg['coordinates'] for leg in walks)
    assert sum(leg['duration_minutes'] for leg in walks) > sum(leg['distance_m'] for leg in walks) / 50
    assert planner.safe_position(center(7, 7), set(), [0, 0], mode='crew') is False


def test_safe_segment_checks_next_minute_and_detects_fire_between_endpoints():
    roads = network({'base': (1, 7), 'end': (11, 7)}, [('road', 'base', 'end')])
    planner = router(roads)
    journey = planner.options(center(1, 7), [11, 7], burning=set(), wind=[0, 0])['options'][0]['journey']
    assert planner.safe_journey_segment(journey, 0, set(), [0, 0])
    assert not planner.safe_journey_segment(journey, 0, {(5, 7)}, [0, 0])
    assert planner.safe_journey_segment(journey, journey['duration_minutes'], {(5, 7)}, [0, 0])
    assert not planner.safe_position(center(6, 7), {(5, 7)}, [1, 0])
    assert planner.safe_position(center(7, 7), {(5, 7)}, [-1, 0])


def test_missing_walking_terrain_is_unmapped_not_graph_failure():
    roads = network({'base': (1, 7), 'access': (6, 7)}, [('road', 'base', 'access')])
    mask = [[0] * 15 for _ in range(15)]
    planner = router(roads, mask=mask)
    assert planner.options(center(1, 7), [8, 7], mode='crew', burning=set(), wind=[0, 0])['status'] == 'unmapped'
    with pytest.raises(ValueError):
        planner.options(center(1, 7), [8, 7], mode='aircraft', burning=set(), wind=[0, 0])


def test_blocked_and_incomplete_map_are_distinct():
    roads = network({'base': (1, 7), 'end': (11, 7)}, [('road', 'base', 'end')])
    planner = router(roads)
    assert planner.options(center(1, 7), [11, 7], burning={(6, 7)}, wind=[0, 0])['status'] == 'blocked'
    outside = network({'base': (1, 7), 'outside': (16, 7), 'end': (11, 7)},
                      [('out', 'base', 'outside'), ('back', 'outside', 'end')])
    assert router(outside).options(center(1, 7), [11, 7], burning=set(), wind=[0, 0])['status'] == 'unmapped'


def test_reciprocal_edge_allows_both_directions_from_midpoint():
    roads = network({'west': (1, 7), 'east': (11, 7)},
                    [('eastbound', 'west', 'east'), ('westbound', 'east', 'west')])
    planner = router(roads)
    origin = center(6, 7)
    for target, edge in [([1, 7], 'westbound'), ([11, 7], 'eastbound')]:
        result = planner.options(origin, target, burning=set(), wind=[0, 0])
        assert result['status'] == 'reachable'
        leg = result['options'][0]['journey']['legs'][0]
        assert leg['edge_id'] == edge
        assert leg['coordinates'][0] == origin
        assert leg['speed_kph'] > 0


def test_coincident_unconnected_nodes_are_not_joined():
    roads = network({'base': (1, 7), 'cross-a': (5, 7), 'cross-b': (5, 7), 'end': (11, 7)},
                    [('first', 'base', 'cross-a'), ('second', 'cross-b', 'end')])
    planner = router(roads)
    assert planner.options(center(1, 7), [11, 7], burning=set(), wind=[0, 0])['status'] == 'unreachable'
    assert planner.options(center(5, 7), [11, 7], burning=set(), wind=[0, 0])['status'] == 'unmapped'


def test_allowed_modes_apply_to_graph_and_mid_edge_origins():
    roads = network({'base': (1, 7), 'end': (11, 7)}, [('restricted', 'base', 'end')])
    document = roads.document
    document['edges'][0]['allowed_modes'] = ['crew']
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})
    planner = router(RoadNetwork(document, document['sha256']))
    engine = planner.options(center(1, 7), [11, 7], burning=set(), wind=[0, 0])
    crew = planner.options(center(1, 7), [11, 7], mode='crew', burning=set(), wind=[0, 0])
    assert engine['status'] == 'blocked'
    assert crew['status'] == 'reachable'
    assert crew['options'][0]['journey']['legs'][0]['edge_id'] == 'restricted'
    assert planner.options(center(5, 7), [11, 7], burning=set(), wind=[0, 0])['status'] == 'blocked'
    assert planner.safe_journey_segment(crew['options'][0]['journey'], 0, set(), [0, 0], mode='crew')
    assert not planner.safe_journey_segment(crew['options'][0]['journey'], 0, set(), [0, 0], mode='engine')
    document['edges'][0]['allowed_modes'] = None
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        router(RoadNetwork(document, document['sha256']))


def test_excluded_cells_change_routes_and_cache_and_segment_checks():
    roads = network({'base': (0, 7), 'upper': (4, 5), 'lower': (4, 9), 'end': (10, 7)},
                    [('upper-a', 'base', 'upper'), ('upper-b', 'upper', 'end'),
                     ('lower-a', 'base', 'lower'), ('lower-b', 'lower', 'end')])
    planner = router(roads)
    upper_only = planner.options(center(0, 7), [10, 7], burning=set(), wind=[0, 0],
                                 excluded_cells={(4, 9)})
    lower_only = planner.options(center(0, 7), [10, 7], burning=set(), wind=[0, 0],
                                 excluded_cells={(4, 5)})
    for result, first_edge in ((upper_only, 'upper-a'), (lower_only, 'lower-a')):
        assert result['status'] == 'reachable'
        assert result['options'][0]['journey']['legs'][0]['edge_id'] == first_edge
    assert upper_only['options'][0]['id'] != lower_only['options'][0]['id']
    assert not planner.safe_position(center(4, 5), set(), [0, 0], excluded_cells={(4, 5)})
    journey = upper_only['options'][0]['journey']
    assert not planner.safe_journey_segment(journey, journey['legs'][0]['duration_minutes'] - .1,
                                            set(), [0, 0], excluded_cells={(4, 5)})
    assert planner.options(center(0, 7), [10, 7], burning=set(), wind=[0, 0],
                           excluded_cells={(4, 5), (4, 9)})['status'] == 'blocked'


def test_allowed_modes_select_different_directed_alternatives():
    roads = network({'base': (0, 7), 'upper': (4, 5), 'lower': (4, 9), 'end': (10, 7)},
                    [('upper-a', 'base', 'upper'), ('upper-b', 'upper', 'end'),
                     ('lower-a', 'base', 'lower'), ('lower-b', 'lower', 'end')])
    document = roads.document
    for edge in document['edges']:
        edge['allowed_modes'] = ['crew'] if edge['id'].startswith('upper') else ['engine']
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})
    planner = router(RoadNetwork(document, document['sha256']))
    for mode, expected in (('engine', 'lower-a'), ('crew', 'upper-a')):
        result = planner.options(center(0, 7), [10, 7], mode=mode, burning=set(), wind=[0, 0],
                                 target_metric=center(10, 7))
        assert result['status'] == 'reachable'
        assert result['options'][0]['journey']['legs'][0]['edge_id'] == expected


def test_offroad_crew_reaches_graph_by_walk_without_snapping():
    roads = network({'base': (1, 7), 'access': (6, 7)}, [('return', 'access', 'base')])
    planner = router(roads)
    origin = [center(8, 7)[0] + 20, center(8, 7)[1] + 10]
    result = planner.options(origin, [1, 7], mode='crew', burning=set(), wind=[0, 0],
                             excluded_cells={(7, 7)}, target_metric=center(1, 7))
    assert result['status'] == 'reachable'
    journey = result['options'][0]['journey']
    assert journey['origin'] == journey['legs'][0]['coordinates'][0] == origin
    assert journey['destination'] == center(1, 7)
    assert journey['legs'][0]['mode'] == 'walk'
    assert any(leg['edge_id'] == 'return' for leg in journey['legs'])
    assert all((7, 7) not in planner._segment_cells(*leg['coordinates'])
               for leg in journey['legs'] if leg['mode'] == 'walk')
    assert planner.options(origin, [1, 7], burning=set(), wind=[0, 0])['status'] == 'unmapped'


def test_exact_destination_and_unknown_fuel_mask():
    roads = network({'base': (1, 7), 'access': (6, 7)}, [('road', 'base', 'access')])
    planner = router(roads)
    destination = [center(8, 7)[0] + 10, center(8, 7)[1] + 20]
    result = planner.options(center(1, 7), [8, 7], mode='crew', burning=set(), wind=[0, 0],
                             target_metric=destination)
    assert result['status'] == 'reachable'
    assert all(option['journey']['destination'] == destination for option in result['options'])
    assert result['options'][0]['journey']['legs'][-1]['coordinates'][-1] == destination
    with pytest.raises(ValueError):
        planner.options(center(1, 7), [8, 7], mode='crew', burning=set(), wind=[0, 0],
                        target_metric=center(9, 7))
    inputs = dict(planner.inputs, fuel_known_mask=[[1] * 15 for _ in range(15)])
    inputs['fuel_known_mask'][7][8] = 0
    unknown = HazardRouter(roads, inputs, planner.heights, walk_mask=[[1] * 15 for _ in range(15)])
    assert not unknown.safe_position(destination, set(), [0, 0], mode='crew')
    assert unknown.options(center(1, 7), [8, 7], mode='crew', burning=set(), wind=[0, 0],
                           target_metric=destination)['status'] == 'unmapped'
