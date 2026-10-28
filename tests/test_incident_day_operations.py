import json

import pytest

from envs.wildfire_response.incident_day.operations import Operations
from wildfire_core.mobility_routing import journey_position, make_journey, make_leg


def route(start, end, mode='road'):
    return make_journey([make_leg(mode, [start, end], 60)], start, end, 'test route')


def unit(identifier='engine-1', kind='engine', **changes):
    result = dict(id=identifier, kind=kind, home_base='base', position=[0, 0],
                  base_position=[0, 0], capacity_litres=100, water_litres=40,
                  state='available', available_at=0, work_remaining=240, rest_minutes=2)
    result.update(changes)
    return result


def group(identifier='g1', count=4, **changes):
    result = dict(id=identifier, count=count, position=[0, 0], home=[0, 0],
                  status='at_home', exposed=False, destination_id=None)
    result.update(changes)
    return result


def destination(identifier='d1', capacity=5, position=None):
    return dict(id=identifier, name=identifier, position=position or [1000, 0],
                capacity=capacity, status='open')


def run(operations, minute, *, safe=None, wind=0, daylight=True, effects=None, planner=None,
        exposure_check=None, events=None, movement_check=None):
    operations.tick(minute, wind=wind, daylight=daylight,
                    hazard_check=safe or (lambda position, kind: True),
                    suppress=(effects if effects is not None else []).append,
                    emit=lambda source, kind, payload, **kwargs: (
                        events.append((source, kind, payload, kwargs)) if events is not None else None),
                    return_planner=planner, exposure_check=exposure_check, movement_check=movement_check)


def return_plan(resource, minute):
    return route(resource['position'], resource['base_position'],
                 'flight' if resource['kind'] == 'aircraft' else 'road')


def test_setup_stock_and_duty_require_actual_arrival_and_return_route():
    effects = []
    operations = Operations([unit(work_remaining=2)], [], [], {}, return_planner=return_plan)
    assert operations.dispatch('engine-1', 'sector', [2, 3], route([0, 0], [1000, 0]), 0)
    assert not operations.dispatch('engine-1', 'sector', [2, 3], route([0, 0], [1000, 0]), 0)
    for minute in range(7):
        run(operations, minute, effects=effects)
    assert effects == []
    run(operations, 7, effects=effects)
    assert len(effects) == 1
    assert effects[0]['water_litres'] == 40
    run(operations, 8, effects=effects)
    assert len(effects) == 2
    resource = operations.resources['engine-1']
    assert resource['water_litres'] == 0
    assert resource['work_remaining'] == 0
    assert resource['state'] == 'returning'
    for minute in range(9, 23):
        run(operations, minute, effects=effects)
    assert resource['state'] == 'available'
    assert resource['water_litres'] == 100
    assert resource['work_remaining'] == 240
    assert len(effects) == 2


def test_return_requires_continuous_supplied_route_and_preserves_position():
    operations = Operations([unit()], [], [], {})
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(4):
        run(operations, minute)
    resource = operations.resources['engine-1']
    assert resource['position'] == [2000, 0]
    assert not operations.release('engine-1', route([0, 0], [0, 0]), 3)
    assert resource['state'] == 'needs_assistance'
    assert resource['position'] == [2000, 0]
    assert operations.release('engine-1', route([2000, 0], [0, 0]), 3)
    run(operations, 4)
    assert resource['position'] == [1000, 0]
    run(operations, 5)
    assert resource['position'] == [0, 0]


def test_release_before_movement_uses_last_validated_position():
    operations = Operations([unit()], [], [], {})
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(3):
        run(operations, minute)
    resource = operations.resources['engine-1']
    assert resource['position'] == [1000, 0]
    assert operations.release('engine-1', route([1000, 0], [0, 0]), 3)
    assert resource['state'] == 'returning'
    assert resource['position'] == [1000, 0]
    assert resource['journey']['origin'] == [1000, 0]
    run(operations, 3)
    assert resource['position'] == [1000, 0]
    run(operations, 4)
    assert resource['position'] == [0, 0]


