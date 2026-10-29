"""Private, aggregate movement evidence collected alongside Operations.tick.

Aircraft clearance and weather here are simulation assumptions, not aviation
clearance. This observer does not participate in operational decisions.
"""
import copy
import math

_VERSION = 2
_TOL = 0.05  # metric geometry tolerance, metres


def _point(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(x) in (int, float) and math.isfinite(x) for x in value))


def _interpolate(route, minute):
    """Independently interpolate by elapsed leg time and segment length."""
    elapsed = max(0, minute - route['departed_at'])
    for leg in route['legs']:
        duration = leg['duration_minutes']
        if elapsed < duration:
            points = leg['coordinates']
            lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
            remaining = sum(lengths) * elapsed / duration
            for (a, b), length in zip(zip(points, points[1:]), lengths):
                if length and remaining <= length:
                    return [a[i] + (b[i] - a[i]) * remaining / length for i in (0, 1)]
                remaining -= length
            return list(points[-1])
        elapsed -= duration
    return list(route['destination'])


def _project(point, line):
    """Return unambiguous distance along a directed edge, or None."""
    matches, offset = [], 0.0
    for a, b in zip(line, line[1:]):
        length = math.dist(a, b)
        if length:
            t = max(0., min(1., sum((point[i] - a[i]) * (b[i] - a[i]) for i in (0, 1)) / length**2))
            projected = [a[i] + t * (b[i] - a[i]) for i in (0, 1)]
            if math.dist(point, projected) <= _TOL:
                matches.append(offset + t * length)
        offset += length
    if not matches:
        return 'outside'
    if max(matches) - min(matches) > _TOL:
        return None
    return min(matches)


