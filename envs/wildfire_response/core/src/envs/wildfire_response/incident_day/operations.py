"""Finite incident-day resources and protection orders.

Coordinates and journeys use the native mobility routing contract. The caller
owns the clock, hazard, suppression effects, route planning and presentation.
An aircraft's supplied return route abstracts recovery to its declared base:
once returning, weather, clearance and ground hazard buffers do not delay it.
This is an exercise assumption, not a flight-safety model.
"""

import copy
import math

from wildfire_core.mobility_routing import journey_position


def _same_position(first, second):
    return (isinstance(first, (list, tuple)) and isinstance(second, (list, tuple))
            and len(first) == len(second) == 2
            and all(type(value) in (int, float) and math.isfinite(value)
                    for value in (*first, *second))
            and math.dist(first, second) < 0.01)


def _route(journey, origin, destination, minute):
    if not isinstance(journey, dict):
        return None
    result = copy.deepcopy(journey)
    try:
        duration = result['duration_minutes']
        legs = result['legs']
        if (type(duration) not in (int, float) or not math.isfinite(duration)
                or duration <= 0 or not isinstance(legs, list)
                or not _same_position(result['origin'], origin)
                or not _same_position(result['destination'], destination)):
            return None
        previous = result['origin']
        total = 0
        for leg in legs:
            coordinates = leg['coordinates']
            leg_duration = leg['duration_minutes']
            if (not isinstance(coordinates, list) or len(coordinates) < 2
                    or not _same_position(previous, coordinates[0])
                    or any(not _same_position(point, point) for point in coordinates)
                    or type(leg_duration) not in (int, float)
                    or not math.isfinite(leg_duration) or leg_duration <= 0):
                return None
            total += leg_duration
            previous = coordinates[-1]
        if ((legs and (not _same_position(previous, destination)
                       or not math.isclose(total, duration, abs_tol=1e-5)))
                or (not legs and not _same_position(origin, destination))):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    result['departed_at'] = minute
    result['arrives_at'] = minute + duration
    return result


def _movement_safe(journey, minute, kind, position, hazard_check, movement_check):
    return (hazard_check(copy.deepcopy(position), kind)
            and (movement_check is None or movement_check(copy.deepcopy(journey), minute, kind)))


def _duty_finished(unit, minute):
    return ((unit.get('duty_end_at') is not None and minute >= unit['duty_end_at'])
            or (unit.get('total_work_remaining') is not None and unit['total_work_remaining'] <= 0))