@pytest.mark.parametrize('stopped_state', ['blocked', 'needs_assistance'])
def test_stopped_resource_can_dispatch_alternative_route_from_actual_position(stopped_state):
    operations = Operations([unit(water_litres=37, work_remaining=23)], [], [], {})
    assert operations.dispatch('engine-1', 'first', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(3):
        run(operations, minute)
    resource = operations.resources['engine-1']
    if stopped_state == 'blocked':
        for minute in (3, 4):
            run(operations, minute, safe=lambda position, kind: False)
    else:
        assert not operations.release('engine-1', None, 2)
        run(operations, 3)
        run(operations, 4)
    assert resource['state'] == stopped_state
    assert resource['position'] == [1000, 0]
    original_journey = json.loads(json.dumps(resource['journey']))
    assert not operations.dispatch('engine-1', 'next', [1, 1], route([0, 0], [1000, 2000]), 4)
    assert resource['state'] == stopped_state
    assert resource['journey'] == original_journey
    assert operations.dispatch('engine-1', 'next', [1, 1], route([1000, 0], [1000, 2000]), 4)
    assert resource['position'] == [1000, 0]
    assert resource['water_litres'] == 37
    assert resource['work_remaining'] == 23
    assert resource['state'] == 'mobilizing'
    assert 'blocked_state' not in resource
    run(operations, 5)
    assert resource['position'] == [1000, 0]
    run(operations, 6)
    assert resource['position'] == [1000, 1000]
    run(operations, 7)
    assert resource['position'] == [1000, 2000]
    assert resource['state'] == 'on_scene'


@pytest.mark.parametrize('stopped_state', ['blocked', 'needs_assistance'])
@pytest.mark.parametrize('changes', [dict(water_litres=0), dict(work_remaining=0), dict(available_at=5)])
def test_stopped_resource_still_requires_readiness_and_finite_stock(stopped_state, changes):
    resource = unit(state=stopped_state, position=[1000, 0], **changes)
    operations = Operations([resource], [], [], {})
    assert not operations.dispatch('engine-1', 'next', [1, 1], route([1000, 0], [1000, 1000]), 0)
    assert operations.resources['engine-1']['state'] == stopped_state


@pytest.mark.parametrize('returning', [False, True])
def test_resource_movement_blocks_once_and_resumes_on_original_route(returning):
    operations = Operations([unit()], [], [], {})
    events = []
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(3):
        run(operations, minute, events=events)
    resource = operations.resources['engine-1']
    if returning:
        assert operations.release('engine-1', route([1000, 0], [0, 0]), 2)
    original_legs = json.loads(json.dumps(resource['journey']['legs']))
    for minute in (3, 4):
        run(operations, minute, safe=lambda position, kind: False, events=events)
        assert resource['state'] == 'blocked'
        assert resource['blocked_state'] == ('returning' if returning else 'travel')
        assert resource['position'] == [1000, 0]
    assert [event[1] for event in events] == ['movement_blocked']
    restored = Operations.restore(json.loads(json.dumps(operations.checkpoint())))
    run(operations, 5, events=events)
    run(restored, 5)
    assert restored.checkpoint() == operations.checkpoint()
    assert resource['position'] == ([0, 0] if returning else [2000, 0])
    if returning:
        assert resource['journey'] is None
    else:
        assert resource['journey']['legs'] == original_legs
    assert [event[1] for event in events] == ['movement_blocked', 'movement_resumed']
    assert all(event[3]['actionable'] for event in events)
    if not returning:
        for minute in range(6, 9):
            run(operations, minute, events=events)
        assert resource['position'] == [5000, 0]
        assert resource['state'] == 'on_scene'


@pytest.mark.parametrize('returning', [False, True])
def test_resource_swept_check_blocks_unsafe_intermediate_position(returning):
    operations = Operations([unit()], [], [], {})
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(4):
        run(operations, minute)
    resource = operations.resources['engine-1']
    if returning:
        assert operations.release('engine-1', route([2000, 0], [0, 0]), 3)
    blocked_coordinate = 1500 if returning else 2500
    segments = []

    def check(journey, minute, kind):
        start = journey_position(journey, minute - 1)[0]
        end = journey_position(journey, minute)[0]
        segments.append((start, end, minute, kind))
        journey['legs'].clear()
        return not min(start[0], end[0]) < blocked_coordinate < max(start[0], end[0])

    events = []
    for minute in (4, 5):
        run(operations, minute, movement_check=check, events=events)
        assert resource['position'] == [2000, 0]
        assert resource['state'] == 'blocked'
    expected_end = [1000, 0] if returning else [3000, 0]
    assert segments == [([2000, 0], expected_end, 4, 'engine'),
                        ([2000, 0], expected_end, 5, 'engine')]
    assert len(resource['journey']['legs']) == 1
    assert [event[1] for event in events] == ['movement_blocked']
    run(operations, 6, movement_check=lambda journey, minute, kind: True, events=events)
    assert resource['position'] == expected_end
    assert [event[1] for event in events] == ['movement_blocked', 'movement_resumed']


def test_unready_dry_and_exhausted_units_cannot_dispatch():
    operations = Operations([unit('unready', available_at=5), unit('dry', water_litres=0),
                             unit('exhausted', work_remaining=0)], [], [], {})
    for identifier in ('unready', 'dry', 'exhausted'):
        assert not operations.dispatch(identifier, 's', [0, 0], route([0, 0], [1000, 0]), 0)
    assert operations.dispatch('unready', 's', [0, 0], route([0, 0], [1000, 0]), 5)


def test_unavailable_relief_becomes_available_at_roster_time():
    operations = Operations([unit('relief', state='unavailable', available_at=480)], [], [], {})
    run(operations, 479)
    assert operations.resources['relief']['state'] == 'unavailable'
    assert not operations.dispatch('relief', 's', [0, 0], route([0, 0], [1000, 0]), 479)
    run(operations, 480)
    assert operations.resources['relief']['state'] == 'available'
    assert operations.dispatch('relief', 's', [0, 0], route([0, 0], [1000, 0]), 480)


def test_total_work_budget_survives_rest_and_stops_at_exhaustion():
    resource = unit(kind='crew', position=[1000, 0], state='on_scene', due_at=0,
                    capacity_litres=0, water_litres=0, work_remaining=1,
                    rest_minutes=1, total_work_remaining=2, duty_end_at=100)
    operations = Operations([resource], [], [], {'ground_setup_minutes': 1}, return_planner=return_plan)
    effects = []
    for minute in range(3):
        run(operations, minute, effects=effects)
    crew = operations.resources['engine-1']
    assert crew['state'] == 'available'
    assert crew['total_work_remaining'] == 1
    assert len(effects) == 1
    assert operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0]), 2)
    for minute in range(3, 7):
        run(operations, minute, effects=effects)
    assert len(effects) == 2
    assert crew['total_work_remaining'] == 0
    assert crew['state'] == 'off_duty'
    assert crew['position'] == [0, 0]
    assert not operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0]), 6)
    run(operations, 7, effects=effects)
    assert len(effects) == 2
    assert crew['state'] == 'off_duty'