class MovementObserver:
    def __init__(self, env):
        self.env = env
        self.ops = env.ops
        original = self.ops.tick
        if getattr(original, '_movement_observer', False):
            raise ValueError('Operations tick already has a movement observer')
        self.coverage = {}
        self.findings = {}

        def observed_tick(minute, **kwargs):
            before = self._capture()
            previous_minute = self.ops.last_minute
            drops = []
            suppress = kwargs['suppress']

            def observed_suppress(unit):
                aircraft = unit.get('kind') == 'aircraft'
                evidence = dict(id=unit.get('id'), clearance=unit.get('clearance')) if aircraft else None
                applied = suppress(unit)
                if aircraft and applied is not False:
                    drops.append(evidence)
                return applied

            arguments = dict(kwargs, suppress=observed_suppress)
            result = original(minute, **arguments)
            after = self._capture()
            self._inspect(minute, previous_minute, before, after, drops, kwargs)
            return result

        observed_tick._movement_observer = True
        self.ops.tick = observed_tick

    def _count(self, name, n=1):
        self.coverage[name] = self.coverage.get(name, 0) + n

    def _finding(self, kind, minute, entity, detail):
        entry = self.findings.setdefault(kind, {'count': 0, 'first': dict(minute=minute, entity=entity, detail=detail)})
        entry['count'] += 1

    def _capture(self):
        return copy.deepcopy(dict(resources=self.ops.resources, groups=self.ops.groups, orders=self.ops.orders,
                                  reservations=self.ops.reservations, destinations=self.ops.destinations))

    def _road_checks(self, route, minute, entity):
        for leg in route.get('legs', []):
            if leg.get('mode') != 'road':
                continue
            self._count('road_legs_checked')
            edge = self.env.network.edges.get(leg.get('edge_id'))
            if edge is None:
                self._finding('unsupported_road_leg', minute, entity, 'Road edge absent from pinned network')
                continue
            coords, line = leg.get('coordinates'), edge.get('coordinates')
            if not (isinstance(coords, list) and len(coords) >= 2 and all(_point(p) for p in coords)
                    and isinstance(line, list) and len(line) >= 2 and all(_point(p) for p in line)):
                self._count('road_geometry_unverifiable')
                continue
            offsets = [_project(p, line) for p in coords]
            if 'outside' in offsets:
                self._finding('unsupported_road_leg', minute, entity, 'Road leg leaves the pinned edge geometry')
                continue
            if any(value is None for value in offsets):
                # Ambiguous self-intersections cannot prove invalid direction.
                self._count('road_geometry_unverifiable')
                continue
            if any(b < a - _TOL for a, b in zip(offsets, offsets[1:])):
                self._finding('direction_invalid_road_leg', minute, entity, 'Road leg traverses a directed edge backwards')
            elif any(abs(math.dist(a, b) - (end - start)) > _TOL
                     for a, b, start, end in zip(coords, coords[1:], offsets, offsets[1:])):
                self._finding('unsupported_road_leg', minute, entity, 'Road leg leaves the pinned edge geometry')

    def _movement(self, before, after, route_before, route_after, minute, entity, arrival, *, position_minute=None):
        old_position, new_position = before.get('position'), after.get('position')
        if not (_point(old_position) and _point(new_position)):
            self._count('position_unverifiable')
            return
        moved = math.dist(old_position, new_position) > _TOL
        # A newly installed return/replacement is not evidence that its old route
        # advanced; an old journey can also be cleared immediately at base.
        if not isinstance(route_before, dict):
            if moved:
                self._finding('movement_without_journey', minute, entity, 'Position changed without a prior journey')
            return
        same = (isinstance(route_after, dict) and route_before.get('origin') == route_after.get('origin')
                and route_before.get('destination') == route_after.get('destination')
                and route_before.get('legs') == route_after.get('legs'))
        route = route_after if same else route_before
        if not moved and not arrival and not same:
            return
        try:
            expected = _interpolate(route, minute if position_minute is None else position_minute)
            if not _point(expected):
                raise ValueError('invalid position')
            self._count('timed_positions_checked')
            if math.dist(new_position, expected) > _TOL:
                self._finding('off_timed_path', minute, entity, 'Actual position differs from timed route interpolation')
            if arrival and minute < route['departed_at'] + route['duration_minutes']:
                self._finding('early_arrival', minute, entity, 'Arrival precedes the journey duration')
            self._road_checks(route, minute, entity)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            self._count('journey_unverifiable')

    def _capacity(self, minute, state):
        groups, reservations, destinations = state['groups'], state['reservations'], state['destinations']
        self._count('capacity_ticks_checked')
        for group_id, dest in reservations.items():
            if group_id not in groups or dest not in destinations:
                self._finding('invalid_reservation', minute, group_id, 'Reservation references an unknown cohort or destination')
        for identifier, destination in destinations.items():
            occupants = sum(group['count'] for group_id, group in groups.items()
                            if reservations.get(group_id) == identifier or group.get('destination_id') == identifier)
            if occupants > destination['capacity']:
                self._finding('capacity_exceeded', minute, identifier, 'Occupants and reservations exceed capacity')

    def _inspect(self, minute, previous_minute, before, after, drops, kwargs):
        self._count('ticks_observed')
        self._capacity(minute, after)
        wind, daylight = kwargs['wind'], kwargs['daylight']
        speed = math.hypot(*wind) if isinstance(wind, (list, tuple)) else wind
        window = bool(daylight) and speed <= self.ops.profile.get('aircraft_max_wind', 40)
        for drop in drops:
            self._count('modeled_aircraft_drops_checked')
            if not drop['clearance'] or not window:
                self._finding('aircraft_drop_without_modeled_clearance', minute, drop['id'],
                              'Applied drop lacks simulated clearance or modeled flight window')
        if previous_minute is not None and minute != previous_minute + 1:
            self._count('multi_minute_ticks_unobserved')
            return
        for identifier, unit in before['resources'].items():
            current = after['resources'][identifier]
            if unit.get('kind') == 'aircraft' and unit.get('state') == 'mobilizing' and current.get('state') == 'travel':
                self._count('modeled_aircraft_departures_checked')
                if not unit.get('clearance') or not window:
                    self._finding('aircraft_departure_without_modeled_clearance', minute, identifier,
                                  'Departure lacks simulated clearance or modeled flight window')
            state = unit.get('blocked_state') if unit.get('state') == 'blocked' else unit.get('state')
            if state in ('travel', 'returning'):
                arrival = ((state == 'travel' and current.get('state') in ('on_scene', 'working'))
                           or (state == 'returning' and current.get('state') in ('available', 'refilling', 'resting', 'off_duty')))
                if (current.get('state') == 'needs_assistance' and _point(unit.get('position'))
                        and _point(current.get('position')) and unit['position'] == current['position']):
                    # Return planning or fuel exhaustion can halt before movement.
                    # The retained journey still has its old clock, so verify the
                    # last position against the preceding tick, not this tick.
                    self._count('stationary_assistance_transitions')
                    self._movement(unit, current, unit.get('journey'), unit.get('journey'), minute,
                                   identifier, False, position_minute=minute - 1)
                else:
                    if (current.get('state') == 'needs_assistance' and _point(unit.get('position'))
                            and _point(current.get('position'))
                            and math.dist(unit['position'], current['position']) > _TOL):
                        self._finding('movement_during_assistance_transition', minute, identifier,
                                      'Position changed while active travel stopped for assistance')
                    self._movement(unit, current, unit.get('journey'), current.get('journey'), minute, identifier, arrival)
        for identifier, group in before['groups'].items():
            order = before['orders'].get(group.get('order_id'))
            if not order or order.get('state') not in ('executing', 'blocked') or 'started_at' not in order:
                continue
            current = after['groups'][identifier]
            next_order = after['orders'].get(group.get('order_id'), {})
            arrival = next_order.get('state') == 'completed' and current.get('status') == 'arrived'
            self._movement(group, current, order.get('journey'), next_order.get('journey'), minute, identifier, arrival)

    def snapshot(self):
        return copy.deepcopy(dict(version=_VERSION, coverage=self.coverage, findings=self.findings))

    def restore(self, state):
        if (not isinstance(state, dict) or set(state) != {'version', 'coverage', 'findings'}
                or type(state.get('version')) is not int or state['version'] != _VERSION):
            raise ValueError('Incompatible movement observer snapshot')
        coverage, findings = state.get('coverage'), state.get('findings')
        if not isinstance(coverage, dict) or not isinstance(findings, dict):
            raise ValueError('Invalid movement observer snapshot')
        if not all(isinstance(key, str) and type(value) is int and value >= 0 for key, value in coverage.items()):
            raise ValueError('Invalid movement observer coverage')
        for key, value in findings.items():
            if (not isinstance(key, str) or not isinstance(value, dict) or type(value.get('count')) is not int
                    or value['count'] < 1 or set(value) != {'count', 'first'}
                    or not isinstance(value.get('first'), dict)):
                raise ValueError('Invalid movement observer findings')
            first = value['first']
            if (set(first) != {'minute', 'entity', 'detail'} or type(first.get('minute')) is not int
                    or not isinstance(first.get('entity'), str)
                    or not isinstance(first.get('detail'), str)):
                raise ValueError('Invalid movement observer finding')
        self.coverage, self.findings = copy.deepcopy(coverage), copy.deepcopy(findings)
