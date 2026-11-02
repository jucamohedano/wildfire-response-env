"""Local, additive source-map development exercises."""
import json
import os
import math
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.real_map_scenarios import (
    NETWORK_PIN, PACK_PIN, POST_NODES, SECTOR, REDESIGNED_EGRESS_EDGES, _supported_journey, _warm_handover,
)
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.scenarios import (
    ALL_CASE_IDS, FAMILIES, REAL_MAP_DEVELOPMENT_CASE_IDS, REAL_MAP_V2_CASE_IDS,
    REAL_MAP_DEVELOPMENT_V1_CASE_IDS, REAL_MAP_DEVELOPMENT_REDESIGNED_CASE_IDS,
    REAL_MAP_DEVELOPMENT_RECOVERY_CASE_IDS,
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
    catalog = next(item for item in registry()['catalogs'] if item['id'] == 'incident_day_realmap_development_v3')
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
            index = REAL_MAP_DEVELOPMENT_CASE_IDS.index(case['id'])
            parent_ids = (REAL_MAP_DEVELOPMENT_REDESIGNED_CASE_IDS
                          if case['id'] in REAL_MAP_DEVELOPMENT_RECOVERY_CASE_IDS
                          else REAL_MAP_DEVELOPMENT_V1_CASE_IDS)
            expected_parent = build_scenario(inputs, network, parent_ids[index])
            assert case['parent_scenario_sha256'] == expected_parent['sha256']
            assert case['seed'] == parent['seed']
            assert case['initial_fire'] == parent['initial_fire']
            extension = REDESIGNED_EGRESS_EDGES if case['id'] in REAL_MAP_DEVELOPMENT_CASE_IDS[5:7] else set()
            assert set(case['profile']['allowed_road_edge_ids']) == set(parent['profile']['allowed_road_edge_ids']) | extension
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


@pytest.mark.parametrize('index,recorded_sha', [
    (5, '12a7b96c99b2945a339d34490f9fae418dc961837e4c4afef5d100a5011fcfca'),
    (6, 'f2912684718969f4f2bed67b0888ae9938a50664abe955cc1c6165c58dbbaefc'),
])
def test_redesigned_post_preserves_archived_parent_and_family(cases, index, recorded_sha):
    from envs.wildfire_response.incident_day.real_map_scenarios import REDESIGNED_POST_NODE
    inputs, network, _ = cases
    old_id = REAL_MAP_DEVELOPMENT_V1_CASE_IDS[index]
    new_id = REAL_MAP_DEVELOPMENT_CASE_IDS[index]
    assert new_id in REAL_MAP_DEVELOPMENT_REDESIGNED_CASE_IDS
    old, new = build_scenario(inputs, network, old_id), build_scenario(inputs, network, new_id)
    assert old['sha256'] == recorded_sha
    assert new['parent_scenario_sha256'] == old['sha256'] != new['sha256']
    assert old['events'] == new['events'] and old['family'] == new['family']
    assert old['seed'] == new['seed'] and old['initial_fire'] == new['initial_fire']
    assert old['destinations'] == new['destinations'] and old['groups'] == new['groups']
    assert set(new['profile']['allowed_road_edge_ids']) == set(old['profile']['allowed_road_edge_ids']) | REDESIGNED_EGRESS_EDGES
    post = network.nodes[REDESIGNED_POST_NODE]
    for unit in new['resources']:
        parent = next(item for item in old['resources'] if item['id'] == unit['id'])
        if unit['kind'] in ('engine', 'crew'):
            assert unit['position'] == unit['base_position'] == post
            expected = dict(parent, position=post, base_position=post)
            assert unit == expected
        else:
            assert unit == parent
    assert all(new['profile']['facilities'][center]['position'] == post for center in ('center-0', 'center-1'))
    assert all(item['node_id'] == REDESIGNED_POST_NODE for item in new['temporary_posts'])
    assert select_day_case_id(new_id, PACK_PIN, NETWORK_PIN) == new_id
    with pytest.raises(ValueError, match='Archived'):
        select_day_case_id(old_id, PACK_PIN, NETWORK_PIN)
    assert select_day_case_id(old_id, PACK_PIN, NETWORK_PIN, allow_archived=True) == old_id

    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'],
                          allowed_edge_ids=new['profile']['allowed_road_edge_ids'])
    burning = _warm_handover(inputs, new['initial_fire'], new['seed'], new['wind'])
    hazards = router._hazards(burning, new['wind'])
    assert router._safe_cell(router._cell(post), hazards, 'crew')
    assert math.dist(router._cell(post), SECTOR) > 5
    for mode in ('engine', 'crew'):
        outward = router.options(post, list(SECTOR), mode=mode, burning=burning, wind=new['wind'], limit=1)['options'][0]
        back = router.options(outward['approach_point'], router._cell(post), mode=mode, burning=burning,
                              wind=new['wind'], target_metric=post, limit=1)['options'][0]
        assert _supported_journey(router, outward['journey'], hazards, router.allowed_edge_ids)
        assert _supported_journey(router, back['journey'], hazards, router.allowed_edge_ids)
        assert outward['travel_minutes'] + back['travel_minutes'] + 7 < 480


