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


def test_recorded_real_map_recovery_geometry_when_pinned_inputs_available():
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
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=PACK_PIN,
                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    env.reset('incident-day-realmap-development-v1-005')
    assert env._scenario['profile']['recovery_candidate_policy'] == 'bounded-origin-targets-v1'
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
