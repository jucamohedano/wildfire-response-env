"""Recovery fallback is opt-in, bounded, and subject to existing route safety."""
import math

import pytest

from envs.wildfire_response.incident_day import routing
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_core.mobility_routing import RoadNetwork


def center(x, y):
    return [x * 100 + 50, 1450 - y * 100]


def planner(mask=None):
    # The closest destination candidate is disconnected. The walking-origin
    # access node can reach the exact destination on foot but is ranked second.
    positions = {'access': center(5, 7), 'dead': center(5, 6),
                 'decoy': center(7, 7), 'decoy-end': center(9, 7)}
    edges = [('local', 'access', 'dead'), ('isolated', 'decoy', 'decoy-end')]
    document = dict(version='coastal-road-network-v1', crs='EPSG:25830',
        nodes=[dict(id=k, position=v) for k, v in positions.items()],
        edges=[dict(id=k, source=a, target=b, coordinates=[positions[a], positions[b]],
                    length_m=math.dist(positions[a], positions[b]), speed_kph=3)
               for k, a, b in edges])
    document['sha256'] = digest(document)
    network = RoadNetwork(document, document['sha256'])
    inputs = dict(grid=dict(width=15, height=15, crs='EPSG:25830', transform=[100, 0, 0, 0, -100, 1500]),
                  fuel_classes=[[1] * 15 for _ in range(15)],
                  fuel_valid_mask=[[1] * 15 for _ in range(15)], study_mask=[[1] * 15 for _ in range(15)])
    return HazardRouter(network, inputs, [[100] * 15 for _ in range(15)],
                        walk_mask=mask if mask is not None else [[1] * 15 for _ in range(15)])


def find(router, *, enabled, burning=(), excluded=(), wind=(0, 0)):
    return router.options(center(3, 7), [7, 7], mode='crew', burning=burning, wind=wind,
                          excluded_cells=excluded, target_metric=center(7, 7), limit=1,
                          emergency_egress=True, recovery_origin_candidates=enabled)


@pytest.mark.parametrize('burning,wind', [((), (0, 0)), ({(2, 7)}, (1, 0))])
def test_bounded_fallback_finds_safe_origin_candidate_and_preserves_legacy(monkeypatch, burning, wind):
    monkeypatch.setattr(routing, 'MAX_WALK_CANDIDATES', 1)
    router = planner()
    assert not find(router, enabled=False, burning=burning, wind=wind)['options']
    result = find(router, enabled=True, burning=burning, wind=wind)
    assert result['status'] == 'reachable'
    journey = result['options'][0]['journey']
    assert journey['access_node'] == 'access'
    assert journey['origin'] == center(3, 7) and journey['destination'] == center(7, 7)
    if 'emergency_egress' in journey:
        for minute in range(journey['duration_minutes']):
            checked = router.safe_journey_segment(journey, minute, burning, wind, mode='crew', egress_status=True)
            assert checked['safe']
            journey['emergency_egress']['exited'] = checked['egress_exited']
        assert journey['emergency_egress']['exited']
    else:
        hazards = router._hazards(burning, wind)
        assert all(router._safe_line(leg['coordinates'], hazards, 'crew') for leg in journey['legs'])
    # The cache must not let the opt-in result leak into the legacy call.
    assert not find(router, enabled=False, burning=burning, wind=wind)['options']


def test_fallback_does_not_cross_excluded_burning_or_unsupported_cells(monkeypatch):
    monkeypatch.setattr(routing, 'MAX_WALK_CANDIDATES', 1)
    router = planner()
    assert not find(router, enabled=True, excluded={(4, 7), (4, 6), (4, 8)})['options']
    assert not find(router, enabled=True, burning={(7, 7)})['options']
    mask = [[1] * 15 for _ in range(15)]
    mask[7][3] = 0
    assert not find(planner(mask), enabled=True)['options']


def test_recovery_flag_is_restricted_to_crew_emergency_return():
    router = planner()
    with pytest.raises(ValueError):
        router.options(center(3, 7), [7, 7], mode='crew', burning=(), wind=(0, 0),
                       target_metric=center(7, 7), recovery_origin_candidates=True)
    with pytest.raises(ValueError):
        router.options(center(3, 7), [7, 7], mode='engine', burning=(), wind=(0, 0),
                       target_metric=center(7, 7), emergency_egress=True, recovery_origin_candidates=True)