def test_all_archived_v1_hashes_match_the_recorded_release(cases):
    inputs, network, _ = cases
    recorded = (
        'ed758be8182244fc02abcb428585b80d08aeaf5fbf44567db35e960f99ee3ad4',
        '899a38f2df6ffac3e707bc206a38f992137a7a9594d14d7212c04db94b54918e',
        'd8fcf5e9e553a15f66fc9cef1d0bab134d81c3982a2849e98ea24956e8e41fb6',
        '0ea8af2f5ebb2a002a055078372227d96443b998e193cd90be156096f8cc3346',
        '9869f97a35bbbee468da4e04b86c6459eff781dfa2468df640f01872698d9c9e',
        '12a7b96c99b2945a339d34490f9fae418dc961837e4c4afef5d100a5011fcfca',
        'f2912684718969f4f2bed67b0888ae9938a50664abe955cc1c6165c58dbbaefc',
        '590d4126de92463f165aaacda0d2a211ce60bd1e47d17641c5da229f0f7c89b4',
        '154f7f7427054813eb25b6051cfb5e59e33b342fca02941abdd50a3f41e9b5c6',
        'f74afbc266afb5eec335079ea156d5ad26cee933e6e9b530830a5966279a2c5b',
        '045688fd24c36dc9949ba9dbd6b706714183067e00ca4c56114474c1cd98f668',
        'd84a920141ba0de549d2877002811b6ce02dcbcc823ec525a51cff4019885331',
    )
    for identifier, expected in zip(REAL_MAP_DEVELOPMENT_V1_CASE_IDS, recorded):
        assert build_scenario(inputs, network, identifier)['sha256'] == expected
        with pytest.raises(ValueError, match='Archived'):
            select_day_case_id(identifier, PACK_PIN, NETWORK_PIN)
        assert select_day_case_id(identifier, PACK_PIN, NETWORK_PIN, allow_archived=True) == identifier


