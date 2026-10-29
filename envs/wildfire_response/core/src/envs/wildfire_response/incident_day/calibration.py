"""Small synthetic mechanism experiments; measurements are not physical validation."""
from .operations import Operations
from wildfire_core.mobility_routing import make_journey, make_leg

VERSION = 'incident-day-calibration-probes-v1'


def route(start, end, mode='road'):
    return make_journey([make_leg(mode, [start, end], 60)], start, end, 'synthetic probe')


def unit(kind='engine', **changes):
    value = dict(id='unit', kind=kind, home_base='base', position=[0, 0], base_position=[0, 0],
                 capacity_litres=100, water_litres=40, state='available', available_at=0,
                 work_remaining=2, rest_minutes=2)
    value.update(changes)
    return value


def group(identifier='g', **changes):
    value = dict(id=identifier, count=4, position=[0, 0], home=[0, 0], status='at_home',
                 exposed=False, destination_id=None)
    value.update(changes)
    return value


def destination(identifier, position, capacity=5):
    return dict(id=identifier, name=identifier, position=position, capacity=capacity, status='open')


def return_plan(resource, minute):
    return route(resource['position'], resource['base_position'],
                 'flight' if resource['kind'] == 'aircraft' else 'road')


def tick(ops, minute, *, blocked=False, daylight=True, wind=0, exposed=False):
    effects = []
    ops.tick(minute, wind=wind, daylight=daylight, hazard_check=lambda pos, kind: not blocked,
             suppress=effects.append, emit=lambda *args, **kwargs: None,
             return_planner=return_plan, exposure_check=lambda record: exposed and record['position'] == [0, 0])
    return effects


def ground_probe(setup, block_minute=None, stock=40, duty=None, supply=100):
    changes = dict(water_litres=stock)
    if duty is not None:
        changes['duty_end_at'] = duty
    changes['facility_id'] = 'base'
    ops = Operations([unit(**changes)], [], [], {'ground_setup_minutes': setup,
                     'ground_refill_minutes': 2, 'engine_litres_per_minute': 20,
                     'facilities': {'base': dict(position=[0, 0], allowed_kinds=['engine'], water_litres=supply)}},
                     return_planner=return_plan)
    ops.dispatch('unit', 'sector', [1, 1], route([0, 0], [1000, 0]), 0)
    trace, work = [], []
    for minute in range(24):
        effects = tick(ops, minute, blocked=minute == block_minute)
        resource = ops.resources['unit']
        trace.append(dict(minute=minute, state=resource['state'], position=resource['position'][:],
                          water=resource['water_litres'], work_remaining=resource['work_remaining']))
        work.extend([minute] * len(effects))
    return dict(work_minutes=work, trace=trace, remaining_supply=ops.profile['facilities']['base']['water_litres'])


def aircraft_probe(daylight_at_drop, windy_at_drop=False):
    ops = Operations([unit('aircraft', water_litres=100, work_remaining=10)], [], [],
                     {'aircraft_setup_minutes': 2, 'aircraft_max_wind': 2.8}, return_planner=return_plan)
    ops.clear_aircraft('unit', 0)
    ops.dispatch('unit', 'sector', [1, 1], route([0, 0], [1000, 0], 'flight'), 0)
    trace, drops = [], []
    for minute in range(12):
        effects = tick(ops, minute, daylight=daylight_at_drop or minute < 4,
                       wind=3 if windy_at_drop and minute >= 4 else 0)
        drops.extend([minute] * len(effects))
        resource = ops.resources['unit']
        trace.append(dict(minute=minute, state=resource['state'], water=resource['water_litres']))
    return dict(drops=drops, trace=trace)


def aircraft_fuel_probe(fuel):
    ops = Operations([unit('aircraft', water_litres=100, work_remaining=10,
                           fuel_minutes=fuel, fuel_capacity_minutes=10)], [], [], {}, return_planner=return_plan)
    ops.clear_aircraft('unit', 0)
    return dict(fuel_minutes=fuel, dispatched=ops.dispatch('unit', 'sector', [1, 1],
                route([0, 0], [1000, 0], 'flight'), 0))


def protection_probe(issue_minute):
    """Compare order timing with a toy home-only hazard, not a fire or health model."""
    ops = Operations([], [destination('a', [1000, 0]), destination('b', [2000, 0])],
                     [group(), group('other')], {})
    trace, order, capacity_rejected = [], None, None
    for minute in range(issue_minute + 12):
        if minute == issue_minute:
            order = ops.protect('g', 'evacuate', 'a', route([0, 0], [1000, 0]), minute)
            try:
                ops.protect('other', 'evacuate', 'a', route([0, 0], [1000, 0]), minute)
                capacity_rejected = False
            except ValueError:
                capacity_rejected = True
        tick(ops, minute, exposed=minute == 4 and ops.groups['g']['position'] == [0, 0])
        if minute == issue_minute + 6 and ops.groups['g']['status'] == 'arrived':
            ops.protect('g', 'evacuate', 'b', route([1000, 0], [2000, 0]), minute)
        resident_group = ops.groups['g']
        trace.append(dict(minute=minute, status=resident_group['status'], position=resident_group['position'][:],
                          exposed=resident_group['exposed'], destination_id=resident_group.get('destination_id')))
    return dict(capacity_rejected=capacity_rejected, initial_order=order, trace=trace,
                reservations=ops.reservations.copy())


def run_probes():
    return dict(version=VERSION, scope='deterministic synthetic mechanics, not calibrated real-world physics',
                ground_setup_short=ground_probe(1), ground_setup_long=ground_probe(5),
                ground_blocked=ground_probe(1, block_minute=2),
                ground_low_stock=ground_probe(1, stock=20), ground_low_supply=ground_probe(1, supply=50),
                ground_duty_limit=ground_probe(1, duty=4),
                aircraft_open=aircraft_probe(True), aircraft_window_closes=aircraft_probe(False),
                aircraft_wind_increases=aircraft_probe(True, windy_at_drop=True),
                aircraft_low_fuel=aircraft_fuel_probe(1), aircraft_sufficient_fuel=aircraft_fuel_probe(2),
                protection_early=protection_probe(0), protection_late=protection_probe(5))