def test_old_fixture_profile_does_not_opt_in():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    assert 'recovery_candidate_policy' not in env._scenario['profile']


@pytest.fixture
def source_environment():
    import json
    import os
    from pathlib import Path
    from envs.wildfire_response.incident_day.real_map_scenarios import NETWORK_PIN, PACK_PIN

    root = Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME', '.runtime/wildfire-response-spain'))
    input_path = root / 'runtime/es-ma-2021/inputs.json'
    network_path = root / 'runtime/es-ma-2021/network.json'
    if not input_path.is_file() or not network_path.is_file():
        pytest.skip('Pinned source-map inputs unavailable')
    inputs, network = json.loads(input_path.read_text()), json.loads(network_path.read_text())
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=PACK_PIN,
                          expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)


def test_recorded_real_map_recovery_geometry_when_pinned_inputs_available(source_environment):
    env = source_environment
    env.reset('incident-day-realmap-development-v1-005')
    assert 'recovery_candidate_policy' not in env._scenario['profile']
    # Exercise the historical geometry with the candidate option explicitly:
    # this does not enable it in the frozen v1 scenario.
    burning = {(14, 26), (14, 27), (14, 28), (15, 26), (15, 27), (15, 28),
               (16, 26), (16, 27), (16, 29), (17, 26), (17, 27), (17, 28),
               (17, 29), (18, 26), (18, 27), (18, 28), (18, 29), (18, 30),
               (19, 26), (19, 28), (19, 30)}
    for uid, position in (('crew-0', [304489.3403585126, 4038250.0]),
                          ('crew-1', [304494.65674604976, 4038250.0])):
        base = env.ops.resources[uid]['base_position']
        args = dict(mode='crew', burning=burning, wind=[0., 1.], target_metric=base,
                    emergency_egress=True, limit=1)
        target = env.router._cell(base)
        assert not env.router.options(position, target, **args)['options']
        result = env.router.options(position, target, **args, recovery_origin_candidates=True)
        assert result['status'] == 'reachable'
        journey = result['options'][0]['journey']
        assert journey['access_node'] in ('osm:n946554144', 'osm:n946554100')
        hazards = env.router._hazards(burning, [0., 1.])
        for leg in journey['legs']:
            assert env.router._safe_line(leg['coordinates'], hazards,
                                         'crew' if leg['mode'] == 'walk' else 'engine')
            if leg['mode'] == 'road':
                assert leg['edge_id'] in env.router.allowed_edge_ids
    # A later run with this fix changes suppression and reaches a different
    # minute-301 state: the base itself lies in the wind-driven fire buffer.
    # Candidate fallback must not convert that genuinely blocked return into
    # an apparently safe one.
    later_burning = {(13, 31), (14, 26), (14, 27), (14, 28), (14, 29), (14, 30), (14, 31),
                     (15, 26), (15, 27), (15, 28), (15, 29), (15, 30), (16, 26), (16, 27),
                     (16, 28), (16, 29), (16, 30), (17, 26), (17, 27), (17, 28), (17, 29),
                     (17, 30), (17, 31), (18, 26), (18, 27), (18, 28), (18, 29), (18, 30),
                     (19, 26), (19, 28), (19, 29), (19, 31)}
    for uid, position in (('crew-0', [306250.0, 4038483.291942265]),
                          ('crew-1', [306250.0, 4038486.8897650745])):
        base = env.ops.resources[uid]['base_position']
        target = env.router._cell(base)
        assert target in env.router._hazards(later_burning, [0., 1.])
        assert not env.router.options(position, target, mode='crew', burning=later_burning,
            wind=[0., 1.], target_metric=base, emergency_egress=True,
            recovery_origin_candidates=True)['options']
    env.reset('incident-day-realmap-v2-002')
    assert 'recovery_candidate_policy' not in env._scenario['profile']


def test_active_redesigned_profile_opts_into_recovery_candidates(source_environment, monkeypatch):
    from envs.wildfire_response.incident_day.real_map_scenarios import REDESIGNED_POST_NODE

    env = source_environment
    env.reset('incident-day-realmap-development-v2-005')
    assert env._scenario['profile']['recovery_candidate_policy'] == 'bounded-origin-targets-v1'
    unit = env.ops.resources['crew-0']
    assert unit['base_position'] == env.network.nodes[REDESIGNED_POST_NODE]
    calls = []
    def options(*args, **kwargs):
        calls.append((args, kwargs))
        return {'options': []}
    monkeypatch.setattr(env.router, 'options', options)
    assert env._return_route(unit, 0) is None
    assert calls[0][1]['recovery_origin_candidates'] is True
    assert calls[0][1]['emergency_egress'] is True
    assert calls[0][1]['target_metric'] == unit['base_position']