@pytest.mark.parametrize('kind', ['engine', 'aircraft'])
def test_absolute_duty_end_returns_from_last_position_without_work(kind):
    operations = Operations([unit(kind=kind, duty_end_at=3, total_work_remaining=10)],
                            [], [], {}, return_planner=return_plan)
    if kind == 'aircraft':
        operations.clear_aircraft('engine-1', 0)
    assert operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(3):
        run(operations, minute)
    resource = operations.resources['engine-1']
    assert resource['position'] == [1000, 0]
    assert resource['total_work_remaining'] == 10
    run(operations, 3)
    assert resource['state'] == 'returning'
    assert resource['position'] == [1000, 0]
    assert resource['journey']['origin'] == [1000, 0]
    run(operations, 4)
    assert resource['state'] == 'off_duty'
    assert resource['position'] == [0, 0]
    assert resource['total_work_remaining'] == 10
    assert not operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0]), 4)


def test_expired_duty_without_return_route_waits_for_assistance():
    operations = Operations([unit(duty_end_at=3)], [], [], {})
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0]), 0)
    for minute in range(4):
        run(operations, minute)
    resource = operations.resources['engine-1']
    assert resource['state'] == 'needs_assistance'
    assert resource['position'] == [1000, 0]
    assert not operations.dispatch('engine-1', 's', [0, 0], route([1000, 0], [2000, 0]), 3)
    assert operations.release('engine-1', route([1000, 0], [0, 0]), 3)
    run(operations, 4)
    assert resource['state'] == 'off_duty'


