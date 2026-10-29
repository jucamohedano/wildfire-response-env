import copy
import json
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.plausibility import counterfactual_case
from envs.wildfire_response.incident_day.real_map_acceptance import wind_event_coverage
from envs.wildfire_response.incident_day.real_map_scenarios import (
    ALLOWED_ROAD_EDGES, WIND_ROAD_EDGES, NETWORK_PIN, PACK_PIN,
    _warm_handover, build_real_map_scenario, build_real_map_v2_scenario)
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.scenario_coverage import event_coverage
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_core.mobility_routing import RoadNetwork


ROOT = Path(__file__).resolve().parents[1]
IDS = tuple(f'incident-day-realmap-v2-{index:03}' for index in range(6))
V1_SCENARIO_PINS = (
    'a9ea0dd9a2ad2c048ea15e5acb4632e7dd255f40b0f496ce5f46c0489e0b14ea',
    '81a060a459a51521c310d605dcd257d9609861f4842f2277a827e8e7e5da3cdf',
    'ae903a9e403b945654763cbbc1ba54232d57b4980d5ac4bef51007a1cfcf2d70',
    '5a6a4d4dfcc7240ab01e87491727777950aca332118cb21b7c32a742a82ce225',
    'f8d6b1405971994106e11464faea4cc1c0df8ce49c8198825e1610080cf2d06c',
    'de4165f816a4b7be6cb99fc2efb3d7ae83ab2475c1e0fdf50f07d5a89370921b',
)


@pytest.fixture(scope='module')
def real_inputs():
    path = ROOT / '.runtime/estimated-incident-day-inputs-final.json'
    if not path.exists():
        pytest.skip('Pinned estimated exercise pack is unavailable')
    inputs = json.loads(path.read_text())
    document = json.loads((ROOT / 'data/coastal_mobility_v1/network.json').read_text())
    return inputs, document, RoadNetwork(document, NETWORK_PIN)


@pytest.fixture
def v2_environment(real_inputs):
    inputs, document, unused_network = real_inputs
    return IncidentDayEnv(inputs, document, expected_inputs_sha256=PACK_PIN,
                          expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)


def test_v2_is_additive_and_preserves_v1_scenario_identity(real_inputs):
    inputs, unused_document, network = real_inputs
    for index, identifier in enumerate(IDS):
        v1 = build_real_map_scenario(inputs, network, f'incident-day-realmap-v1-{index:03}')
        assert v1['sha256'] == V1_SCENARIO_PINS[index]
        before = copy.deepcopy(v1)
        variant = build_real_map_v2_scenario(inputs, network, identifier)
        assert v1 == before
        assert variant['parent_scenario_sha256'] == v1['sha256']
        assert variant['sha256'] != v1['sha256']
        assert 'allowed_road_edge_ids' not in v1['profile']
        assert variant['profile']['allowed_road_edge_ids'] == sorted(
            WIND_ROAD_EDGES if index == 1 else ALLOWED_ROAD_EDGES)
        assert build_real_map_scenario(inputs, network, v1['id']) == before
    assert build_real_map_v2_scenario(inputs, network, IDS[2])['profile']['causal_coverage']


def test_quiet_handover_requires_actual_work_and_return(v2_environment):
    v2_environment.reset(IDS[0])
    proof = counterfactual_case(v2_environment)
    assert proof['status'] == 'demonstrated'
    assert proof['details']['work_minute'] < proof['details']['return_minute'] < 200