def test_redesigned_corridor_has_only_declared_pinned_directions_and_supports_recorded_escape(source_environment):
    from envs.wildfire_response.incident_day.real_map_scenarios import REDESIGNED_EGRESS_EDGES
    env = source_environment
    expected = {
        'osm:w652769435:0000:r', 'osm:w652769435:0001:r', 'osm:w652769435:0002:r',
        'osm:w47438359:0000:r', 'osm:w47438359:0001:r', 'osm:w47438359:0002:r',
        'osm:w47438359:0003:r', 'osm:w374742049:0000:f',
    }
    assert REDESIGNED_EGRESS_EDGES == expected
    original = None
    for index in range(12):
        env.reset(f'incident-day-realmap-development-v1-{index:03}')
        before = set(env._scenario['profile']['allowed_road_edge_ids'])
        assert not before & expected
        if index == 5:
            original = before
        env.reset(f'incident-day-realmap-development-v2-{index:03}')
        after = set(env._scenario['profile']['allowed_road_edge_ids'])
        assert after == before | (expected if index in (5, 6) else set())
        if index in (5, 6):
            assert set(env._scenario['profile']['synthetic_egress_corridor']['edge_ids']) == expected
            assert expected <= set(env.network.edges)
            for edge_id in expected:
                reverse = edge_id[:-1] + ('f' if edge_id.endswith('r') else 'r')
                assert reverse not in after
    env.reset('incident-day-realmap-development-v2-005')
    burning = {(13, 31), (14, 26), (14, 27), (14, 28), (14, 29), (14, 30), (14, 31),
               (15, 26), (15, 27), (15, 28), (15, 29), (15, 30), (16, 26), (16, 27),
               (16, 28), (16, 29), (16, 30), (17, 26), (17, 27), (17, 28), (17, 29),
               (17, 30), (17, 31), (18, 26), (18, 27), (18, 28), (18, 29), (18, 30),
               (19, 26), (19, 28), (19, 29), (19, 31)}
    position = [306250.0, 4038486.9986383966]
    base = env.ops.resources['crew-0']['base_position']
    target = env.router._cell(base)
    args = dict(mode='crew', burning=burning, wind=[0., 1.], target_metric=base,
                emergency_egress=True, recovery_origin_candidates=True, limit=1)
    legacy_router = HazardRouter(env.network, env.inputs, env.inputs['heights'],
                                 walk_mask=env.inputs['walk_mask'], allowed_edge_ids=original)
    assert not legacy_router.options(position, target, **args)['options']
    journey = env.router.options(position, target, **args)['options'][0]['journey']
    assert {leg['edge_id'] for leg in journey['legs'] if leg['mode'] == 'road'} - original <= expected
    assert any(leg['edge_id'] in expected for leg in journey['legs'] if leg['mode'] == 'road')
    assert journey['origin'] == position and journey['destination'] == base
    for minute in range(journey['duration_minutes']):
        checked = env.router.safe_journey_segment(journey, minute, burning, [0., 1.],
                                                   mode='crew', egress_status=True)
        assert checked['safe']
        journey['emergency_egress']['exited'] = checked['egress_exited']
    assert journey['emergency_egress']['exited']
    # A burning base remains forbidden even with the extended corridor.
    assert not env.router.options(position, target, **dict(args, burning=burning | {target}))['options']


@pytest.mark.parametrize('index', [5, 6])
def test_corridor_keeps_meaningful_family_event(source_environment, index):
    from envs.wildfire_response.incident_day.plausibility import counterfactual_case
    env = source_environment
    env.reset(f'incident-day-realmap-development-v2-{index:03}')
    probe = counterfactual_case(env)
    assert probe['status'] == 'demonstrated'
    if index == 5:
        routes = probe['details']['candidate_routes']
        assert routes['engine']['before'] > 0 and routes['engine']['after'] == 0
        assert routes['crew']['after'] > 0
    else:
        assert probe['details']['minute'] == 180
        assert probe['details']['otherwise_open'] and probe['details']['closed_by_event']
