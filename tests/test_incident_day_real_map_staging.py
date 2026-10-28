import gzip
import hashlib
import json
import math
import copy
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.real_map_scenarios import (
    ALLOWED_ROAD_EDGES, WIND_ROAD_EDGES, HOME_NODES, NETWORK_PIN, PACK_PIN, POST_NODES, SECTOR, SNAPSHOT_PIN,
    build_real_map_scenario)
from envs.wildfire_response.incident_day.scenarios import REAL_MAP_CASE_IDS
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_core.mobility_routing import RoadNetwork


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def real_inputs():
    pack_path = ROOT / '.runtime/estimated-incident-day-inputs-final.json'
    if not pack_path.exists():
        pytest.skip('The separately prepared and pinned estimated exercise pack is not present')
    inputs = json.loads(pack_path.read_text())
    document = json.loads((ROOT / 'data/coastal_mobility_v1/network.json').read_text())
    assert inputs['sha256'] == PACK_PIN and document['sha256'] == NETWORK_PIN
    return inputs, document, RoadNetwork(document, NETWORK_PIN)


def test_pinned_road_segments_avoid_retained_restrictions_and_barriers(real_inputs):
    unused_inputs, document, unused_network = real_inputs
    raw = (ROOT / 'data/coastal_mobility_v1/source_snapshot.json.gz').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_PIN
    source = json.loads(gzip.decompress(raw))
    nodes = {element['id']: element for element in source['elements'] if element['type'] == 'node'}
    ways = {element['id']: element for element in source['elements'] if element['type'] == 'way'}
    restrictions = [element for element in source['elements'] if element['type'] == 'relation']
    restricted_ways = {member['ref'] for relation in restrictions for member in relation['members']
                       if member['type'] == 'way'}
    restricted_nodes = {member['ref'] for relation in restrictions for member in relation['members']
                        if member['type'] == 'node'}
    graph_edges = {edge['id']: edge for edge in document['edges']}
    for identifier in WIND_ROAD_EDGES:
        edge = graph_edges[identifier]
        way_id = int(identifier.split(':')[1][1:])
        assert way_id not in restricted_ways
        start = int(edge['source'].split('n', 1)[1])
        end = int(edge['target'].split('n', 1)[1])
        assert start not in restricted_nodes and end not in restricted_nodes
        way_nodes = ways[way_id]['nodes']
        first, last = sorted((way_nodes.index(start), way_nodes.index(end)))
        for node_id in way_nodes[first:last + 1]:
            tags = nodes[node_id]['tags']
            assert node_id not in restricted_nodes
            assert tags.get('barrier') in (None, 'no')
            assert tags.get('ford', 'no') == 'no'
            assert tags.get('access') in (None, 'yes', 'permissive', 'designated', 'official')


def test_builder_rejects_tampered_pack_and_unpinned_seed(real_inputs):
    inputs, unused_document, network = real_inputs
    tampered = copy.deepcopy(inputs)
    tampered['fuel_known_mask'][26][14] = 0
    with pytest.raises(ValueError, match='hash'):
        build_real_map_scenario(tampered, network, REAL_MAP_CASE_IDS[0])
    with pytest.raises(ValueError, match='seeds are pinned'):
        build_real_map_scenario(inputs, network, REAL_MAP_CASE_IDS[0], seed=1)


@pytest.mark.parametrize('identifier', REAL_MAP_CASE_IDS)
def test_additive_scenarios_have_known_large_fire_and_finite_mapped_posts(real_inputs, identifier):
    inputs, document, network = real_inputs
    scenario = build_real_map_scenario(inputs, network, identifier)
    assert scenario['sector_grid'] == list(SECTOR)
    assert len(scenario['initial_fire']) == 25
    assert all(inputs['fuel_known_mask'][row][column] for column, row in scenario['initial_fire'])
    assert not scenario['historical_replay']
    destinations = [item['position'] for item in scenario['destinations']]
    assert len({tuple(network.nodes[node]) for node in HOME_NODES}) == len(HOME_NODES)
    for index, group in enumerate(scenario['groups']):
        assert group['home'] == network.nodes[HOME_NODES[index]]
        assert group['home'] not in destinations
        if identifier == REAL_MAP_CASE_IDS[4] and index == 1:
            assert group['status'] == 'arrived' and group['destination_id'] == 'destination-0'
            assert group['position'] == destinations[0]
            assert scenario['profile']['causal_coverage']
            assert scenario['profile']['closure_affects_occupants']
        else:
            assert group['position'] == group['home'] and group['destination_id'] is None
            assert group['position'] not in destinations
    for index, node in enumerate(POST_NODES):
        post = network.nodes[node]
        grid = (int((post[0] - inputs['grid']['transform'][2]) // 500),
                int((post[1] - inputs['grid']['transform'][5]) // -500))
        assert math.dist(grid, SECTOR) > 5
        assert scenario['profile']['facilities'][f'center-{index}']['position'] == post
        assert scenario['destinations'][index]['position'] == post
        for kind in ('engine', 'crew'):
            unit = next(item for item in scenario['resources'] if item['id'] == f'{kind}-{index}')
            assert unit['position'] == unit['base_position'] == post
            assert unit['facility_id'] == f'center-{index}'
            if kind == 'crew':
                assert unit['state'] == 'available' and unit['available_at'] == 0
    environment = IncidentDayEnv(inputs, document, expected_inputs_sha256=PACK_PIN,
                                 expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    environment.reset(identifier)
    assert environment._prehistory['footprint_hectares'] > 500
    assert not environment._land_guard()
    assert environment._sector() == list(SECTOR)


def test_crew_travels_sets_up_works_and_returns_to_declared_post(real_inputs):
    inputs, document, unused_network = real_inputs
    environment = IncidentDayEnv(inputs, document, expected_inputs_sha256=PACK_PIN,
                                 expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    observation, unused = environment.reset(REAL_MAP_CASE_IDS[0])
    dispatcher = DayDispatcher()
    worker = None
    while environment.minute < 160 and not (environment.terminated or environment.truncated):
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
            dispatcher.action(environment, observation))
        worker = next((item['resource_id'] for item in environment._work_log
                       if item['resource_id'].startswith('crew-')), None)
        if worker:
            break
    assert worker is not None and not environment.truncated
    states = [item['resources'][worker]['state'] for item in environment._audit]
    assert states[0] == 'available'
    assert 'travel' in states and 'on_scene' in states and 'working' in states
    assert states.index('travel') < states.index('on_scene') < states.index('working')
    unit = environment.ops.resources[worker]
    assert unit['position'] != unit['base_position']
    assert environment.minute < unit['duty_end_at']
    observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
        {'releases': [worker]})
    while environment.minute < 200 and not (environment.terminated or environment.truncated):
        unit = environment.ops.resources[worker]
        if unit['position'] == unit['base_position'] and unit['state'] in ('available', 'resting'):
            break
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
            {'advance_minutes': 20})
    unit = environment.ops.resources[worker]
    assert not environment.truncated
    assert unit['position'] == unit['base_position'] and unit['state'] in ('available', 'resting')
    assert environment.minute < unit['duty_end_at']
    assert unit['facility_id'] == unit['home_base']