def test_wind_handover_does_not_disclose_future_event(v2_environment):
    observation, unused = v2_environment.reset(IDS[1])
    situation = v2_environment.get_situation()
    assert observation == situation
    assert situation['wind'] == [1., 0.]
    assert all(report['kind'] != 'wind_update' for report in situation['reports'])
    text = json.dumps(situation, sort_keys=True).lower()
    assert 'early wind' not in text
    assert 'wind change' not in text
    assert 'realmap_v2_wind_coverage' not in text
    assert 'initial_fire' not in situation and 'events' not in situation
    def assert_no_future_wind(value):
        if isinstance(value, dict):
            assert value.get('wind') != [0., 2.]
            assert not (value.get('kind') == 'wind' and value.get('minute') == 15)
            for child in value.values():
                assert_no_future_wind(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_future_wind(child)
    assert_no_future_wind(situation)


def test_occupied_centre_has_scoped_route_to_other_centre(real_inputs):
    inputs, unused_document, network = real_inputs
    scenario = build_real_map_v2_scenario(inputs, network, IDS[4])
    assert scenario['profile']['closure_affects_occupants']
    group = next(item for item in scenario['groups'] if item['id'] == 'population-1')
    closed, alternative = scenario['destinations']
    assert group['destination_id'] == closed['id'] and group['position'] == closed['position']
    burning = _warm_handover(inputs, scenario['initial_fire'], scenario['seed'], scenario['wind'])
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['fuel_known_mask'],
                          allowed_edge_ids=scenario['profile']['allowed_road_edge_ids'])
    result = router.options(group['position'], router._cell(alternative['position']), mode='crew',
                            burning=burning, wind=scenario['wind'],
                            target_metric=alternative['position'], limit=1)
    assert result['options']
    journey = result['options'][0]['journey']
    assert journey['origin'] == closed['position'] and journey['destination'] == alternative['position']
    assert all(leg['mode'] != 'road' or leg['edge_id'] in ALLOWED_ROAD_EDGES
               for leg in journey['legs'])


def test_wind_same_state_loss_warning_and_real_alternative(v2_environment):
    environment = v2_environment
    observation, unused = environment.reset(IDS[1])
    event = next(event for event in environment._scenario['events'] if event['kind'] == 'wind' and event['minute'] == 15)
    dispatcher = DayDispatcher()
    while environment.minute < 55 and not (environment.terminated or environment.truncated):
        action = dispatcher.action(environment, observation)
        if 'advance_minutes' in action:
            action = {'advance_minutes': 1}
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(action)
    assert not environment.truncated
    result = wind_event_coverage(event, environment.inputs, environment._audit, environment.bus.reports)
    assert result['status'] == 'constraint_evidence_observed'
    evidence = result['causal_evidence']
    assert evidence['warning_received_at'] > event['minute']
    assert any(item['stopped_or_returned_at'] == event['minute']
               for item in evidence['affected_actions'])
    assert any(item['redispatch'] is not None
               and item['redispatch']['authorized_at'] >= evidence['warning_received_at']
               and item['redispatch']['dispatched_at'] < item['redispatch']['travel_at']
               < item['redispatch']['alternate_reached_at']
               for item in evidence['affected_actions'])
    missing = copy.deepcopy(environment._audit)
    missing[15]['event_effects'] = []
    assert wind_event_coverage(event, environment.inputs, missing, environment.bus.reports)['status'] != 'constraint_evidence_observed'


def _wind_trace():
    inputs = {'grid': {'transform': [1, 0, 0, 0, -1, 0]}}
    event = {'kind': 'wind', 'minute': 2, 'wind': [0, 2]}
    unit = 'engine-0'
    snapshot = dict(kind='wind', minute=2, resource_access=[dict(
        resource_id=unit, state='travel', position=[1.5, -.5],
        position_before_safe=True, position_after_safe=False,
        active_destination=[1.5, -.5], active_approach_grid=[1, 0],
        active_segment_before_safe=True, active_segment_after_safe=False,
        before_approaches=[[1, 0]], after_approaches=[[2, 0]])])
    audit = [dict(minute=minute, resources={unit: dict(state='available', position=[0.5, -.5])})
             for minute in range(10)]
    audit[2]['event_effects'] = [snapshot]
    audit[2]['resources'][unit] = dict(state='blocked', position=[1.5, -.5])
    audit[7]['resources'][unit]['state'] = 'travel'
    audit[8]['resources'][unit] = dict(state='on_scene', position=[2.5, -.5])
    reports = [dict(kind='wind_update', observed_at=2, received_at=4, payload={'wind': [0, 2]}),
               dict(kind='dispatch_accepted', observed_at=6, received_at=7,
                    payload=dict(resource_id=unit, offer_id='offer', approach_id='new',
                                 authorized_at=5, approach_grid=[2, 0], approach_point=[2.5, -.5])),
               dict(kind='dispatch_accepted', observed_at=1, received_at=2,
                    payload=dict(resource_id=unit, offer_id='original', approach_id='old',
                                 authorized_at=0, approach_grid=[1, 0], approach_point=[1.5, -.5]))]
    return event, inputs, audit, reports


