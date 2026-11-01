"""Local, additive source-map development exercises."""
import json
import os
import math
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.real_map_scenarios import (
    NETWORK_PIN, PACK_PIN, POST_NODES, SECTOR, _supported_journey, _warm_handover,
)
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.scenarios import (
    ALL_CASE_IDS, FAMILIES, REAL_MAP_DEVELOPMENT_CASE_IDS, REAL_MAP_V2_CASE_IDS,
    build_scenario,
)
from wildfire_core.catalog_lifecycle import (
    ACTIVE_REAL_MAP_CASE_IDS, ACTIVE_REAL_MAP_DEVELOPMENT_CASE_IDS,
    ARCHIVED_DAY_CASE_IDS, default_day_case_id, registry, select_day_case_id,
)
from wildfire_core.mobility_routing import RoadNetwork
from wildfire_core.incident_day import IncidentDayEnv


@pytest.fixture(scope='module')
def cases():
    path = Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME', '.runtime/wildfire-response-spain'))
    inputs_path = path / 'runtime/es-ma-2021/inputs.json'
    network_path = path / 'runtime/es-ma-2021/network.json'
    if not inputs_path.exists() or not network_path.exists():
        pytest.skip('Local pinned source-map runtime unavailable')
    inputs = json.loads(inputs_path.read_text())
    document = json.loads(network_path.read_text())
    network = RoadNetwork(document, NETWORK_PIN)
    scenarios = [build_scenario(inputs, network, identifier) for identifier in REAL_MAP_DEVELOPMENT_CASE_IDS]
    return inputs, network, scenarios


def test_lifecycle_is_additive_and_pinned():
    assert len(REAL_MAP_DEVELOPMENT_CASE_IDS) == 12
    assert len(set(ALL_CASE_IDS)) == len(ALL_CASE_IDS)
    assert ACTIVE_REAL_MAP_DEVELOPMENT_CASE_IDS == REAL_MAP_DEVELOPMENT_CASE_IDS
    assert not set(REAL_MAP_DEVELOPMENT_CASE_IDS) & set(ARCHIVED_DAY_CASE_IDS)
    assert default_day_case_id(PACK_PIN, NETWORK_PIN) == ACTIVE_REAL_MAP_CASE_IDS[0]
    catalog = next(item for item in registry()['catalogs'] if item['id'] == 'incident_day_realmap_development_v1')
    assert catalog['case_ids'] == list(REAL_MAP_DEVELOPMENT_CASE_IDS)
    for identifier in REAL_MAP_DEVELOPMENT_CASE_IDS:
        assert select_day_case_id(identifier, PACK_PIN, NETWORK_PIN) == identifier
        with pytest.raises(ValueError, match='matching input'):
            select_day_case_id(identifier, '0' * 64, NETWORK_PIN, allow_archived=True)


def test_variants_inherit_supported_parent_geography_and_differ_materially(cases):
    inputs, network, scenarios = cases
    assert len({case['sha256'] for case in scenarios}) == 12
    for family, name in enumerate(FAMILIES):
        parent = build_scenario(inputs, network, REAL_MAP_V2_CASE_IDS[family])
        first, second = scenarios[family * 2:family * 2 + 2]
        assert first['family'] == second['family'] == name
        for case in (first, second):
            assert case['parent_scenario_sha256'] == parent['sha256']
            assert case['seed'] == parent['seed']
            assert case['initial_fire'] == parent['initial_fire']
            assert case['profile']['allowed_road_edge_ids'] == parent['profile']['allowed_road_edge_ids']
            assert case['source_context'] == parent['source_context']
            with pytest.raises(ValueError, match='seeds are pinned'):
                build_scenario(inputs, network, case['id'], seed=-1)
        assert (first['events'], first['resources'], first['groups']) != (second['events'], second['resources'], second['groups'])
        if family == 4:
            for case in (first, second):
                closure = next(event for event in case['events'] if event['kind'] == 'destination_closed')
                occupant = next(group for group in case['groups'] if group['id'] == 'population-1')
                assert occupant['destination_id'] == closure['destination_id']
                assert occupant['status'] == 'arrived'