@pytest.mark.parametrize('kind', ['engine', 'aircraft'])
def test_refill_requires_parent_declared_compatible_facility(kind):
    resource = unit(kind=kind, state='on_scene', due_at=0, refill_compatible=False,
                    facility_id='declared-base')
    operations = Operations([resource], [], [], {})
    assert operations.release('engine-1', None, 0)
    assert operations.resources['engine-1']['state'] == 'needs_assistance'
    assert operations.resources['engine-1']['water_litres'] == 40
    assert operations.resources['engine-1']['facility_id'] == 'declared-base'
    for minute in range(3):
        run(operations, minute)
    assert operations.resources['engine-1']['water_litres'] == 40
    operations.resources['engine-1']['refill_compatible'] = True
    assert operations.release('engine-1', None, 2)
    for minute in range(3, 20):
        run(operations, minute)
    assert operations.resources['engine-1']['water_litres'] == 100


def test_expired_aircraft_leaves_refill_slot_for_next_aircraft():
    aircraft = [unit('a1', 'aircraft', state='on_scene', duty_end_at=2),
                unit('a2', 'aircraft', state='on_scene', duty_end_at=20)]
    operations = Operations(aircraft, [], [], {'air_refill_minutes': 5})
    for identifier in ('a1', 'a2'):
        operations.release(identifier, None, 0)
    run(operations, 0)
    run(operations, 1)
    assert operations.air_refill_active == 'a1'
    run(operations, 2)
    assert operations.resources['a1']['state'] == 'off_duty'
    assert operations.resources['a1']['water_litres'] == 40
    assert operations.air_refill_active == 'a2'


def test_facility_water_reserved_once_and_never_overdrawn():
    facilities = {'station': dict(position=[0, 0], allowed_kinds=['engine'], water_litres=60)}
    resources = [unit('e1', state='on_scene', due_at=0, facility_id='station'),
                 unit('e2', state='on_scene', due_at=0, facility_id='station')]
    operations = Operations(resources, [], [], {'facilities': facilities, 'ground_refill_minutes': 2})
    operations.release('e1', None, 0)
    operations.release('e2', None, 0)
    assert operations.profile['facilities']['station']['water_litres'] == 0
    assert operations.resources['e1']['state'] == 'refilling'
    assert operations.resources['e2']['state'] == 'needs_assistance'
    restored = Operations.restore(json.loads(json.dumps(operations.checkpoint())))
    for minute in range(3):
        run(operations, minute)
        run(restored, minute)
    assert operations.checkpoint() == restored.checkpoint()
    assert operations.resources['e1']['water_litres'] == 100
    assert operations.resources['e2']['water_litres'] == 40
    assert operations.profile['facilities']['station']['water_litres'] == 0


@pytest.mark.parametrize('facility', [
    dict(position=[1, 0], allowed_kinds=['engine'], water_litres=100),
    dict(position=[0.001, 0], allowed_kinds=['engine'], water_litres=100),
    dict(position=[0, 0], allowed_kinds=['aircraft'], water_litres=100),
    dict(position=[0, 0], allowed_kinds=['engine'], water_litres=59),
])
def test_incompatible_or_empty_facility_does_not_refill(facility):
    operations = Operations([unit(state='on_scene', due_at=0, facility_id='station')], [], [],
                            {'facilities': {'station': facility}})
    operations.release('engine-1', None, 0)
    assert operations.resources['engine-1']['state'] == 'needs_assistance'
    assert operations.resources['engine-1']['water_litres'] == 40


def test_air_refill_reserves_inventory_only_when_service_starts():
    facility = dict(position=[0, 0], allowed_kinds=['aircraft'], water_litres=60, refuel=True)
    aircraft = [unit('a1', 'aircraft', state='on_scene', facility_id='airfield'),
                unit('a2', 'aircraft', state='on_scene', facility_id='airfield')]
    operations = Operations(aircraft, [], [], {'facilities': {'airfield': facility}, 'air_refill_minutes': 2})
    operations.release('a1', None, 0)
    operations.release('a2', None, 0)
    assert operations.profile['facilities']['airfield']['water_litres'] == 60
    run(operations, 0)
    assert operations.profile['facilities']['airfield']['water_litres'] == 0
    assert operations.air_refill_queue == ['a2']
    run(operations, 1)
    run(operations, 2)
    assert operations.resources['a1']['water_litres'] == 100
    assert operations.resources['a2']['state'] == 'needs_assistance'
    assert operations.resources['a2']['water_litres'] == 40


def test_cancelled_refill_releases_reserved_water():
    facility = dict(position=[0, 0], allowed_kinds=['engine'], water_litres=60)
    operations = Operations([unit(state='on_scene', duty_end_at=1, facility_id='station')], [], [],
                            {'facilities': {'station': facility}, 'ground_refill_minutes': 5})
    operations.release('engine-1', None, 0)
    assert operations.profile['facilities']['station']['water_litres'] == 0
    run(operations, 0)
    run(operations, 1)
    assert operations.resources['engine-1']['state'] == 'off_duty'
    assert operations.profile['facilities']['station']['water_litres'] == 60


