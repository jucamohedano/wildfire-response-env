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
    assert original['version'] == 4
    for state in [dict(version=True, coverage={}, findings={}),
                  dict(version=2, coverage={}, findings={}),
                  dict(version=3, coverage={}, findings={}),
                  dict(version=4, coverage={'ticks_observed': True}, findings={}),
                  dict(version=4, coverage={}, findings={'bad': {'count': 1, 'first': {
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


def returning_aircraft(journey):
    return dict(id='plane', kind='aircraft', state='returning', position=[90, 0],
                base_position=[100, 0], journey=journey, facility_id='airbase',
                capacity_litres=3100, water_litres=0, fuel_minutes=40,
                fuel_capacity_minutes=180, clearance=True)


def shortage_ops(journey=None, *, position=None, base=None, facility_stock=2800, kind='aircraft', minute=10):
    journey = copy.deepcopy(journey or route())
    aircraft = returning_aircraft(journey)
    aircraft['kind'] = kind
    if base is not None:
        aircraft['base_position'] = base
    def finish(ops, now, kwargs):
        ops.resources['plane'].update(position=position if position is not None else [100, 0],
                                      state='needs_assistance', journey=None, fuel_minutes=39)
    ops = FakeOps(resources={'plane': aircraft}, mutate=finish)
    ops.last_minute = minute - 1
    ops.profile['facilities'] = {'airbase': dict(position=[100, 0], allowed_kinds=['aircraft'],
                                                 refuel=True, water_litres=facility_stock)}
    return ops


def test_aircraft_arrival_with_finite_shortage_keeps_path_and_geometry_checks():
    ops = shortage_ops()
    observer = MovementObserver(fake(ops))
    tick(ops, minute=10)
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['timed_positions_checked'] == 2
    assert observer.snapshot()['coverage']['road_legs_checked'] == 2
    assert observer.snapshot()['coverage']['capacity_ticks_checked'] == 1


@pytest.mark.parametrize('fault,expected', [
    ('wrong_base', 'movement_during_assistance_transition'),
    ('wrong_endpoint', 'movement_during_assistance_transition'),
    ('off_path', 'movement_during_assistance_transition'),
    ('early', 'early_arrival'),
    ('road_geometry', 'unsupported_road_leg'),
    ('prior_off_path', 'off_timed_path'),
    ('disconnected_route', 'movement_during_assistance_transition'),
    ('invalid_clock', 'movement_during_assistance_transition'),
    ('no_shortage', 'movement_during_assistance_transition'),
    ('invalid_stock', 'movement_during_assistance_transition'),
    ('exhausted_fuel', 'movement_during_assistance_transition'),
    ('incorrect_fuel', 'movement_during_assistance_transition'),
    ('changed_water', 'movement_during_assistance_transition'),
    ('ground_unit', 'movement_during_assistance_transition'),
])
def test_shortage_arrival_does_not_hide_faults(fault, expected):
    journey = route(edge='missing' if fault == 'road_geometry' else 'e')
    kwargs = {}
    minute = 10
    if fault == 'wrong_base': kwargs['base'] = [101, 0]
    if fault == 'wrong_endpoint': journey['destination'] = [99, 0]
    if fault == 'disconnected_route': journey['legs'][0]['coordinates'][0] = [5, 0]
    if fault == 'invalid_clock': journey['arrives_at'] = 11
    if fault == 'off_path': kwargs['position'] = [100, 1]
    if fault == 'early': minute = 9
    if fault == 'no_shortage': kwargs['facility_stock'] = 3100
    if fault == 'invalid_stock': kwargs['facility_stock'] = float('nan')
    if fault == 'ground_unit': kwargs['kind'] = 'engine'
    ops = shortage_ops(journey, minute=minute, **kwargs)
    if fault == 'prior_off_path': ops.resources['plane']['position'] = [90, 2]
    if fault == 'exhausted_fuel': ops.resources['plane']['fuel_minutes'] = 0
    if fault == 'incorrect_fuel': ops.resources['plane']['fuel_minutes'] = 41
    if fault == 'changed_water':
        mutate = ops.mutate
        def corrupt_water(ops, now, kwargs):
            mutate(ops, now, kwargs)
            ops.resources['plane']['water_litres'] = 100
        ops.mutate = corrupt_water
    observer = MovementObserver(fake(ops))
    tick(ops, minute=minute)
    assert expected in observer.snapshot()['findings']


def test_actual_operations_arrival_and_finite_refill_shortage():
    from envs.wildfire_response.incident_day.operations import Operations
    aircraft = returning_aircraft(route())
    ops = Operations([aircraft], [], [], {'aircraft_max_wind': 5, 'facilities': {
        'airbase': dict(position=[100, 0], allowed_kinds=['aircraft'], refuel=True, water_litres=2800)}})
    ops.last_minute = 9
    observer = MovementObserver(fake(ops))
    tick(ops, minute=10, hazard_check=lambda *args: True, emit=lambda *args, **kwargs: None)
    assert ops.resources['plane']['state'] == 'needs_assistance'
    assert ops.resources['plane']['position'] == [100, 0]
    assert ops.profile['facilities']['airbase']['water_litres'] == 2800
    assert ops.resources['plane']['fuel_minutes'] == 39
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['timed_positions_checked'] == 2


def test_saved_evaluation_aircraft_return_against_recorded_audit():
    import gzip
    from pathlib import Path
    path = Path('/var/tmp/wildfire-evaluation-baselines-v2/private/es-ma-2021-incident-day-evaluation-v1-003-dispatcher.json.gz')
    if not path.is_file():
        pytest.skip('Saved private evaluation audit unavailable')
    with gzip.open(path, 'rt') as stream:
        data = json.load(stream)
    before, after = (data['audit'][minute]['resources']['plane-1'] for minute in (776, 777))
    origin = data['audit'][761]['resources']['plane-1']['position']
    base = after['position']
    assert before['state'] == 'returning' and after['state'] == 'needs_assistance'
    assert before['fuel_minutes'] - after['fuel_minutes'] == 1
    # The audit records positions but not journey plans; reconstruct the observed
    # straight flight solely for this local observer regression.
    leg = make_leg('flight', [origin, base], 240)
    journey = make_journey([leg], origin, base, 'test')
    journey.update(departed_at=761, arrives_at=777, duration_minutes=16)
    journey['legs'][0]['duration_minutes'] = 16
    def finish(ops, minute, kwargs):
        ops.resources['plane-1'].update(position=base, state='needs_assistance', journey=None,
                                        fuel_minutes=after['fuel_minutes'])
    plane = dict(id='plane-1', kind='aircraft', state='returning', position=before['position'],
                 base_position=base, journey=journey, facility_id='airbase', capacity_litres=3100,
                 water_litres=before['water_litres'], fuel_minutes=before['fuel_minutes'])
    ops = FakeOps(resources={'plane-1': plane}, mutate=finish)
    ops.last_minute = 776
    ops.profile['facilities'] = {'airbase': dict(position=base, allowed_kinds=['aircraft'],
                                                 refuel=True, water_litres=2800)}
    observer = MovementObserver(fake(ops))
    tick(ops, minute=777)
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['timed_positions_checked'] == 2


def ground_shortage_ops(journey=None, *, stock=3000, position=None, base=None, minute=10):
    ops = shortage_ops(journey, kind='engine', facility_stock=stock,
                       position=position, base=base, minute=minute)
    ops.resources['plane'].update(capacity_litres=3500, fuel_minutes=None)
    ops.profile['facilities']['airbase']['allowed_kinds'] = ['engine', 'crew']
    ops.profile['facilities']['airbase'].pop('refuel')
    def finish(ops, now, kwargs):
        ops.resources['plane'].update(position=position if position is not None else [100, 0],
                                      state='needs_assistance', journey=None)
    ops.mutate = finish
    return ops


def test_engine_arrival_with_shortage_checks_both_positions_and_directed_road():
    ops = ground_shortage_ops()
    observer = MovementObserver(fake(ops))
    tick(ops, minute=10)
    evidence = observer.snapshot()
    assert evidence['findings'] == {}
    assert evidence['coverage']['timed_positions_checked'] == 2
    assert evidence['coverage']['road_legs_checked'] == 2
    assert evidence['coverage']['capacity_ticks_checked'] == 1


@pytest.mark.parametrize('fault,expected', [
    ('wrong_base', 'movement_during_assistance_transition'),
    ('near_base', 'movement_during_assistance_transition'),
    ('wrong_endpoint', 'movement_during_assistance_transition'),
    ('off_path', 'movement_during_assistance_transition'),
    ('prior_off_path', 'off_timed_path'),
    ('early', 'early_arrival'),
    ('road_geometry', 'unsupported_road_leg'),
    ('wrong_direction', 'direction_invalid_road_leg'),
    ('disconnected_route', 'movement_during_assistance_transition'),
    ('invalid_clock', 'movement_during_assistance_transition'),
    ('no_shortage', 'movement_during_assistance_transition'),
    ('invalid_stock', 'movement_during_assistance_transition'),
    ('wrong_facility_kind', 'movement_during_assistance_transition'),
    ('malformed_facility_kinds', 'movement_during_assistance_transition'),
    ('wrong_facility_position', 'movement_during_assistance_transition'),
    ('incompatible', 'movement_during_assistance_transition'),
    ('changed_water', 'movement_during_assistance_transition'),
    ('zero_capacity_crew', 'movement_during_assistance_transition'),
])
def test_ground_shortage_arrival_rejects_unproven_and_invalid_paths(fault, expected):
    journey = route(edge='missing' if fault == 'road_geometry' else 'e')
    args, minute = {}, 10
    if fault == 'wrong_base': args['base'] = [101, 0]
    if fault == 'near_base': args['position'] = [100.01, 0]
    if fault == 'wrong_endpoint': journey['destination'] = [99, 0]
    if fault == 'off_path': args['position'] = [100, 1]
    if fault == 'early': minute = 9
    if fault == 'no_shortage': args['stock'] = 3500
    if fault == 'invalid_stock': args['stock'] = float('nan')
    if fault == 'disconnected_route': journey['legs'][0]['coordinates'][0] = [5, 0]
    if fault == 'invalid_clock': journey['arrives_at'] = 11
    if fault == 'wrong_direction':
        journey = route([[100, 0], [0, 0]])
        args.update(base=[0, 0], position=[0, 0])
    ops = ground_shortage_ops(journey, minute=minute, **args)
    if fault == 'wrong_direction':
        ops.resources['plane']['position'] = [10, 0]
        ops.profile['facilities']['airbase']['position'] = [0, 0]
    if fault == 'prior_off_path': ops.resources['plane']['position'] = [90, 2]
    if fault == 'wrong_facility_kind': ops.profile['facilities']['airbase']['allowed_kinds'] = ['crew']
    if fault == 'malformed_facility_kinds': ops.profile['facilities']['airbase']['allowed_kinds'] = None
    if fault == 'wrong_facility_position': ops.profile['facilities']['airbase']['position'] = [100.01, 0]
    if fault == 'incompatible': ops.resources['plane']['refill_compatible'] = False
    if fault == 'zero_capacity_crew': ops.resources['plane'].update(kind='crew', capacity_litres=0)
    if fault == 'changed_water':
        mutate = ops.mutate
        def corrupt_water(ops, now, kwargs):
            mutate(ops, now, kwargs)
            ops.resources['plane']['water_litres'] = 100
        ops.mutate = corrupt_water
    observer = MovementObserver(fake(ops))
    tick(ops, minute=minute)
    assert expected in observer.snapshot()['findings']


@pytest.mark.parametrize('mode', ['flight', 'walk'])
def test_engine_shortage_arrival_rejects_nonroad_leg(mode):
    journey = route()
    journey['legs'][0]['mode'] = mode
    ops = ground_shortage_ops(journey)
    observer = MovementObserver(fake(ops))
    tick(ops, minute=10)
    assert 'movement_during_assistance_transition' in observer.snapshot()['findings']


def test_real_engine_tick_arrives_then_cannot_refill():
    from envs.wildfire_response.incident_day.operations import Operations
    engine = returning_aircraft(route())
    engine.update(kind='engine', capacity_litres=3500, fuel_minutes=None)
    ops = Operations([engine], [], [], {'aircraft_max_wind': 5, 'facilities': {
        'airbase': dict(position=[100, 0], allowed_kinds=['engine'], water_litres=3000)}})
    ops.last_minute = 9
    observer = MovementObserver(fake(ops))
    tick(ops, minute=10, hazard_check=lambda *args: True, movement_check=lambda *args: True,
         emit=lambda *args, **kwargs: None)
    assert ops.resources['plane']['state'] == 'needs_assistance'
    assert ops.resources['plane']['position'] == [100, 0]
    assert ops.profile['facilities']['airbase']['water_litres'] == 3000
    assert ops.resources['plane']['water_litres'] == 0
    assert observer.snapshot()['findings'] == {}
    assert observer.snapshot()['coverage']['road_legs_checked'] == 2


@pytest.mark.parametrize('resource,minute,facility,expected_stock', [
    ('engine-0-shift-3', 1030, 'center-0', 3000),
    ('engine-1-shift-3', 1031, 'center-1', 1000),
])
def test_saved_007_ground_arrival_shortage_recognition(resource, minute, facility, expected_stock):
    import gzip
    from pathlib import Path
    path = Path('/var/tmp/wildfire-evaluation-baselines-v2/private/es-ma-2021-incident-day-evaluation-v1-007-dispatcher.json.gz')
    if not path.is_file():
        pytest.skip('Saved private evaluation audit unavailable')
    with gzip.open(path, 'rt') as stream:
        data = json.load(stream)
    audit = data['audit']
    before, after = audit[minute-1]['resources'][resource], audit[minute]['resources'][resource]
    assert before['state'] == 'returning' and after['state'] == 'needs_assistance'
    report = next(r for r in data['reports'] if r['kind'] == 'resource_status'
                  and r['observed_at'] == minute and r['payload']['resource']['id'] == resource)
    base = report['payload']['resource']['base_position']
    assert after['position'] == base and after['water_litres'] == before['water_litres'] == 0
    prefix = 'engine-' + facility[-1]
    related = [prefix, prefix + '-shift-2', prefix + '-shift-3']
    refilled = sum(max(0, b['resources'][uid]['water_litres'] - a['resources'][uid]['water_litres'])
                   for a, b in zip(audit, audit[1:minute+1]) for uid in related)
    assert not any(r['state'] == 'refilling' for r in audit[minute]['resources'].values())
    stock = 40000 - refilled  # 100000 parent stock × the saved 0.4 facility fraction.
    assert data['evaluation_metadata']['configuration']['facility_fraction'] == .4
    assert stock == expected_stock < 3500
    # The saved minute audit omits journey plans. Use a synthetic final segment
    # to exercise recognition with the exact saved positions; separate tests
    # verify directed real-road geometry and full route timing.
    leg = make_leg('road', [before['position'], base], 6, edge_id='e')
    journey = make_journey([leg], before['position'], base, 'test')
    journey.update(departed_at=minute-1, arrives_at=minute, duration_minutes=1)
    journey['legs'][0]['duration_minutes'] = 1
    unit = dict(id=resource, kind='engine', state='returning', position=before['position'],
                base_position=base, journey=journey, facility_id=facility,
                capacity_litres=3500, water_litres=before['water_litres'])
    current = dict(unit, state='needs_assistance', position=after['position'], journey=None)
    facilities = {facility: dict(position=base, allowed_kinds=['engine'], water_litres=stock)}
    assert MovementObserver._arrived_without_refill(unit, current, facilities)