def test_each_case_resets_and_has_supported_ground_approach_and_return(cases):
    inputs, network, scenarios = cases
    document = json.loads((Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME', '.runtime/wildfire-response-spain')) /
                           'runtime/es-ma-2021/network.json').read_text())
    env = IncidentDayEnv(inputs, document, expected_inputs_sha256=PACK_PIN,
                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'])
    for case in scenarios:
        observation, _ = env.reset(case['id'])
        assert observation['task_id'] == case['id']
        burning = _warm_handover(inputs, case['initial_fire'], case['seed'], case['wind'])
        hazards = router._hazards(burning, case['wind'])
        allowed = frozenset(case['profile']['allowed_road_edge_ids'])
        for post in POST_NODES:
            base = network.nodes[post]
            for mode in ('engine', 'crew'):
                options = router.options(base, list(SECTOR), mode=mode, burning=burning,
                                         wind=case['wind'], limit=32)['options']
                assert options
                outward = options[0]
                back = router.options(outward['approach_point'], router._cell(base), mode=mode,
                                      burning=burning, wind=case['wind'], target_metric=base, limit=1)['options']
                assert back
                assert _supported_journey(router, outward['journey'], hazards, allowed)
                assert _supported_journey(router, back[0]['journey'], hazards, allowed)


@pytest.mark.parametrize('index,available_at', [(10, 180), (11, 360)])
def test_shortage_engine_can_dispatch_and_return_within_its_roster_slot(cases, index, available_at):
    inputs, network, scenarios = cases
    case = scenarios[index]
    unit = next(unit for unit in case['resources'] if unit['id'] == 'engine-1')
    replacement = next(unit for unit in case['resources'] if unit['id'] == 'engine-1-shift-2')
    assert unit['state'] == 'unavailable' and unit['available_at'] == available_at
    assert unit['duty_end_at'] == replacement['available_at'] == 480
    assert replacement['state'] == 'unavailable' and replacement['duty_end_at'] == 960
    assert unit['work_remaining'] > 0 and unit['total_work_remaining'] > 0

    burning = _warm_handover(inputs, case['initial_fire'], case['seed'], case['wind'])
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'])
    base = unit['base_position']
    outward = router.options(base, list(SECTOR), mode='engine', burning=burning,
                             wind=case['wind'], limit=32)['options'][0]['journey']
    back = router.options(outward['destination'], router._cell(base), mode='engine', burning=burning,
                          wind=case['wind'], target_metric=base, limit=1)['options'][0]['journey']
    hazards = router._hazards(burning, case['wind'])
    allowed = frozenset(case['profile']['allowed_road_edge_ids'])
    assert _supported_journey(router, outward, hazards, allowed)
    assert _supported_journey(router, back, hazards, allowed)
    needed = (1 + outward['duration_minutes'] + case['profile']['ground_setup_minutes']
              + 1 + math.ceil(back['duration_minutes']))
    assert available_at + needed < unit['duty_end_at']

    ops = Operations([unit], [], [], case['profile'], return_planner=lambda resource, minute: back)
    def tick(minute):
        ops.tick(minute, wind=case['wind'], daylight=True, hazard_check=lambda *args: True,
                 suppress=lambda *args: None, emit=lambda *args, **kwargs: None)
    tick(available_at - 1)
    assert not ops.dispatch(unit['id'], 'incident', list(SECTOR), outward, available_at - 1)
    tick(available_at)
    assert ops.resources[unit['id']]['state'] == 'available'
    assert ops.dispatch(unit['id'], 'incident', list(SECTOR), outward, available_at)
    assert ops.resources[unit['id']]['state'] == 'mobilizing'


def test_destination_variants_keep_population_and_reception_occupancy_coherent(cases):
    _, _, scenarios = cases
    for case in scenarios[8:10]:
        group = next(group for group in case['groups'] if group['id'] == 'population-1')
        destination = next(item for item in case['destinations'] if item['id'] == group['destination_id'])
        handover = case['population_handover']
        closure = next(event for event in case['events'] if event['kind'] == 'destination_closed')
        assert group['position'] == destination['position']
        assert group['count'] <= destination['capacity']
        assert handover['group_id'] == group['id'] and handover['count'] == group['count']
        assert handover['destination_id'] == closure['destination_id'] == destination['id']
        assert case['profile']['closure_affects_occupants']
