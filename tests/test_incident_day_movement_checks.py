import copy
import json
from types import SimpleNamespace

import pytest

from eval.incident_day_movement_checks import MovementObserver
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_core.mobility_routing import make_leg, make_journey


def create():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    return env


def test_real_tick_observer_preserves_physics_and_checkpoint_restore():
    env, control = create(), create()
    observer = MovementObserver(env)
    for system in (env, control):
        option = system.route_options('crew-1')['options'][0]
        system.step({'requests': [dict(resource_id='crew-1', sector_id='incident', approach_id=option['id'])]})
    for _ in range(16):
        for system in (env, control):
            offers = [x['id'] for x in system.get_situation()['offers'] if x['status'] == 'offered']
            action = {'authorizations': offers} if offers else {'advance_minutes': 10}
            system.step(action)
        assert env.get_situation() == control.get_situation()
        assert env.checkpoint() == control.checkpoint()
    state = json.loads(json.dumps(observer.snapshot(), allow_nan=False))
    assert state['coverage']['ticks_observed'] > 0
    assert state['coverage']['timed_positions_checked'] > 0
    assert state['coverage']['road_legs_checked'] > 0
    assert state['findings'] == {}
    restored_env = create()
    restored_env.restore(env.checkpoint())
    restored = MovementObserver(restored_env)
    restored.restore(state)
    assert restored.snapshot() == state
    env.advance_time(10)
    restored_env.advance_time(10)
    assert restored.snapshot() == observer.snapshot()
    with pytest.raises(ValueError):
        MovementObserver(env)


class FakeOps:
    def __init__(self, resources=None, groups=None, orders=None, destinations=None, reservations=None, mutate=None):
        self.resources = resources or {}
        self.groups = groups or {}
        self.orders = orders or {}
        self.destinations = destinations or {}
        self.reservations = reservations or {}
        self.profile = {'aircraft_max_wind': 5}
        self.last_minute = 0
        self.mutate = mutate or (lambda ops, minute, kwargs: None)

    def tick(self, minute, **kwargs):
        self.mutate(self, minute, kwargs)
        self.last_minute = minute


def fake(ops):
    network = SimpleNamespace(edges={'e': {'coordinates': [[0, 0], [100, 0], [100, 100]]}})
    return SimpleNamespace(ops=ops, network=network)


def route(coordinates=None, duration=10, edge='e'):
    coordinates = coordinates or [[0, 0], [100, 0]]
    leg = make_leg('road', coordinates, 6, edge_id=edge)
    result = make_journey([leg], coordinates[0], coordinates[-1], 'test')
    result['duration_minutes'] = result['legs'][0]['duration_minutes'] = duration
    result.update(departed_at=0, arrives_at=duration)
    return result


def tick(ops, minute=1, **kwargs):
    args = dict(wind=[0, 0], daylight=True, suppress=lambda unit: True)
    args.update(kwargs)
    ops.tick(minute, **args)


def unit(journey, **kwargs):
    return dict(id='u', kind='crew', state='travel', position=[0, 0], journey=journey, **kwargs)


def test_off_path_and_early_arrival_are_detected():
    def bad(ops, minute, kwargs):
        ops.resources['u'].update(position=[100, 0], state='on_scene')
    ops = FakeOps(resources={'u': unit(route())}, mutate=bad)
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert {'off_timed_path', 'early_arrival'} <= observer.snapshot()['findings'].keys()


def test_blocked_shift_and_zero_length_return_are_valid():
    journey = route()
    def block(ops, minute, kwargs):
        u = ops.resources['u']
        u.update(state='blocked', blocked_state='travel')
        u['journey']['departed_at'] += 1
        u['journey']['arrives_at'] += 1
    ops = FakeOps(resources={'u': unit(journey)}, mutate=block)
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert observer.snapshot()['findings'] == {}
    zero = dict(origin=[0, 0], destination=[0, 0], legs=[], duration_minutes=1, departed_at=0, arrives_at=1)
    def finish(ops, minute, kwargs):
        ops.resources['u'].update(state='available', journey=None)
    ops = FakeOps(resources={'u': unit(zero)}, mutate=finish)
    ops.resources['u']['state'] = 'returning'
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert observer.snapshot()['findings'] == {}


@pytest.mark.parametrize('coordinates,edge,kind', [
    ([[100, 0], [0, 0]], 'e', 'direction_invalid_road_leg'),
    ([[0, 0], [100, 0]], 'missing', 'unsupported_road_leg'),
    ([[0, 0], [50, 10]], 'e', 'unsupported_road_leg'),
])
def test_invalid_road_legs(coordinates, edge, kind):
    journey = route(coordinates, edge=edge)
    start = coordinates[0]
    def advance(ops, minute, kwargs):
        ops.resources['u']['position'] = [start[i] + (coordinates[-1][i] - start[i]) / 10 for i in (0, 1)]
    ops = FakeOps(resources={'u': unit(journey)}, mutate=advance)
    ops.resources['u']['position'] = start[:]
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert kind in observer.snapshot()['findings']