def test_aircraft_fuel_requires_roundtrip_and_refuels_at_base():
    aircraft = unit(kind='aircraft', water_litres=100, fuel_minutes=7,
                    fuel_capacity_minutes=8, facility_id='airfield')
    facility = dict(position=[0, 0], allowed_kinds=['aircraft'], water_litres=100, refuel=True)
    operations = Operations([aircraft], [], [], {'facilities': {'airfield': facility}, 'air_refill_minutes': 2},
                            return_planner=return_plan)
    operations.clear_aircraft('engine-1', 0)
    outbound = route([0, 0], [4000, 0], 'flight')
    assert not operations.dispatch('engine-1', 's', [0, 0], outbound, 0)
    operations.resources['engine-1']['fuel_minutes'] = 8
    assert operations.dispatch('engine-1', 's', [0, 0], outbound, 0)
    effects = []
    for minute in range(14):
        run(operations, minute, effects=effects)
    aircraft = operations.resources['engine-1']
    assert len(effects) == 1
    assert aircraft['position'] == [0, 0]
    assert aircraft['fuel_minutes'] == 8
    assert aircraft['water_litres'] == 100
    assert operations.profile['facilities']['airfield']['water_litres'] == 0


def test_fuelled_aircraft_requires_planned_recovery_and_refuel_capability():
    aircraft = unit(kind='aircraft', state='on_scene', water_litres=100,
                    fuel_minutes=5, fuel_capacity_minutes=8, facility_id='airfield')
    facility = dict(position=[0, 0], allowed_kinds=['aircraft'], water_litres=100, refuel=False)
    operations = Operations([aircraft], [], [], {'facilities': {'airfield': facility}})
    operations.release('engine-1', None, 0)
    resource = operations.resources['engine-1']
    assert resource['state'] == 'needs_assistance'
    assert resource['fuel_minutes'] == 5
    operations.clear_aircraft('engine-1', 0)
    assert not operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0], 'flight'), 0)
    assert resource['position'] == [0, 0]


def test_flight_conditions_can_be_updated_before_commands():
    operations = Operations([unit(kind='aircraft')], [], [], {})
    operations.clear_aircraft('engine-1', 0)
    run(operations, 0, daylight=True)
    operations.set_flight_conditions([0, 0], False)
    assert not operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0], 'flight'), 1)
    operations.set_flight_conditions([0, 0], True)
    assert operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0], 'flight'), 1)


def test_aircraft_skips_drop_without_return_reserve_and_never_teleports():
    aircraft = unit(kind='aircraft', water_litres=100, fuel_minutes=8, fuel_capacity_minutes=8)
    operations = Operations([aircraft], [], [], {}, return_planner=return_plan)
    operations.clear_aircraft('engine-1', 0)
    assert operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [4000, 0], 'flight'), 0)
    effects, events = [], []
    run(operations, 0)
    run(operations, 1)
    run(operations, 2, safe=lambda position, kind: False)
    for minute in range(3, 13):
        run(operations, minute, effects=effects, events=events)
    aircraft = operations.resources['engine-1']
    assert not effects
    assert aircraft['water_litres'] == 100
    assert aircraft['state'] == 'needs_assistance'
    assert aircraft['fuel_minutes'] == 0
    assert aircraft['position'] == [1000, 0]
    assert [event[1] for event in events].count('fuel_exhausted') == 1