class Operations:
    def __init__(self, resources, destinations, groups, profile, *, return_planner=None):
        self.resources = self._index(resources)
        self.destinations = self._index(destinations)
        self.groups = self._index(groups)
        self.profile = copy.deepcopy(profile)
        self.return_planner = return_planner
        self.orders = {}
        self.reservations = {}
        self.air_refill_queue = []
        self.air_refill_active = None
        self.order_serial = 0
        self.last_minute = None
        self.air_window_open = None
        for unit in self.resources.values():
            unit.setdefault('state', 'available')
            unit.setdefault('available_at', 0)
            unit.setdefault('work_remaining', 240)
            unit.setdefault('rest_minutes', 60)
            unit.setdefault('water_litres', unit.get('capacity_litres', 0))
            unit.setdefault('capacity_litres', 0)
            unit.setdefault('clearance', False)
            unit.setdefault('journey', None)
            if unit['kind'] == 'aircraft' and ('fuel_minutes' in unit or 'fuel_capacity_minutes' in unit):
                unit.setdefault('fuel_capacity_minutes', 180)
                unit.setdefault('fuel_minutes', unit['fuel_capacity_minutes'])
                if (any(type(unit[key]) not in (int, float) or not math.isfinite(unit[key])
                        for key in ('fuel_minutes', 'fuel_capacity_minutes'))
                        or unit['fuel_capacity_minutes'] <= 0
                        or not 0 <= unit['fuel_minutes'] <= unit['fuel_capacity_minutes']):
                    raise ValueError('Invalid aircraft fuel budget')
        for group in self.groups.values():
            group.setdefault('status', 'at_home')
            group.setdefault('exposed', False)
            group.setdefault('destination_id', None)
            destination = group['destination_id']
            if destination is not None:
                if destination not in self.destinations:
                    raise ValueError('Unknown initial destination')
                self.reservations[group['id']] = destination
        for destination in self.destinations.values():
            if self._occupancy(destination['id']) > destination['capacity']:
                raise ValueError('Initial destination capacity exceeded')

    @staticmethod
    def _index(items):
        result = {}
        for item in items:
            value = copy.deepcopy(item)
            if value['id'] in result:
                raise ValueError('Duplicate identifier')
            result[value['id']] = value
        return result

    def _occupancy(self, destination_id, excluding=None):
        return sum(group['count'] for group_id, group in self.groups.items()
                   if group_id != excluding and
                   (self.reservations.get(group_id) == destination_id
                    or group.get('destination_id') == destination_id))

    def _time(self, minute):
        if type(minute) is not int or (self.last_minute is not None and minute < self.last_minute):
            raise ValueError('Minute must be an integer and not precede the last tick')

    def clear_aircraft(self, resource_id, minute):
        self._time(minute)
        unit = self.resources[resource_id]
        if unit['kind'] != 'aircraft':
            return False
        unit['clearance'] = True
        return True

    def dispatch(self, resource_id, sector_id, target_grid, journey, minute):
        self._time(minute)
        unit = self.resources.get(resource_id)
        if (unit is None or unit['state'] not in ('available', 'blocked', 'needs_assistance')
                or unit['available_at'] > minute
                or unit['work_remaining'] <= 0
                or _duty_finished(unit, minute)
                or (unit['kind'] in ('engine', 'aircraft') and unit['water_litres'] <= 0)
                or (unit['kind'] == 'aircraft'
                    and (not unit['clearance'] or self.air_window_open is False))):
            return False
        plan = _route(journey, unit['position'], journey.get('destination') if isinstance(journey, dict) else None, minute)
        if plan is None:
            return False
        if unit['kind'] == 'aircraft' and 'fuel_minutes' in unit:
            if _same_position(plan['destination'], unit['base_position']):
                return_duration = 0
            else:
                if self.return_planner is None:
                    return False
                projected = copy.deepcopy(unit)
                projected.update(position=copy.deepcopy(plan['destination']), state='on_scene', journey=plan)
                returning = self.return_planner(projected, minute + self.profile.get('mobilization_minutes', 1)
                                                + plan['duration_minutes'])
                returning = _route(returning, plan['destination'], unit['base_position'], minute)
                if returning is None:
                    return False
                return_duration = math.ceil(returning['duration_minutes'])
            if unit['fuel_minutes'] < math.ceil(plan['duration_minutes']) + return_duration:
                return False
        unit.pop('blocked_state', None)
        unit.update(state='mobilizing', sector_id=sector_id, target_grid=copy.deepcopy(target_grid),
                    journey=plan, due_at=minute + self.profile.get('mobilization_minutes', 1))
        return True

    def _current_position(self, unit, minute):
        if unit.get('journey') and unit['state'] in ('travel', 'returning'):
            return journey_position(unit['journey'], minute)[0]
        return copy.deepcopy(unit['position'])

    def _return(self, unit, journey, minute, planner=None):
        unit['position'] = self._current_position(unit, minute)
        unit.pop('blocked_state', None)
        if journey is None and planner is not None:
            journey = planner(copy.deepcopy(unit), minute)
        plan = _route(journey, unit['position'], unit['base_position'], minute)
        if _same_position(unit['position'], unit['base_position']):
            unit['journey'] = None
            self._at_base(unit, minute)
            return True
        unit['journey'] = plan
        if plan is None:
            unit['state'] = 'needs_assistance'
            return False
        unit.pop('duty_return_attempted', None)
        unit['state'] = 'returning'
        return True

    def release(self, resource_id, journey, minute):
        self._time(minute)
        unit = self.resources[resource_id]
        if unit['state'] in ('available', 'refilling', 'resting', 'air_refill_queue', 'off_duty'):
            return False
        return self._return(unit, journey, minute, self.return_planner)

    def protect(self, group_id, action, destination_id, journey, minute):
        self._time(minute)
        group = self.groups[group_id]
        if action not in ('evacuate', 'shelter'):
            raise ValueError('Unsupported protection action')
        if action == 'evacuate':
            destination = self.destinations.get(destination_id)
            if (destination is None or destination['status'] != 'open'
                    or self._occupancy(destination_id, group_id) + group['count'] > destination['capacity']):
                raise ValueError('Destination unavailable or capacity exceeded')
            plan = _route(journey, group['position'], destination['position'], minute)
            if plan is None:
                raise ValueError('A valid routed journey is required')
        else:
            if destination_id is not None or journey is not None:
                raise ValueError('Shelter does not take a destination or journey')
            plan = None
        old_order = group.get('order_id')
        if old_order in self.orders and self.orders[old_order]['state'] not in ('completed', 'cancelled'):
            self.orders[old_order]['state'] = 'cancelled'
        if action == 'evacuate':
            self.reservations[group_id] = destination_id
        else:
            self.reservations.pop(group_id, None)
        self.order_serial += 1
        order_id = f'order-{self.order_serial}'
        self.orders[order_id] = dict(id=order_id, group_id=group_id, action=action,
                                     destination_id=destination_id, journey=plan, state='issued',
                                     issued_at=minute, due_at=minute + self.profile.get('order_delivery_minutes', 1))
        group['order_id'] = order_id
        return order_id

    def _at_base(self, unit, minute):
        unit['position'] = copy.deepcopy(unit['base_position'])
        unit['journey'] = None
        if _duty_finished(unit, minute):
            self._off_duty(unit)
            return
        needs_water = unit['water_litres'] < unit['capacity_litres']
        needs_fuel = unit['kind'] == 'aircraft' and unit.get('fuel_minutes', 0) < unit.get('fuel_capacity_minutes', 0)
        if unit['kind'] in ('engine', 'aircraft') and (needs_water or needs_fuel):
            if not self._refill_valid(unit):
                unit['state'] = 'needs_assistance'
                return
        if unit['kind'] == 'aircraft' and (needs_water or needs_fuel):
            unit['state'] = 'air_refill_queue'
            if unit['id'] not in self.air_refill_queue and self.air_refill_active != unit['id']:
                self.air_refill_queue.append(unit['id'])
        elif unit['kind'] == 'engine' and needs_water:
            if self._reserve_refill(unit):
                unit.update(state='refilling', due_at=minute + self.profile.get('ground_refill_minutes', 10))
            else:
                unit['state'] = 'needs_assistance'
        else:
            self._rest_or_available(unit, minute)

    def _refill_valid(self, unit):
        if unit.get('refill_compatible', True) is not True:
            return False
        facilities = self.profile.get('facilities')
        if facilities is None:
            return True
        if not isinstance(facilities, dict):
            return False
        facility = facilities.get(unit.get('facility_id'))
        if (not isinstance(facility, dict) or not _same_position(facility.get('position'), facility.get('position'))
                or list(facility['position']) != list(unit['base_position'])
                or unit['kind'] not in facility.get('allowed_kinds', [])):
            return False
        water = facility.get('water_litres')
        if type(water) not in (int, float) or not math.isfinite(water) or water < 0:
            return False
        if (unit['kind'] == 'aircraft' and unit.get('fuel_minutes', 0) < unit.get('fuel_capacity_minutes', 0)
                and facility.get('refuel') is not True):
            return False
        needed = max(0, unit['capacity_litres'] - unit['water_litres'])
        reservation = unit.get('refill_reservation')
        if reservation is not None and reservation['facility_id'] == unit.get('facility_id'):
            needed = max(0, needed - reservation['water_litres'])
        return water >= needed

    def _reserve_refill(self, unit):
        if not self._refill_valid(unit):
            return False
        facilities = self.profile.get('facilities')
        if facilities is not None:
            amount = max(0, unit['capacity_litres'] - unit['water_litres'])
            facility_id = unit['facility_id']
            facilities[facility_id]['water_litres'] -= amount
            unit['refill_reservation'] = {'facility_id': facility_id, 'water_litres': amount}
        return True

    def _cancel_refill(self, unit):
        reservation = unit.pop('refill_reservation', None)
        if reservation is not None:
            self.profile['facilities'][reservation['facility_id']]['water_litres'] += reservation['water_litres']

    def _complete_refill(self, unit, minute):
        unit.pop('refill_reservation', None)
        unit['water_litres'] = unit['capacity_litres']
        if 'fuel_minutes' in unit:
            unit['fuel_minutes'] = unit['fuel_capacity_minutes']
        self._rest_or_available(unit, minute)

    def _rest_or_available(self, unit, minute):
        if _duty_finished(unit, minute):
            self._off_duty(unit)
        elif unit['work_remaining'] <= 0:
            unit.update(state='resting', due_at=minute + unit['rest_minutes'])
        else:
            unit.update(state='available', available_at=minute)

    def _off_duty(self, unit):
        self._cancel_refill(unit)
        self.air_refill_queue = [identifier for identifier in self.air_refill_queue if identifier != unit['id']]
        if self.air_refill_active == unit['id']:
            self.air_refill_active = None
        unit.pop('blocked_state', None)
        unit.pop('duty_return_attempted', None)
        unit.update(state='off_duty', journey=None)

    def _flight_window(self, wind, daylight):
        speed = math.hypot(*wind) if isinstance(wind, (list, tuple)) and len(wind) == 2 else wind
        return bool(daylight) and speed <= self.profile.get('aircraft_max_wind', 40)

    def tick(self, minute, *, wind, daylight, hazard_check, suppress, emit, return_planner=None,
             exposure_check=None, movement_check=None):
        """Check each proposed step with movement_check(journey_copy, minute, kind).

        The callback checks the swept segment ending at minute and returns true
        when safe. Aircraft recovery is exempt from both movement checks.
        """
        self._time(minute)
        if self.last_minute is not None and minute == self.last_minute:
            raise ValueError('Each minute may be ticked only once')
        planner = return_planner if return_planner is not None else self.return_planner
        start = minute if self.last_minute is None else self.last_minute + 1
        for current in range(start, minute + 1):
            self._tick_minute(current, wind, daylight, hazard_check, suppress, emit, planner,
                              exposure_check, movement_check)
        self.last_minute = minute

    def _tick_minute(self, minute, wind, daylight, hazard_check, suppress, emit, planner,
                     exposure_check, movement_check):
        self.air_window_open = self._flight_window(wind, daylight)
        for unit in self.resources.values():
            state = unit['state']
            movement_state = unit.get('blocked_state') if state == 'blocked' else state
            if _duty_finished(unit, minute):
                if _same_position(unit['position'], unit['base_position']):
                    self._off_duty(unit)
                    continue
                if movement_state != 'returning':
                    if state == 'needs_assistance' and unit.get('duty_return_attempted'):
                        continue
                    if state == 'travel':
                        unit.update(state='blocked', blocked_state='travel')
                    if not self._return(unit, None, minute, planner):
                        unit['duty_return_attempted'] = True
                    continue
            if unit['kind'] == 'aircraft' and movement_state in ('travel', 'returning') and 'fuel_minutes' in unit:
                if unit['fuel_minutes'] < 1:
                    unit.update(state='needs_assistance', journey=None)
                    unit.pop('blocked_state', None)
                    emit('operations', 'fuel_exhausted', {'resource_id': unit['id'],
                         'position': copy.deepcopy(unit['position'])}, urgent=True)
                    continue
                unit['fuel_minutes'] -= 1
            if unit['kind'] == 'aircraft' and movement_state in ('mobilizing', 'travel', 'on_scene', 'working'):
                if not unit['clearance'] or not self.air_window_open:
                    if state == 'travel':
                        position = self._current_position(unit, minute)
                        if not _movement_safe(unit['journey'], minute, unit['kind'], position,
                                              hazard_check, movement_check):
                            unit.update(state='blocked', blocked_state='travel')
                    unit['clearance'] = False
                    self._return(unit, None, minute, planner)
                    emit('operations', 'aircraft_aborted', {'resource_id': unit['id']}, urgent=True)
                    continue
            if state == 'unavailable' and minute >= unit['available_at']:
                unit['state'] = 'available'
            elif state == 'mobilizing' and minute >= unit['due_at']:
                unit['state'] = 'travel'
                unit['journey']['departed_at'] = minute
                unit['journey']['arrives_at'] = minute + unit['journey']['duration_minutes']
            elif movement_state in ('travel', 'returning'):
                if unit['kind'] == 'aircraft' and movement_state == 'returning' and not self.air_window_open:
                    unit['clearance'] = False
                position = journey_position(unit['journey'], minute)[0]
                if not (unit['kind'] == 'aircraft' and movement_state == 'returning') and not _movement_safe(
                        unit['journey'], minute, unit['kind'], position, hazard_check, movement_check):
                    unit['journey']['departed_at'] += 1
                    unit['journey']['arrives_at'] += 1
                    if state != 'blocked':
                        unit.update(state='blocked', blocked_state=movement_state)
                        emit('operations', 'movement_blocked',
                             {'resource_id': unit['id'], 'movement_state': movement_state,
                              'position': copy.deepcopy(unit['position'])}, actionable=True)
                    continue
                unit['position'] = position
                if state == 'blocked':
                    unit['state'] = movement_state
                    unit.pop('blocked_state', None)
                    emit('operations', 'movement_resumed',
                         {'resource_id': unit['id'], 'movement_state': movement_state,
                          'position': copy.deepcopy(position)}, actionable=True)
                if minute >= unit['journey']['arrives_at']:
                    if movement_state == 'returning':
                        self._at_base(unit, minute)
                    else:
                        unit.update(state='on_scene', due_at=minute + self.profile.get(
                            'aircraft_setup_minutes' if unit['kind'] == 'aircraft' else 'ground_setup_minutes',
                            2 if unit['kind'] == 'aircraft' else 5))
            elif state == 'on_scene' and minute >= unit['due_at']:
                unit['state'] = 'working'
            elif state == 'refilling' and unit['kind'] != 'aircraft' and minute >= unit['due_at']:
                if not self._refill_valid(unit):
                    self._cancel_refill(unit)
                    unit['state'] = 'needs_assistance'
                else:
                    self._complete_refill(unit, minute)
            elif state == 'resting' and minute >= unit['due_at']:
                unit['work_remaining'] = self.profile.get('duty_minutes', 240)
                unit.update(state='available', available_at=minute)
            if unit['state'] == 'working':
                if (unit['work_remaining'] <= 0 or
                        (unit['kind'] in ('engine', 'aircraft') and unit['water_litres'] <= 0)
                        or not hazard_check(copy.deepcopy(unit['position']), unit['kind'])):
                    self._return(unit, None, minute, planner)
                    continue
                return_journey = None
                if (unit['kind'] == 'aircraft' and 'fuel_minutes' in unit
                        and not _same_position(unit['position'], unit['base_position'])):
                    if planner is not None:
                        return_journey = _route(planner(copy.deepcopy(unit), minute), unit['position'],
                                                unit['base_position'], minute)
                    if return_journey is None or unit['fuel_minutes'] < math.ceil(return_journey['duration_minutes']):
                        self._return(unit, return_journey, minute, None)
                        continue
                suppress(copy.deepcopy(unit))
                unit['work_remaining'] -= 1
                if unit.get('total_work_remaining') is not None:
                    unit['total_work_remaining'] -= 1
                if unit['kind'] == 'aircraft':
                    unit['water_litres'] = 0
                elif unit['kind'] == 'engine':
                    unit['water_litres'] = max(0, unit['water_litres'] - self.profile.get('engine_litres_per_minute', 20))
                if (_duty_finished(unit, minute) or unit['work_remaining'] <= 0
                        or (unit['kind'] in ('engine', 'aircraft') and unit['water_litres'] <= 0)):
                    self._return(unit, return_journey, minute, planner)

        if self.air_refill_active is not None:
            unit = self.resources[self.air_refill_active]
            if not self._refill_valid(unit):
                self._cancel_refill(unit)
                self.air_refill_active = None
                unit['state'] = 'needs_assistance'
            elif minute >= unit['due_at']:
                self.air_refill_active = None
                self._complete_refill(unit, minute)
        if self.air_refill_active is None and self.air_refill_queue:
            identifier = self.air_refill_queue.pop(0)
            unit = self.resources[identifier]
            if not self._reserve_refill(unit):
                unit['state'] = 'needs_assistance'
            else:
                unit.update(state='refilling', due_at=minute + self.profile.get('air_refill_minutes', 15))
                self.air_refill_active = identifier

        for group in self.groups.values():
            order = self.orders.get(group.get('order_id'))
            if order is None:
                continue
            if order['state'] == 'issued' and minute >= order['due_at']:
                order.update(state='delivered', delivered_at=minute,
                             due_at=minute + self.profile.get('order_ack_minutes', 1))
                emit('operations', 'order_delivered', {'order_id': order['id'], 'group_id': group['id']})
            elif order['state'] == 'delivered' and minute >= order['due_at']:
                order.update(state='acknowledged', acknowledged_at=minute,
                             due_at=minute + self.profile.get('order_execution_minutes', 1))
                emit('operations', 'order_acknowledged', {'order_id': order['id'], 'group_id': group['id']})
            elif order['state'] == 'acknowledged' and minute >= order['due_at']:
                if order['action'] == 'shelter':
                    destination = self.destinations.get(group.get('destination_id'))
                    if (_same_position(group['position'], group.get('home')) or
                            (destination is not None and
                             _same_position(group['position'], destination['position']))):
                        group['status'] = 'sheltering'
                        order.update(state='completed', completed_at=minute)
                    else:
                        group['status'] = 'unable_to_comply'
                        order.update(state='unable_to_comply', completed_at=minute)
                        emit('operations', 'unable_to_comply',
                             {'order_id': order['id'], 'group_id': group['id'],
                              'position': copy.deepcopy(group['position'])}, actionable=True)
                elif self.destinations[order['destination_id']]['status'] != 'open':
                    order['state'] = 'blocked'
                    group['status'] = 'blocked'
                    emit('operations', 'movement_blocked',
                         {'order_id': order['id'], 'group_id': group['id'],
                          'position': copy.deepcopy(group['position'])}, actionable=True)
                else:
                    order.update(state='executing', started_at=minute)
                    order['journey']['departed_at'] = minute
                    order['journey']['arrives_at'] = minute + order['journey']['duration_minutes']
                    group.update(status='evacuating', destination_id=None)
            elif order['state'] in ('executing', 'blocked') and order['action'] == 'evacuate':
                route = order['journey']
                if order['state'] == 'blocked' and 'started_at' not in order:
                    if self.destinations[order['destination_id']]['status'] == 'open':
                        order.update(state='executing', started_at=minute)
                        route['departed_at'] = minute
                        route['arrives_at'] = minute + route['duration_minutes']
                        group.update(status='evacuating', destination_id=None)
                        emit('operations', 'movement_resumed',
                             {'order_id': order['id'], 'group_id': group['id'],
                              'position': copy.deepcopy(group['position'])}, actionable=True)
                    continue
                position = journey_position(route, minute)[0]
                if (self.destinations[order['destination_id']]['status'] != 'open'
                        or not _movement_safe(route, minute, 'group', position, hazard_check, movement_check)):
                    route['departed_at'] += 1
                    route['arrives_at'] += 1
                    was_blocked = order['state'] == 'blocked'
                    order['state'] = group['status'] = 'blocked'
                    if not was_blocked:
                        emit('operations', 'movement_blocked',
                             {'order_id': order['id'], 'group_id': group['id'],
                              'position': copy.deepcopy(group['position'])}, actionable=True)
                    continue
                group['position'] = position
                was_blocked = order['state'] == 'blocked'
                order['state'], group['status'] = 'executing', 'evacuating'
                if was_blocked:
                    emit('operations', 'movement_resumed',
                         {'order_id': order['id'], 'group_id': group['id'],
                          'position': copy.deepcopy(position)}, actionable=True)
                if minute >= route['arrives_at']:
                    group.update(status='arrived', destination_id=order['destination_id'])
                    order.update(state='completed', completed_at=minute)
                    emit('operations', 'evacuation_arrived', {'order_id': order['id'], 'group_id': group['id']})

        if exposure_check is not None:
            for group in self.groups.values():
                if exposure_check(copy.deepcopy(group)):
                    group['exposed'] = True

    def snapshot(self):
        return copy.deepcopy(dict(resources=self.resources, destinations=self.destinations, groups=self.groups,
                                  orders=self.orders, reservations=self.reservations,
                                  air_refill_queue=self.air_refill_queue, air_refill_active=self.air_refill_active))

    def checkpoint(self):
        result = self.snapshot()
        result.update(profile=copy.deepcopy(self.profile), order_serial=self.order_serial,
                      last_minute=self.last_minute, air_window_open=self.air_window_open)
        return result

    @classmethod
    def restore(cls, state, *, return_planner=None):
        state = copy.deepcopy(state)
        instance = cls(list(state['resources'].values()), list(state['destinations'].values()),
                       list(state['groups'].values()), state['profile'], return_planner=return_planner)
        for key in ('orders', 'reservations', 'air_refill_queue', 'air_refill_active', 'order_serial', 'last_minute'):
            setattr(instance, key, state[key])
        instance.air_window_open = state.get('air_window_open')
        return instance