def test_aircraft_and_capacity_checks_use_actual_tick():
    aircraft = dict(id='plane', kind='aircraft', state='mobilizing', clearance=False, position=[0, 0], journey=None)
    groups = {'g': dict(id='g', count=11, position=[0, 0], destination_id='d')}
    def bad(ops, minute, kwargs):
        ops.resources['plane']['state'] = 'travel'
        kwargs['suppress'](copy.deepcopy(ops.resources['plane']))
    ops = FakeOps(resources={'plane': aircraft}, groups=groups,
                  destinations={'d': {'id': 'd', 'capacity': 10}}, reservations={'g': 'd'}, mutate=bad)
    observer = MovementObserver(fake(ops))
    tick(ops, daylight=False)
    result = observer.snapshot()
    assert {'aircraft_drop_without_modeled_clearance', 'aircraft_departure_without_modeled_clearance',
            'capacity_exceeded'} <= result['findings'].keys()
    assert result['coverage']['modeled_aircraft_drops_checked'] == 1


def test_failed_drop_is_not_counted_and_reservation_is_not_double_counted():
    aircraft = dict(id='plane', kind='aircraft', state='mobilizing', clearance=True, position=[0, 0], journey=None)
    groups = {'g': dict(id='g', count=10, position=[0, 0], destination_id='d')}
    def step(ops, minute, kwargs):
        ops.resources['plane']['state'] = 'travel'
        kwargs['suppress'](copy.deepcopy(ops.resources['plane']))
    ops = FakeOps(resources={'plane': aircraft}, groups=groups, destinations={'d': {'capacity': 10}},
                  reservations={'g': 'd'}, mutate=step)
    observer = MovementObserver(fake(ops))
    tick(ops, suppress=lambda unit: False)
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['modeled_aircraft_departures_checked'] == 1
    assert observer.snapshot()['coverage'].get('modeled_aircraft_drops_checked', 0) == 0


def test_cohort_timed_movement_and_capacity_reservations():
    journey = route()
    group = dict(id='g', count=5, position=[0, 0], destination_id=None, order_id='o', status='evacuating')
    order = dict(id='o', group_id='g', state='executing', started_at=0, journey=journey)
    def bad(ops, minute, kwargs):
        ops.groups['g']['position'] = [10, 40]
        ops.reservations['g'] = 'd'
    ops = FakeOps(groups={'g': group}, orders={'o': order},
                  destinations={'d': {'id': 'd', 'capacity': 4}}, mutate=bad)
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert {'off_timed_path', 'capacity_exceeded'} <= observer.snapshot()['findings'].keys()
    assert observer.snapshot()['coverage']['timed_positions_checked'] == 1


def test_changed_return_and_subedge_inherited_journeys():
    def replace(ops, minute, kwargs):
        ops.resources['u'].update(state='returning', journey=route([[0, 0], [50, 0]]))
    ops = FakeOps(resources={'u': unit(route())}, mutate=replace)
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert observer.snapshot()['findings'] == {}
    journey = route([[25, 0], [100, 0], [100, 50]])
    journey.update(departed_at=-4, arrives_at=6)
    def advance(ops, minute, kwargs):
        ops.resources['u']['position'] = [87.5, 0]
    inherited = unit(journey)
    inherited['position'] = [75, 0]
    ops = FakeOps(resources={'u': inherited}, mutate=advance)
    observer = MovementObserver(fake(ops))
    tick(ops)
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['road_legs_checked'] == 1


def test_snapshot_rejects_invalid_or_non_json_state_atomically():
    observer = MovementObserver(fake(FakeOps()))
    tick(observer.ops)
    original = observer.snapshot()
    for state in [dict(version=True, coverage={}, findings={}),
                  dict(version=2, coverage={'ticks_observed': True}, findings={}),
                  dict(version=2, coverage={}, findings={'bad': {'count': 1, 'first': {
                      'minute': 1, 'entity': 'u', 'detail': 'bad', 'extra': object()}}})]:
        with pytest.raises(ValueError):
            observer.restore(state)
        assert observer.snapshot() == original


def test_interrupted_travel_checks_previous_position_and_still_detects_bad_movement():
    def stop(ops, minute, kwargs):
        ops.resources['u']['state'] = 'needs_assistance'
    for initial_position, expected in [([10, 0], False), ([10, 4], True)]:
        journey = route()
        moving = unit(journey)
        moving['position'] = initial_position
        ops = FakeOps(resources={'u': moving}, mutate=stop)
        ops.last_minute = 1
        observer = MovementObserver(fake(ops))
        tick(ops, minute=2)
        assert ('off_timed_path' in observer.snapshot()['findings']) is expected
        assert observer.snapshot()['coverage']['stationary_assistance_transitions'] == 1

    def move_and_stop(ops, minute, kwargs):
        ops.resources['u'].update(position=[20, 0], state='needs_assistance')
    moving = unit(route())
    moving['position'] = [10, 0]
    ops = FakeOps(resources={'u': moving}, mutate=move_and_stop)
    ops.last_minute = 1
    observer = MovementObserver(fake(ops))
    tick(ops, minute=2)
    assert 'movement_during_assistance_transition' in observer.snapshot()['findings']


def test_stationary_active_travel_still_requires_timed_progress():
    moving = unit(route())
    moving['position'] = [10, 0]
    ops = FakeOps(resources={'u': moving})
    ops.last_minute = 1
    observer = MovementObserver(fake(ops))
    tick(ops, minute=2)
    assert 'off_timed_path' in observer.snapshot()['findings']