@pytest.mark.parametrize('abort_weather', [dict(wind=[30, 30]), dict(daylight=False)])
def test_aircraft_recovers_without_new_clearance_after_abort(abort_weather):
    operations = Operations([unit(kind='aircraft')], [], [], {'air_refill_minutes': 1},
                            return_planner=return_plan)
    aircraft = operations.resources['engine-1']
    outbound = route([0, 0], [5000, 0], 'flight')
    events, effects = [], []
    assert not operations.dispatch('engine-1', 's', [0, 0], outbound, 0)
    assert operations.clear_aircraft('engine-1', 0)
    assert operations.dispatch('engine-1', 's', [0, 0], outbound, 0)
    for minute in range(4):
        run(operations, minute)
    run(operations, 4, events=events, effects=effects, **abort_weather)
    assert aircraft['position'] == [2000, 0]
    assert aircraft['state'] == 'returning'
    assert aircraft['journey']['origin'] == [2000, 0]
    assert not aircraft['clearance']
    run(operations, 5, daylight=False, safe=lambda position, kind: False, events=events, effects=effects)
    assert aircraft['position'] == [1000, 0]
    for minute in range(6, 9):
        run(operations, minute, daylight=False, safe=lambda position, kind: False,
            events=events, effects=effects)
    assert aircraft['position'] == [0, 0]
    assert aircraft['state'] == 'available'
    assert not aircraft['clearance']
    assert not effects
    assert [event[1] for event in events].count('aircraft_aborted') == 1
    assert operations.clear_aircraft('engine-1', 8)
    assert not operations.dispatch('engine-1', 's', [0, 0], outbound, 8)
    run(operations, 9)
    assert operations.dispatch('engine-1', 's', [0, 0], outbound, 9)


def test_blocked_aircraft_abort_recovers_from_last_actual_position():
    operations = Operations([unit(kind='aircraft')], [], [], {}, return_planner=return_plan)
    operations.clear_aircraft('engine-1', 0)
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0], 'flight'), 0)
    for minute in range(3):
        run(operations, minute)
    run(operations, 3, safe=lambda position, kind: False)
    assert operations.resources['engine-1']['state'] == 'blocked'
    run(operations, 4, daylight=False)
    aircraft = operations.resources['engine-1']
    assert aircraft['state'] == 'returning'
    assert aircraft['position'] == [1000, 0]
    assert aircraft['journey']['origin'] == [1000, 0]
    run(operations, 5, daylight=False)
    assert aircraft['position'] == [0, 0]


def test_aircraft_abort_uses_validated_position_and_recovery_is_exempt():
    operations = Operations([unit(kind='aircraft')], [], [], {}, return_planner=return_plan)
    operations.clear_aircraft('engine-1', 0)
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [5000, 0], 'flight'), 0)
    for minute in range(3):
        run(operations, minute)
    calls = []
    def unsafe(journey, minute, kind):
        calls.append((minute, kind))
        return False
    run(operations, 3, daylight=False, movement_check=unsafe)
    aircraft = operations.resources['engine-1']
    assert aircraft['position'] == [1000, 0]
    assert aircraft['journey']['origin'] == [1000, 0]
    run(operations, 4, daylight=False, movement_check=unsafe)
    assert aircraft['position'] == [0, 0]
    assert calls == []


def test_single_drop_and_single_server_refill_queue():
    aircraft = [unit('a1', 'aircraft'), unit('a2', 'aircraft')]
    operations = Operations(aircraft, [], [], {'air_refill_minutes': 3}, return_planner=return_plan)
    effects = []
    for identifier in ('a1', 'a2'):
        operations.clear_aircraft(identifier, 0)
        assert operations.dispatch(identifier, 's', [0, 0], route([0, 0], [1000, 0], 'flight'), 0)
    for minute in range(6):
        run(operations, minute, effects=effects)
    assert [effect['id'] for effect in effects] == ['a1', 'a2']
    assert all(operations.resources[identifier]['water_litres'] == 0 for identifier in ('a1', 'a2'))
    run(operations, 6, effects=effects)
    assert operations.air_refill_active == 'a1'
    assert operations.air_refill_queue == ['a2']
    for minute in range(7, 10):
        run(operations, minute, effects=effects)
    assert operations.air_refill_active == 'a2'
    assert operations.resources['a1']['water_litres'] == 100
    assert operations.resources['a2']['water_litres'] == 0
    assert len(effects) == 2