@pytest.mark.parametrize('index', [5, 6])
def test_relocated_post_supports_actual_ground_work_and_refill(cases, index):
    import copy
    inputs, network, scenarios = cases
    env = IncidentDayEnv(inputs, network.document, expected_inputs_sha256=PACK_PIN,
                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    env.reset(scenarios[index]['id'])
    for mode in ('engine', 'crew'):
        unit = copy.deepcopy(next(u for u in env._scenario['resources'] if u['id'] == mode + '-0'))
        options = env.router.options(unit['base_position'], list(SECTOR), mode=mode,
            burning=env._burning(), wind=list(env.fire.wind), limit=32)['options']
        assert options
        unit.update(position=options[0]['approach_point'][:], target_grid=list(SECTOR), state='working')
        assert env._suppress(unit), f'{mode} approach cannot reach burning ground'
        assert env._work_log[-1]['resource_id'] == unit['id']
    for uid in ('engine-0', 'engine-1'):
        ops = Operations([copy.deepcopy(env.ops.resources[uid])], [], [], copy.deepcopy(env._scenario['profile']))
        unit = ops.resources[uid]
        before = ops.profile['facilities'][unit['facility_id']]['water_litres']
        needed = unit['capacity_litres'] - unit['water_litres']
        assert needed > 0
        ops._at_base(unit, 0)
        assert unit['state'] == 'refilling'
        assert ops.profile['facilities'][unit['facility_id']]['water_litres'] == before - needed
        due = unit['due_at']
        ops.tick(due, wind=[0, 0], daylight=True, hazard_check=lambda *args: True,
                     suppress=lambda *args: None, emit=lambda *args, **kwargs: None)
        assert unit['water_litres'] == unit['capacity_litres']
        assert unit['state'] == 'available'


@pytest.mark.parametrize('index,parent_sha,recovery_minute,morning_minute', [
    (2, 'fcd97e48c4a4c43ece62af7cd22b3cc898d0d7d1b71037b43338664aede9a168', 720, 20),
    (3, '7fcbb3eef30b044aa4316ec75d67da70a5a9b8068e3f6d8c291e4904d606ade8', 750, 45),
])
def test_v3_wind_exercises_preserve_archived_parent_and_only_redesign_afternoon(
        cases, index, parent_sha, recovery_minute, morning_minute):
    import copy
    from envs.wildfire_response.incident_day.real_map_acceptance import wind_access_event

    inputs, network, _ = cases
    old_id = REAL_MAP_DEVELOPMENT_REDESIGNED_CASE_IDS[index]
    new_id = REAL_MAP_DEVELOPMENT_CASE_IDS[index]
    assert new_id == f'incident-day-realmap-development-v3-{index:03}'
    old = build_scenario(inputs, network, old_id)
    new = build_scenario(inputs, network, new_id)
    assert old['sha256'] == parent_sha
    assert new['parent_scenario_sha256'] == parent_sha
    assert new['sha256'] != parent_sha
    assert new['version'] == 'wildfire-incident-day-realmap-development-scenarios-v3'
    assert wind_access_event(old) == wind_access_event(new) == {
        'minute': morning_minute, 'kind': 'wind', 'wind': [0., 2.]}
    original_recovery = next(event for event in old['events'] if event['minute'] == 1020)
    assert original_recovery == {'minute': 1020, 'kind': 'wind', 'wind': [0., -1.]}
    expected_events = copy.deepcopy(old['events'])
    next(event for event in expected_events if event['minute'] == 1020)['minute'] = recovery_minute
    expected_events.sort(key=lambda event: event['minute'])
    assert new['events'] == expected_events
    design = new['synthetic_recovery_design']
    assert design['version'] == 'afternoon-wind-window-v1'
    assert design['original_minute'] == 1020 and design['replacement_minute'] == recovery_minute
    assert 'not observed weather' in design['assumption']
    changed = {'id', 'version', 'parent_scenario_sha256', 'events', 'synthetic_recovery_design', 'sha256'}
    assert {key: value for key, value in old.items() if key not in changed} == {
        key: value for key, value in new.items() if key not in changed}
    assert old_id in ARCHIVED_DAY_CASE_IDS and new_id not in ARCHIVED_DAY_CASE_IDS
    with pytest.raises(ValueError, match='Archived'):
        select_day_case_id(old_id, PACK_PIN, NETWORK_PIN)
    assert select_day_case_id(old_id, PACK_PIN, NETWORK_PIN, allow_archived=True) == old_id
    assert select_day_case_id(new_id, PACK_PIN, NETWORK_PIN) == new_id
    with pytest.raises(ValueError, match='matching input'):
        select_day_case_id(new_id, '0' * 64, NETWORK_PIN, allow_archived=True)