def test_wind_coverage_requires_same_unit_immediate_stop_and_new_authorized_route():
    event, inputs, audit, reports = _wind_trace()
    assert wind_event_coverage(event, inputs, audit, reports)['status'] == 'constraint_evidence_observed'
    later = copy.deepcopy(audit)
    later[2]['resources']['engine-0']['state'] = 'travel'
    later[3]['resources']['engine-0']['state'] = 'blocked'
    assert wind_event_coverage(event, inputs, later, reports)['status'] != 'constraint_evidence_observed'
    already_blocked = copy.deepcopy(audit)
    already_blocked[2]['event_effects'][0]['resource_access'][0]['state'] = 'blocked'
    assert wind_event_coverage(event, inputs, already_blocked, reports)['status'] != 'constraint_evidence_observed'
    moved_at_stop = copy.deepcopy(audit)
    moved_at_stop[2]['resources']['engine-0']['position'] = [1.6, -.5]
    assert wind_event_coverage(event, inputs, moved_at_stop, reports)['status'] != 'constraint_evidence_observed'
    unchanged = copy.deepcopy(reports)
    unchanged[1]['payload']['approach_grid'] = [1, 0]
    unchanged[1]['payload']['approach_point'] = [1.5, -.5]
    assert wind_event_coverage(event, inputs, audit, unchanged)['status'] != 'constraint_evidence_observed'
    early = copy.deepcopy(reports)
    early[1]['payload']['authorized_at'] = 3
    assert wind_event_coverage(event, inputs, audit, early)['status'] != 'constraint_evidence_observed'
    no_point = copy.deepcopy(reports)
    del no_point[1]['payload']['approach_point']
    assert wind_event_coverage(event, inputs, audit, no_point)['status'] != 'constraint_evidence_observed'
    reused = copy.deepcopy(reports)
    reused[1]['payload']['offer_id'] = 'original'
    assert wind_event_coverage(event, inputs, audit, reused)['status'] != 'constraint_evidence_observed'
    unrelated = copy.deepcopy(reports)
    unrelated[1]['payload']['resource_id'] = 'engine-1'
    assert wind_event_coverage(event, inputs, audit, unrelated)['status'] != 'constraint_evidence_observed'
    retained = copy.deepcopy(audit)
    retained[2]['event_effects'][0]['resource_access'][0]['after_approaches'].append([1, 0])
    assert wind_event_coverage(event, inputs, retained, reports)['status'] != 'constraint_evidence_observed'
    no_travel = copy.deepcopy(audit)
    no_travel[7]['resources']['engine-0']['state'] = 'available'
    assert wind_event_coverage(event, inputs, no_travel, reports)['status'] != 'constraint_evidence_observed'
    wrong_point = copy.deepcopy(audit)
    wrong_point[8]['resources']['engine-0']['position'] = [2.6, -.5]
    assert wind_event_coverage(event, inputs, wrong_point, reports)['status'] != 'constraint_evidence_observed'
    interrupted = copy.deepcopy(reports)
    interrupted.append(dict(kind='dispatch_accepted', observed_at=8, received_at=9,
                            payload=dict(resource_id='engine-0', offer_id='later', approach_id='later',
                                         authorized_at=7, approach_grid=[2, 0], approach_point=[2.5, -.5])))
    assert wind_event_coverage(event, inputs, audit, interrupted)['status'] != 'constraint_evidence_observed'


def test_crew_restriction_requires_delivered_refusals_and_actual_work(v2_environment):
    environment = v2_environment
    observation, unused = environment.reset(IDS[2])
    dispatcher = DayDispatcher()
    while environment.minute < 130 and not (environment.terminated or environment.truncated):
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
            dispatcher.action(environment, observation))
    event = next(event for event in environment._scenario['events'] if event['kind'] == 'access_restriction')
    result = event_coverage(event, environment._scenario, environment.inputs, environment._audit,
                            environment.bus.reports, list(environment.ops.orders.values()), environment._query_log)
    assert result['status'] == 'constraint_evidence_observed'
    assert result['causal_evidence']['public_refusals']
    assert result['causal_evidence']['crew_work_count'] > 0
    assert not result['causal_evidence']['forbidden_work']
    assert event_coverage(event, environment._scenario, environment.inputs, environment._audit,
                          environment.bus.reports, list(environment.ops.orders.values()), [])['status'] != 'constraint_evidence_observed'