def test_evacuation_reservations_blocking_relocation_and_exposure():
    operations = Operations([], [destination(), destination('d2', position=[2000, 0])],
                            [group(), group('g2', count=2)], {})
    first = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [1000, 0]), 0)
    with pytest.raises(ValueError):
        operations.protect('g2', 'evacuate', 'd1', route([0, 0], [1000, 0]), 0)
    run(operations, 0)
    run(operations, 1)
    assert operations.orders[first]['state'] == 'delivered'
    run(operations, 2)
    assert operations.orders[first]['state'] == 'acknowledged'
    run(operations, 3)
    run(operations, 4, safe=lambda position, kind: position != [1000, 0])
    assert operations.groups['g1']['position'] == [0, 0]
    assert operations.groups['g1']['status'] == 'blocked'
    assert not operations.groups['g1']['exposed']
    run(operations, 5)
    assert operations.groups['g1']['status'] == 'arrived'
    run(operations, 6, safe=lambda position, kind: position != [1000, 0])
    assert not operations.groups['g1']['exposed']
    run(operations, 7, exposure_check=lambda record: record['position'] == [1000, 0])
    assert operations.groups['g1']['exposed']
    operations.protect('g1', 'evacuate', 'd2', route([1000, 0], [2000, 0]), 7)
    with pytest.raises(ValueError):
        operations.protect('g2', 'evacuate', 'd1', route([0, 0], [1000, 0]), 7)
    for minute in range(8, 11):
        run(operations, minute)
    operations.protect('g2', 'evacuate', 'd1', route([0, 0], [1000, 0]), 10)
    for minute in range(11, 15):
        run(operations, minute)
    assert operations.groups['g1']['destination_id'] == 'd2'
    assert operations.groups['g2']['destination_id'] == 'd1'


def test_evacuation_block_emits_once_and_resumes_without_teleport():
    operations = Operations([], [destination(position=[4000, 0])], [group()], {})
    events = []
    order_id = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [4000, 0]), 0)
    for minute in range(5):
        run(operations, minute, events=events)
    assert operations.groups['g1']['position'] == [1000, 0]
    original_legs = json.loads(json.dumps(operations.orders[order_id]['journey']['legs']))
    for minute in (5, 6):
        run(operations, minute, safe=lambda position, kind: False, events=events)
        assert operations.groups['g1']['status'] == 'blocked'
        assert operations.groups['g1']['position'] == [1000, 0]
    assert [event[1] for event in events].count('movement_blocked') == 1
    run(operations, 7, events=events)
    assert operations.groups['g1']['position'] == [2000, 0]
    assert operations.orders[order_id]['journey']['legs'] == original_legs
    assert [event[1] for event in events].count('movement_resumed') == 1
    for minute in (8, 9):
        run(operations, minute, events=events)
    assert operations.groups['g1']['position'] == [4000, 0]
    assert operations.groups['g1']['status'] == 'arrived'


@pytest.mark.parametrize('fresh_origin', [False, True])
def test_pending_redirect_preserves_active_trip_until_execution(fresh_origin):
    operations = Operations([], [destination('d1', position=[8000, 0]),
                                 destination('d2', position=[4000, 5000])], [group()], {})
    events = []
    original = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [8000, 0]), 0)
    for minute in range(5):
        run(operations, minute, events=events)
    origin = [4000, 0] if fresh_origin else [1000, 0]
    redirect = operations.protect('g1', 'evacuate', 'd2', route(origin, [4000, 5000]), 4)
    assert operations.groups['g1']['order_id'] == original
    assert operations.groups['g1']['pending_order_id'] == redirect
    assert operations.reservations['g1'] == 'd1'
    restored = Operations.restore(json.loads(json.dumps(operations.checkpoint())))
    for minute in range(5, 8):
        run(operations, minute, events=events)
        run(restored, minute)
    assert operations.checkpoint() == restored.checkpoint()
    assert operations.groups['g1']['position'] == [4000, 0]
    if fresh_origin:
        assert operations.orders[original]['state'] == 'cancelled'
        assert operations.orders[redirect]['state'] == 'executing'
        assert operations.groups['g1']['order_id'] == redirect
        assert operations.reservations['g1'] == 'd2'
        run(operations, 8, events=events)
        assert operations.groups['g1']['position'] == [4000, 1000]
    else:
        assert operations.orders[original]['state'] == 'executing'
        assert operations.orders[redirect]['state'] == 'unable_to_comply'
        assert operations.groups['g1']['order_id'] == original
        assert operations.reservations['g1'] == 'd1'
        for minute in range(8, 12):
            run(operations, minute, events=events)
        assert operations.groups['g1']['destination_id'] == 'd1'
        assert [event[1] for event in events].count('unable_to_comply') == 1


def test_pending_redirect_rechecks_capacity_without_losing_active_reservation():
    operations = Operations([], [destination('d1', position=[8000, 0]),
                                 destination('d2', position=[4000, 5000])],
                            [group(), group('g2', count=2)], {})
    original = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [8000, 0]), 0)
    for minute in range(5):
        run(operations, minute)
    redirect = operations.protect('g1', 'evacuate', 'd2', route([4000, 0], [4000, 5000]), 4)
    operations.protect('g2', 'evacuate', 'd2', route([0, 0], [4000, 5000]), 4)
    for minute in range(5, 8):
        run(operations, minute)
    assert operations.orders[redirect]['state'] == 'unable_to_comply'
    assert operations.orders[original]['state'] == 'executing'
    assert operations.reservations == {'g1': 'd1', 'g2': 'd2'}
    assert operations.groups['g1']['position'] == [4000, 0]


def test_pending_shelter_uses_location_at_execution():
    operations = Operations([], [destination(position=[4000, 0])], [group()], {})
    original = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [4000, 0]), 0)
    for minute in range(5):
        run(operations, minute)
    pending = operations.protect('g1', 'shelter', None, None, 4)
    for minute in range(5, 8):
        run(operations, minute)
    assert operations.orders[original]['state'] == 'completed'
    assert operations.orders[pending]['state'] == 'completed'
    assert operations.groups['g1']['status'] == 'sheltering'
    assert operations.groups['g1']['destination_id'] == 'd1'
    assert operations._occupancy('d1') == 4


def test_evacuation_swept_check_blocks_intermediate_hazard():
    operations = Operations([], [destination(position=[3000, 0])], [group()], {})
    events = []
    operations.protect('g1', 'evacuate', 'd1', route([0, 0], [3000, 0]), 0)
    for minute in range(5):
        run(operations, minute)
    segments = []
    def check(journey, minute, kind):
        start = journey_position(journey, minute - 1)[0]
        end = journey_position(journey, minute)[0]
        segments.append((start, end, minute, kind))
        return False
    run(operations, 5, movement_check=check, events=events)
    assert segments == [([1000, 0], [2000, 0], 5, 'group')]
    assert operations.groups['g1']['position'] == [1000, 0]
    assert operations.groups['g1']['status'] == 'blocked'
    assert not operations.groups['g1']['exposed']
    run(operations, 6, movement_check=lambda journey, minute, kind: True, events=events)
    assert operations.groups['g1']['position'] == [2000, 0]
    assert [event[1] for event in events] == ['movement_blocked', 'movement_resumed']


def test_shelter_order_fails_on_road_but_succeeds_at_home_or_destination():
    operations = Operations([], [destination(position=[8000, 0])],
                            [group(), group('home', count=1),
                             group('arrived', count=1, position=[8000, 0],
                                   destination_id='d1', status='arrived')], {})
    events = []
    evacuation = operations.protect('g1', 'evacuate', 'd1', route([0, 0], [8000, 0]), 0)
    for minute in range(5):
        run(operations, minute, events=events)
    shelter = operations.protect('g1', 'shelter', None, None, 4)
    home_order = operations.protect('home', 'shelter', None, None, 4)
    arrived_order = operations.protect('arrived', 'shelter', None, None, 4)
    for minute in range(5, 8):
        run(operations, minute, events=events)
    assert operations.orders[evacuation]['state'] == 'executing'
    assert operations.orders[shelter]['state'] == 'unable_to_comply'
    assert operations.groups['g1']['status'] == 'evacuating'
    assert operations.groups['g1']['position'] == [4000, 0]
    assert operations.reservations['g1'] == 'd1'
    assert operations.orders[home_order]['state'] == 'completed'
    assert operations.orders[arrived_order]['state'] == 'completed'
    reports = [event for event in events if event[1] == 'unable_to_comply']
    assert len(reports) == 1
    assert reports[0][2]['order_id'] == shelter
    assert reports[0][3]['actionable']


def test_checkpoint_round_trip_and_defensive_snapshot():
    operations = Operations([unit()], [destination()], [group()], {}, return_planner=return_plan)
    operations.protect('g1', 'evacuate', 'd1', route([0, 0], [1000, 0]), 0)
    operations.dispatch('engine-1', 's', [0, 0], route([0, 0], [1000, 0]), 0)
    run(operations, 0)
    snapshot = operations.snapshot()
    snapshot['resources']['engine-1']['position'][0] = 999
    assert operations.resources['engine-1']['position'] == [0, 0]
    restored = Operations.restore(json.loads(json.dumps(operations.checkpoint())), return_planner=return_plan)
    for minute in range(1, 10):
        run(operations, minute)
        run(restored, minute)
    assert restored.checkpoint() == operations.checkpoint()
