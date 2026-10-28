"""One continuous incident day with causal, sparse control-centre decisions."""
import copy
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from ..regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork, make_journey, make_leg
from .data import load_inputs
from .clock import solar_daylight
from .events import EventBus
from .operations import Operations
from .outcomes import OutcomeCounter, recompute
from .routing import HazardRouter
from .scenarios import CASE_IDS, DayFire, build_scenario, native, grid_point


VERSION = 'wildfire-incident-day-v2'


def implementation_digest():
    root = Path(__file__).parents[3]
    folders = [root / 'envs/wildfire_response', root / 'wildfire_core', root / 'simulator']
    paths = [path for folder in folders for path in folder.rglob('*.py') if 'tests' not in path.parts]
    paths.append(root / 'envs/wildfire_response/data/huelva_regional_v2.json')
    return digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(paths)})


class IncidentDayEnv:
    def __init__(self, inputs, network, *, expected_inputs_sha256, expected_network_sha256,
                 allow_unapproved_data=False, artifact_dir=None):
        self.inputs = load_inputs(inputs, expected_inputs_sha256, allow_unapproved=allow_unapproved_data)
        self.network = RoadNetwork(network, expected_network_sha256)
        if self.inputs['network_sha256'] != self.network.sha256:
            raise ValueError('Input pack and road network pins disagree')
        self.router = HazardRouter(self.network, self.inputs, self.inputs['heights'], walk_mask=self.inputs['walk_mask'])
        resolution = self.inputs['provenance'].get('fuel_resolution') or {}
        self._uncertain_land = {(cell['column'], cell['row'])
            for cell in resolution.get('document', {}).get('cells', []) if cell['land_kind'] == 'unresolved'}
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self._scenario = None

    def reset(self, scenario_id=CASE_IDS[0], seed=None):
        self._scenario = build_scenario(self.inputs, self.network, scenario_id, seed)
        self._configure_router()
        self.minute = self.decision = self.invalid_streak = 0
        self.terminated = self.truncated = self.censored = self.abandoned = False
        self._input_censored = False
        self.reward = 0.
        self.fire = DayFire(self.inputs, self._scenario['seed'])
        self.fire.set_wind(x=self._scenario['wind'][0], y=self._scenario['wind'][1])
        for column, row in self._scenario['initial_fire']:
            if (column, row) in self._uncertain_land:
                raise ValueError('Cannot initialize an incident on unresolved land or water')
            self.fire.add_fire(column, row)
        for unused in range(self._scenario['warmup_minutes']):
            if self._land_guard():
                raise ValueError('Warm start reaches unresolved land or water')
            self.fire.step()
        self.fire.tick = 0
        self.fire.boundary_contact_at = None
        self.bus = EventBus(self._scenario['seed'], self._scenario['centers'])
        self.ops = Operations(self._scenario['resources'], self._scenario['destinations'],
                              self._scenario['groups'], self._scenario['profile'], return_planner=self._return_route)
        self._known_resources = {key: self._resource_view(unit, 0) for key, unit in self.ops.resources.items()}
        self._known_groups = copy.deepcopy(self.ops.groups)
        self._known_destinations = copy.deepcopy(self.ops.destinations)
        self._known_wind = self._scenario['wind'][:]
        self._known_wind_at = 0
        self._memory = {}
        self._known_revision = 0
        self._offers, self._issued_orders, self._commands, self._approaches = {}, {}, [], {}
        self._offer_serial = 0
        self._query_log = []
        self._signals = {}
        self._invalid_log = []
        self._work_log = []
        self._event_effects = []
        self._closed_centers, self._restrictions = {}, set()
        self._known_restrictions = set()
        self._air_unavailable_until = self._known_air_unavailable_until = 0
        self._pending_requests = set()
        self._dose = {key: 0 for key in self.ops.groups}
        self._events = copy.deepcopy(self._scenario['events'])
        self._footprint = self._burned()
        self._audit = []
        self._actions = []
        self._counter = OutcomeCounter(self._footprint,
            [row * self.fire.width + column for row, cells in enumerate(self.fire.cells)
             for column, cell in enumerate(cells) if cell['initial_fuel'] > 0],
            self.ops.groups, self.inputs['grid']['cell_km'] ** 2 * 100)
        self._prehistory = dict(minutes=self._scenario['warmup_minutes'],
            checkpoint_sha256=digest(self.fire.checkpoint()),
            footprint_hectares=len(self._footprint) * self.inputs['grid']['cell_km'] ** 2 * 100,
            role='Synthetic initialization, not an expert demonstration')
        if self._prehistory['footprint_hectares'] <= 500:
            raise ValueError('Incident-day handover must already represent a large fire')
        initial_seen = sorted(self._burning())[:max(1, len(self._burning()) // 2)]
        self._capture_cells(initial_seen, 0)
        self.bus.emit('handover', 'handover', dict(text='EXERCISE. Large-fire operational handover. '
            'Reported perimeter is partial; resource stocks and assignments are carried forward. '
            + self._scenario.get('handover_notice', ''),
            cells=self._cells(initial_seen)), 0, role='center', delay=0)
        self.bus.emit('prior-shift', 'handover_followup', dict(text='EXERCISE. Confirmation of the carried '
            'ground assignment and remaining stock. Prehistory is initialization, not an expert policy.',
            resource=self._resource_view(self.ops.resources['engine-0'], 0)), 0, role='center', delay=15)
        self._receive(self.bus.tick(0, self._center_views()))
        self._record(initial=True)
        self._initial_observation = self.get_situation()
        if self.artifact_dir:
            self._start_artifacts()
        return self.get_situation(), self._info(None, 'handover')

    def _resource_view(self, unit, minute):
        fields = ('id', 'kind', 'home_base', 'position', 'base_position', 'capacity_litres',
                  'water_litres', 'state', 'available_at', 'work_remaining', 'sector_id', 'target_grid',
                  'duty_end_at', 'total_work_remaining', 'facility_id', 'fuel_minutes', 'fuel_capacity_minutes')
        value = dict({key: copy.deepcopy(unit[key]) for key in fields if key in unit},
                     observed_at=minute, received_at=minute)
        journey = unit.get('journey') or {}
        if journey.get('_trusted_return_egress') is True:
            value['withdrawal'] = dict(policy=journey['emergency_egress']['version'],
                                      buffer_exited=journey['emergency_egress']['exited'])
        return value

    def _sector(self):
        return self._scenario.get('sector_grid', [self.fire.width // 2, self.fire.height // 2])[:]

    def _configure_router(self):
        self.router = HazardRouter(self.network, self.inputs, self.inputs['heights'],
            walk_mask=self.inputs['walk_mask'],
            allowed_edge_ids=self._scenario['profile'].get('allowed_road_edge_ids'))

    def _burning(self):
        return {(column, row) for row, cells in enumerate(self.fire.cells) for column, cell in enumerate(cells)
                if self.fire.burning(cell)}

    def _burned(self):
        return {row * self.fire.width + column for row, cells in enumerate(self.fire.cells)
                for column, cell in enumerate(cells) if self.fire.burning(cell) or cell['burned'] > 0}

    def _cells(self, points):
        return [dict(x=column, y=row, burning=self.fire.burning(self.fire.cells[row][column]),
                     observed_at=self.minute) for column, row in points]

    def _capture_cells(self, points, observed_at):
        for cell in self._cells(points):
            cell.update(observed_at=observed_at, received_at=self.minute)
            self._memory[(cell['x'], cell['y'])] = cell

    def _calendar_daylight(self):
        current = datetime.fromisoformat(self._scenario['start_utc']) + timedelta(minutes=self.minute)
        centers = self.inputs['grid'].get('centre_lon_lat')
        longitude, latitude = (centers[self._sector()[1]][self._sector()[0]] if centers else [-5.15, 36.55])
        return solar_daylight(current, longitude, latitude)

    def _daylight(self):
        return self._calendar_daylight() and self.minute >= self._air_unavailable_until

    def _center_views(self):
        return {center: dict(available=self._closed_centers.get(center, -1) <= self.minute,
            resources=dict(observed_at=max((unit['observed_at'] for unit in self._known_resources.values()
                                           if unit['home_base'] == center), default=0),
                           units=[copy.deepcopy(unit) for unit in self._known_resources.values() if unit['home_base'] == center]),
            situation=dict(observed_at=max((cell['observed_at'] for cell in self._memory.values()), default=0),
                           cells=copy.deepcopy(list(self._memory.values())), wind=self._known_wind[:]))
            for center in self._scenario['centers']}

    def _emit(self, source, kind, payload, urgent=False, actionable=True):
        payload = copy.deepcopy(payload)
        if kind in ('movement_blocked', 'work_target_unavailable', 'egress_unavailable'):
            point = grid_point(self.inputs, payload['position'])
            payload['cells'] = self._cells([(column, row)
                for row in range(max(0, point[1] - 2), min(self.fire.height, point[1] + 3))
                for column in range(max(0, point[0] - 2), min(self.fire.width, point[0] + 3))])
        if payload.get('order_id') in self.ops.orders:
            order = self.ops.orders[payload['order_id']]
            payload['order'] = {key: value for key, value in order.items()
                                if key not in ('journey', 'due_at')}
        return self.bus.emit(source, kind, payload, self.minute, urgent=urgent, actionable=actionable)

    def _receive(self, records):
        for record in records:
            payload = record['payload']
            if 'resource' in payload:
                unit = copy.deepcopy(payload['resource'])
                unit['received_at'] = record['received_at']
                if unit['observed_at'] >= self._known_resources.get(unit['id'], {}).get('observed_at', -1):
                    self._known_resources[unit['id']] = unit
            if 'group' in payload:
                group_id = payload['group']['id']
                if record['observed_at'] >= self._known_groups.get(group_id, {}).get('observed_at', -1):
                    self._known_groups[group_id] = dict(copy.deepcopy(payload['group']),
                        observed_at=record['observed_at'], received_at=record['received_at'])
            if 'destination' in payload:
                destination = payload['destination']
                if record['observed_at'] >= self._known_destinations.get(destination['id'], {}).get('observed_at', -1):
                    self._known_destinations[destination['id']] = dict(copy.deepcopy(destination),
                        observed_at=record['observed_at'], received_at=record['received_at'])
            if 'cells' in payload:
                for cell in payload['cells']:
                    point = (cell['x'], cell['y'])
                    if cell['observed_at'] >= self._memory.get(point, {}).get('observed_at', -1):
                        self._memory[point] = dict(copy.deepcopy(cell), received_at=record['received_at'])
                self._known_revision += 1
            if 'wind' in payload:
                if record['observed_at'] >= self._known_wind_at:
                    self._known_wind = payload['wind'][:]
                    self._known_wind_at = record['observed_at']
                    self._known_revision += 1
            if 'offer' in payload:
                self._offers[payload['offer']['id']] = copy.deepcopy(payload['offer'])
                self._pending_requests.discard(payload['offer']['resource_id'])
            if record['kind'] in ('request_unavailable', 'dispatch_declined'):
                self._pending_requests.discard(payload.get('resource_id'))
            if record['kind'] == 'aircraft_window':
                self._known_air_unavailable_until = payload['until']
            if record['kind'] == 'access_restriction':
                self._known_restrictions.add(payload['resource_kind'])
                self._known_revision += 1
            if 'order' in payload:
                order = payload['order']
                if record['observed_at'] >= self._issued_orders.get(order['id'], {}).get('observed_at', -1):
                    self._issued_orders[order['id']] = dict(copy.deepcopy(order), observed_at=record['observed_at'],
                                                           received_at=record['received_at'])

    def get_situation(self):
        if self._scenario is None:
            raise RuntimeError('reset required')
        return copy.deepcopy(dict(interface_version=VERSION, task_id=self._scenario['id'], minute=self.minute,
            decision=self.decision, clock=dict(minute=self.minute, horizon_minutes=1440,
                now_utc=(datetime.fromisoformat(self._scenario['start_utc']) + timedelta(minutes=self.minute)).isoformat()),
            resources=list(self._known_resources.values()), districts=list(self._known_groups.values()),
            destinations=list(self._known_destinations.values()), sensor_memory=list(self._memory.values()),
            wind=self._known_wind[:], reports=self.bus.reports[-20:], report_count=len(self.bus.reports),
            status_requests=[self.bus.requests[key] for key in sorted(self.bus.requests)],
            offers=[self._offers[key] for key in sorted(self._offers)],
            pending_resource_requests=sorted(self._pending_requests),
            access_restrictions=sorted(self._known_restrictions),
            protection_orders=[self._issued_orders[key] for key in sorted(self._issued_orders)],
            centers=self._scenario['centers'][:],
            sectors=[dict(id='incident', center=self._sector())],
            input_approval=self.inputs['approval_status'],
            aircraft_operable=self._calendar_daylight() and math.hypot(*self._known_wind) <= 2.8
                and self.minute >= self._known_air_unavailable_until,
            rules=dict(wait='Up to60min; actionable deliveries interrupt. Routine telemetry does not wake.',
                timing='Physical progression is one minute; committed orders consume one minute.',
                doctrine='No mandatory15-20min ping interval or minimum aircraft fire size.',
                exposure='Synthetic exposure, not mortality', daylight='Offline approximate solar window; no flight authorization claim')))

    def get_reports(self, ids=None, *, after_cursor=0, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20 or type(after_cursor) is not int or not 0 <= after_cursor <= len(self.bus.reports):
            raise ValueError('Bounded delivered-report query required')
        known = {record['id'] for record in self.bus.reports}
        if ids is not None and (not isinstance(ids, list) or len(ids) > 20 or any(identifier not in known for identifier in ids)):
            raise ValueError('Only delivered report IDs are queryable')
        return self.bus.get_reports(ids, after_cursor, limit)

    def route_options(self, resource_id, sector_id='incident'):
        unit = self._known_resources.get(resource_id)
        if unit is None or sector_id != 'incident':
            raise ValueError('Choose a public resource and sector')
        target = self._sector()
        known_fire = {point for point, cell in self._memory.items() if cell['burning']}
        if unit['kind'] == 'aircraft':
            target_native = native(self.inputs, target)
            journey = make_journey([make_leg('flight', [unit['position'], target_native], 240)],
                                   unit['position'], target_native, 'Synthetic land-loaded fixed-wing sortie')
            identifier = digest(dict(resource=resource_id, minute=self.minute, journey=journey))[:20]
            options = [dict(id=identifier, approach_grid=target, approach_point=target_native,
                            journey=journey, travel_minutes=journey['duration_minutes'], knowledge_revision=self._known_revision)]
            result = dict(status='reachable', options=options)
        else:
            result = self.router.options(unit['position'], target, mode=unit['kind'], burning=known_fire,
                wind=self._known_wind, revision=self._known_revision,
                excluded_cells=self._excluded(unit['kind'], self._known_restrictions))
        for option in result['options']:
            option['id'] = digest(dict(route_id=option['id'], resource_id=resource_id, sector_id=sector_id))[:20]
            self._approaches[option['id']] = dict(copy.deepcopy(option), resource_id=resource_id, sector_id=sector_id)
        self._log_query('route_options', dict(resource_id=resource_id, sector_id=sector_id), result)
        return copy.deepcopy(result)

    def evacuation_options(self, group_id, destination_id):
        if group_id not in self._known_groups or destination_id not in self._known_destinations:
            raise ValueError('Choose a reported group and destination')
        group, destination = self._known_groups[group_id], self._known_destinations[destination_id]
        known_fire = {point for point, cell in self._memory.items() if cell['burning']}
        result = self.router.options(group['position'], grid_point(self.inputs, destination['position']), mode='crew',
            burning=known_fire, wind=self._known_wind, revision=self._known_revision, limit=32,
            target_metric=destination['position'])
        result['options'] = [option for option in result['options']
                            if math.dist(option['journey']['destination'], destination['position']) < .01][:3]
        if result['status'] == 'reachable' and not result['options']:
            result['status'] = 'unmapped'
        for option in result['options']:
            option['id'] = digest(dict(id=option['id'], group_id=group_id, destination_id=destination_id))[:20]
            self._approaches[option['id']] = dict(copy.deepcopy(option), group_id=group_id, destination_id=destination_id)
        self._log_query('evacuation_options', dict(group_id=group_id, destination_id=destination_id), result)
        return copy.deepcopy(result)

    def _log_query(self, tool, arguments, result):
        query = dict(tool=tool, arguments=arguments, before_decision=self.decision,
                     minute=self.minute, result_sha256=digest(result))
        if self._scenario['profile'].get('causal_coverage') and tool == 'route_options':
            query['public_route_summary'] = dict(status=result['status'], option_count=len(result['options']))
        self._query_log.append(query)
        if self.artifact_dir:
            with (self.artifact_dir / 'queries.jsonl').open('a') as stream:
                stream.write(json.dumps(query, sort_keys=True, allow_nan=False) + '\n')

    def _return_route(self, unit, minute):
        if unit['kind'] == 'aircraft':
            return make_journey([make_leg('flight', [unit['position'], unit['base_position']], 240)],
                                unit['position'], unit['base_position'], 'Declared recovery-to-base flight')
        result = self.router.options(unit['position'], grid_point(self.inputs, unit['base_position']),
            mode=unit['kind'], burning=self._burning(), wind=list(self.fire.wind),
            excluded_cells=self._excluded(unit['kind'], self._restrictions),
            revision=0, target_metric=unit['base_position'], limit=1,
            emergency_egress=self._scenario['profile'].get('emergency_egress_policy') == 'return-only-buffer-exit-v1')
        return result['options'][0]['journey'] if result['options'] else None

    def check_action(self, action):
        try:
            self._validate(action)
            return dict(valid=True, error=None)
        except (ValueError, TypeError, KeyError) as error:
            return dict(valid=False, error=str(error))

    def _validate(self, action):
        if not isinstance(action, dict) or len(json.dumps(action, allow_nan=False).encode()) > 65536:
            raise ValueError('Bounded structured action required')
        allowed = {'advance_minutes', 'requests', 'authorizations', 'releases', 'protection_orders', 'status_requests'}
        if set(action) - allowed:
            raise ValueError('Unknown incident-day action fields')
        if 'advance_minutes' in action:
            if len(action) != 1 or type(action['advance_minutes']) is not int or not 1 <= action['advance_minutes'] <= 60:
                raise ValueError('Wait must be1-60minutes without simultaneous orders')
            return
        for field in allowed - {'advance_minutes'}:
            if not isinstance(action.get(field, []), list) or len(action.get(field, [])) > 6:
                raise ValueError('At most six entries per order kind')
        assigned = set()
        for request in action.get('requests', []):
            if set(request) != {'resource_id', 'sector_id', 'approach_id'}:
                raise ValueError('Request needs resource, sector and proposed approach')
            identifier = request['resource_id']
            option = self._approaches.get(request['approach_id'])
            if identifier in assigned or identifier not in self._known_resources or option is None or option['resource_id'] != identifier or option['sector_id'] != request['sector_id']:
                raise ValueError('Use a matching public approach once per unit')
            assigned.add(identifier)
            if identifier in self._pending_requests:
                raise ValueError('A request for this resource is already pending')
        for identifier in action.get('authorizations', []):
            offer = self._offers.get(identifier)
            if offer is None or offer['status'] != 'offered' or offer['expires_at'] <= self.minute:
                raise ValueError('Authorize a live offered allocation')
            if offer['resource_id'] in assigned:
                raise ValueError('One mission-changing order per resource per commit')
            assigned.add(offer['resource_id'])
        for identifier in action.get('releases', []):
            if identifier not in self._known_resources:
                raise ValueError('Unknown resource')
            if identifier in assigned:
                raise ValueError('One mission-changing order per resource per commit')
            assigned.add(identifier)
        for request in action.get('status_requests', []):
            if set(request) != {'center_id', 'subject'} or request['center_id'] not in self._scenario['centers'] or request['subject'] not in ('resources', 'situation'):
                raise ValueError('Choose a known center and status subject')
        protected = set()
        for order in action.get('protection_orders', []):
            if set(order) != {'group_id', 'action', 'destination_id', 'approach_id'} or order['group_id'] not in self._known_groups:
                raise ValueError('Protection needs a public group/action/destination')
            if order['action'] not in ('shelter', 'evacuate'):
                raise ValueError('Unsupported protection action')
            if order['group_id'] in protected:
                raise ValueError('One protection order per cohort per commit')
            protected.add(order['group_id'])
            if order['action'] == 'evacuate' and order['destination_id'] not in self._known_destinations:
                raise ValueError('Choose a public evacuation destination')
            approach = self._approaches.get(order['approach_id'])
            if order['action'] == 'evacuate' and (approach is None or approach.get('group_id') != order['group_id']
                    or approach.get('destination_id') != order['destination_id']):
                raise ValueError('Choose a matching publicly proposed evacuation approach')
            if order['action'] == 'shelter' and (order['destination_id'] is not None or order['approach_id'] is not None):
                raise ValueError('Shelter does not choose an evacuation destination')

    def step(self, action):
        if self._scenario is None or self.terminated or self.truncated:
            raise RuntimeError('A live episode is required')
        previous = self.minute
        error = None
        try:
            self._validate(action)
            valid = True
        except (ValueError, TypeError, KeyError, OverflowError) as exception:
            error, valid = str(exception), False
        self.decision += 1
        self.invalid_streak = 0 if valid else self.invalid_streak + 1
        if not valid:
            try:
                rejected = json.dumps(action, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                rejected = repr(action)
            self._invalid_log.append(dict(decision=self.decision, minute=previous,
                validation_error=error, rejected_arguments=rejected[:65536], arguments_truncated=len(rejected) > 65536))
        if valid and 'advance_minutes' not in action:
            for request in action.get('status_requests', []):
                self.bus.request_status(request['center_id'], request['subject'], self.minute)
            for request in action.get('requests', []):
                self._pending_requests.add(request['resource_id'])
                self._commands.append(dict(due=self.minute + 2, kind='request', data=copy.deepcopy(request)))
            for identifier in action.get('authorizations', []):
                self._offers[identifier]['status'] = 'authorized'
                self._commands.append(dict(due=self.minute + 1, kind='authorize', data=identifier))
            for identifier in action.get('releases', []):
                self._commands.append(dict(due=self.minute + 1, kind='release', data=identifier))
            for order in action.get('protection_orders', []):
                self._commands.append(dict(due=self.minute + 1, kind='protect', data=copy.deepcopy(order)))
        duration = action.get('advance_minutes', 1) if valid else 1
        wake = 'watch_timeout'
        for unused in range(min(duration, 1440 - self.minute)):
            actionable = self._tick()
            if self.censored or self.invalid_streak >= 3:
                self.abandoned = self.invalid_streak >= 3
                self.truncated = True
                wake = ('input_uncertainty_censored' if self._input_censored else 'boundary_censored') if self.censored else 'invalid_abandonment'
                break
            if self.minute == 1440:
                self.terminated = True
                wake = 'day_complete'
                break
            if actionable:
                wake = actionable
                break
        terminal = self.terminated or self.truncated
        reward = self.outcomes()['graded_score'] if terminal else 0.
        self.reward += reward
        observation = self.get_situation()
        recorded = copy.deepcopy(action) if valid else {'invalid_incident_day_action': True}
        self._actions.append(dict(action=recorded, valid=valid, start_minute=previous,
            minute=self.minute, observation_sha256=digest(observation), reward=reward,
            terminated=self.terminated, truncated=self.truncated))
        if self.artifact_dir:
            self._append_artifacts(self._actions[-1])
            if self.minute // 60 > previous // 60:
                self.save_checkpoint(self.artifact_dir / 'private' / f'checkpoint-{self.minute}.json.gz')
        return observation, reward, self.terminated, self.truncated, self._info(error, wake)

    def advance_time(self, max_minutes=60):
        return self.step(dict(advance_minutes=max_minutes))

    def request_status(self, center_id, subject='situation'):
        return self.step(dict(status_requests=[dict(center_id=center_id, subject=subject)]))

    def _tick(self):
        self.minute += 1
        self._work_log = []
        self._event_effects = []
        for event in [event for event in self._events if event['minute'] == self.minute]:
            self._events.remove(event)
            if event['kind'] == 'wind':
                if self._scenario['profile'].get('realmap_v2_wind_coverage') and event['minute'] == 15:
                    from .real_map_acceptance import capture_wind_effect

                    self._event_effects.append(capture_wind_effect(self, event))
                self.fire.set_wind(x=event['wind'][0], y=event['wind'][1])
                self.bus.emit('weather', 'wind_update', dict(wind=event['wind'][:]), self.minute, channel='radio')
            elif event['kind'] == 'access_restriction':
                if self._scenario['profile'].get('causal_coverage'):
                    self._event_effects.append(self._access_effect(event))
                self._restrictions.add(event['resource_kind'])
                self._emit('road-patrol', 'access_restriction', dict(resource_kind=event['resource_kind'],
                    text='EXERCISE. Central-sector approach restricted for this vehicle class.'), urgent=True)
            elif event['kind'] == 'aircraft_unavailable':
                self._air_unavailable_until = event['until']
                self._emit('regional', 'aircraft_window', dict(until=event['until']))
            elif event['kind'] == 'destination_closed':
                destination = self.ops.destinations[event['destination_id']]
                destination['status'] = 'closed'
                if self._scenario['profile'].get('closure_affects_occupants'):
                    affected = []
                    for group in self.ops.groups.values():
                        if (group.get('destination_id') == destination['id']
                                and group['position'] == destination['position']):
                            affected.append(dict(group_id=group['id'], count=group['count'],
                                                 previous_status=group['status']))
                            group['status'] = 'blocked'
                            self._emit(destination['id'], 'relocation_required',
                                dict(group=copy.deepcopy(group), destination_id=destination['id'],
                                     event_minute=self.minute), urgent=True)
                    self._event_effects.append(dict(kind='destination_closed', minute=self.minute,
                        destination_id=destination['id'], affected_occupants=affected))
                self._emit('destination', 'destination_threatened', dict(destination=copy.deepcopy(destination)), urgent=True)
            elif event['kind'] == 'center_unavailable':
                self._closed_centers[event['center_id']] = event['until']
        before = {identifier: unit['state'] for identifier, unit in self.ops.resources.items()}
        self.ops.set_flight_conditions(list(self.fire.wind), self._daylight())
        self._execute_commands()
        self.ops.tick(self.minute, wind=list(self.fire.wind), daylight=self._daylight(),
            hazard_check=self._hazard_safe, suppress=self._suppress, emit=self._emit,
            movement_check=self._movement_safe)
        self._input_censored = self._land_guard()
        if not self._input_censored:
            self.fire.step()
        for group in self.ops.groups.values():
            if self._exposure(group):
                group['exposed'] = True
        for identifier, unit in self.ops.resources.items():
            changed = unit['state'] != before[identifier]
            if changed or self.minute % 15 == 0:
                self._emit(unit['home_base'], 'resource_status' if changed else 'telemetry',
                           dict(resource=self._resource_view(unit, self.minute)), actionable=changed)
            if unit['state'] in ('on_scene', 'working'):
                point = grid_point(self.inputs, unit['position'])
                cells = [(column, row) for row in range(max(0, point[1] - 2), min(self.fire.height, point[1] + 3))
                         for column in range(max(0, point[0] - 2), min(self.fire.width, point[0] + 3))]
                if self.minute % 10 == 0 or changed:
                    self._emit(identifier, 'field_observation', dict(cells=self._cells(cells)), actionable=changed)
        for identifier, group in self.ops.groups.items():
            point = grid_point(self.inputs, group['position'])
            distance = min((math.dist(point, cell) for cell in self._burning()), default=math.inf)
            level = 'exposed' if distance <= 1.5 else 'nearby_smoke' if distance <= 3 else 'clear'
            changed = level != self._signals.get(identifier, 'clear')
            self._signals[identifier] = level
            if changed or self.minute % 30 == 0:
                self.bus.emit(identifier, 'resident_update', dict(group=copy.deepcopy(group), condition=level,
                    text='EXERCISE. Resident reports ' + level.replace('_', ' ') + ' conditions.'), self.minute,
                    role='resident', channel='phone', location=grid_point(self.inputs, group['position']),
                    urgent=level == 'exposed', actionable=changed)
        working = sum(unit['state'] == 'working' for unit in self._known_resources.values())
        observed_fire = sum(cell['burning'] for cell in self._memory.values())
        shortage = observed_fire > max(8, 4 * working)
        if shortage and not self._signals.get('shortage', False):
            self._emit('center-0', 'reinforcement_needed', dict(text='EXERCISE. Observed incident exceeds '
                'the exercise capacity of the working assignment. Request assistance.', working_resources=working), urgent=True)
        self._signals['shortage'] = shortage
        if self.minute % 120 == 0:
            self.bus.emit('satellite', 'satellite_summary', dict(cells=self._cells(sorted(self._burning() | set(self._memory)))),
                          self.minute, role='satellite', channel='routine', actionable=False)
        records = self.bus.tick(self.minute, self._center_views())
        self._receive(records)
        self.censored = self._input_censored or self.fire.boundary_contact_at is not None
        self._record()
        deadline = any(offer['status'] == 'offered' and offer['expires_at'] - self.minute in (1, 2)
                       for offer in self._offers.values())
        if any(record['actionable'] for record in records):
            return 'actionable_delivery'
        if deadline:
            return 'allocation_deadline'
        if self.minute % 60 == 0:
            return 'hourly_checkpoint'
        return None

    def _execute_commands(self):
        for command in [item for item in self._commands if item['due'] <= self.minute]:
            self._commands.remove(command)
            data = command['data']
            if command['kind'] == 'request':
                unit = self.ops.resources[data['resource_id']]
                if (unit['state'] not in ('available', 'blocked', 'needs_assistance') or unit['available_at'] > self.minute
                        or self._closed_centers.get(unit['home_base'], -1) > self.minute):
                    self._emit(unit['home_base'], 'request_unavailable', dict(resource_id=unit['id']))
                    continue
                self._offer_serial += 1
                identifier = f'offer-{self._offer_serial}'
                offer = dict(id=identifier, **data, status='offered', expires_at=self.minute + 30,
                             readback='EXERCISE. Confirm resource, sector and approach exactly.')
                self._emit(unit['home_base'], 'resource_offer', dict(offer=offer))
            elif command['kind'] == 'authorize':
                offer = self._offers[data]
                unit = self.ops.resources[offer['resource_id']]
                approach = self._approaches[offer['approach_id']]
                if unit['kind'] == 'aircraft':
                    self.ops.clear_aircraft(unit['id'], self.minute)
                accepted = self.ops.dispatch(unit['id'], offer['sector_id'],
                    self._sector(), approach['journey'], self.minute)
                self._emit(unit['home_base'], 'dispatch_accepted' if accepted else 'dispatch_declined', dict(resource_id=unit['id']))
            elif command['kind'] == 'release':
                unit = self.ops.resources[data]
                accepted = self.ops.release(data, self._return_route(unit, self.minute), self.minute)
                self._emit(unit['home_base'], 'release_accepted' if accepted else 'release_declined',
                    dict(resource_id=data, resource=self._resource_view(unit, self.minute)))
            else:
                group = self.ops.groups[data['group_id']]
                journey = None
                if data['action'] == 'evacuate':
                    journey = self._approaches[data['approach_id']]['journey']
                try:
                    identifier = self.ops.protect(group['id'], data['action'], data['destination_id'], journey, self.minute)
                    self._emit('population', 'protection_accepted', dict(order_id=identifier, group_id=group['id']))
                except ValueError:
                    self._emit('population', 'protection_declined', dict(group_id=group['id']), urgent=True)

    def _hazard_safe(self, position, kind):
        if kind == 'aircraft':
            return True
        point = grid_point(self.inputs, position)
        if kind in self._restrictions and math.dist(point, self._sector()) <= 5:
            return False
        return self.router.safe_position(position, self._burning(), list(self.fire.wind),
                                         mode='crew' if kind in ('crew', 'group') else 'engine')

    def _excluded(self, kind, restrictions):
        if kind not in restrictions:
            return set()
        return {(column, row) for row in range(self.fire.height) for column in range(self.fire.width)
                if math.dist([column, row], self._sector()) <= 5}

    def _access_effect(self, event):
        target = self._sector()
        effects = []
        for unit in self.ops.resources.values():
            if (unit['kind'] != event['resource_kind'] or unit['available_at'] > self.minute
                    or unit['duty_end_at'] <= self.minute or unit['water_litres'] <= 0):
                continue
            arguments = dict(mode=unit['kind'], burning=self._burning(), wind=list(self.fire.wind),
                             revision=0, limit=32)
            before = self.router.options(unit['position'], target, **arguments,
                excluded_cells=self._excluded(unit['kind'], self._restrictions))
            after = self.router.options(unit['position'], target, **arguments,
                excluded_cells=self._excluded(unit['kind'], self._restrictions | {event['resource_kind']}))
            effects.append(dict(resource_id=unit['id'], state=unit['state'],
                options_before=len(before['options']), options_after=len(after['options']),
                before_status=before['status'], after_status=after['status']))
        return dict(kind=event['kind'], minute=self.minute, resource_kind=event['resource_kind'],
                    resource_access=effects)

    def _movement_safe(self, journey, minute, kind):
        if kind == 'aircraft':
            return True
        return self.router.safe_journey_segment(journey, minute - 1, self._burning(), list(self.fire.wind),
            excluded_cells=self._excluded(kind, self._restrictions), mode='crew' if kind == 'group' else kind,
            egress_status=(self._scenario['profile'].get('emergency_egress_policy') == 'return-only-buffer-exit-v1'
                           and journey.get('_trusted_return_egress') is True))

    def _suppress(self, unit):
        target = unit.get('target_grid')
        if target is None:
            return False
        for column, row in sorted(self._burning()):
            position = grid_point(self.inputs, unit['position'])
            if (math.dist([column, row], target) <= 3
                    and (unit['kind'] == 'aircraft' or math.dist([column, row], position) <= 3)):
                cell = self.fire.cells[row][column]
                cell['heat'] = max(0., cell['heat'] - (.15 if unit['kind'] == 'aircraft' else .06))
                cell['wet'] = max(cell['wet'], 5)
                self._work_log.append(dict(resource_id=unit['id'], location=[column, row],
                                           position=unit['position'][:], state=unit['state']))
                return True
        return False

    def _exposure(self, group):
        point = grid_point(self.inputs, group['position'])
        threatened = any(math.dist(point, cell) <= 1.5 for cell in self._burning())
        if threatened:
            self._dose[group['id']] += 2 if group['status'] == 'sheltering' else 10
        return tuple(point) in self._burning() or self._dose[group['id']] >= 180

    def _land_guard(self):
        return bool(self._uncertain_land and any(
            max(abs(column - other_column), abs(row - other_row)) <= 1
            for column, row in self._burning() for other_column, other_row in self._uncertain_land))

    def _record(self, initial=False):
        footprint = self._burned()
        record = dict(minute=self.minute, new_footprint=sorted(footprint - self._footprint),
            resources={key: {field: copy.deepcopy(unit.get(field)) for field in ('state', 'position', 'water_litres',
                'work_remaining', 'total_work_remaining', 'fuel_minutes', 'due_at', 'duty_end_at')}
                for key, unit in self.ops.resources.items()},
            destinations={key: destination['status'] for key, destination in self.ops.destinations.items()},
            wind=list(self.fire.wind), applied_work=copy.deepcopy(self._work_log),
            population={key: dict(count=group['count'], exposed=group['count'] if group['exposed'] else 0,
                status=group['status'], protection_pending=any(order['group_id'] == key
                    and order['state'] not in ('completed', 'cancelled', 'unable_to_comply')
                    for order in self.ops.orders.values())) for key, group in self.ops.groups.items()})
        if initial:
            record.update(footprint=sorted(footprint), cell_area_hectares=self.inputs['grid']['cell_km'] ** 2 * 100,
                          burnable=[row * self.fire.width + column for row, cells in enumerate(self.fire.cells)
                                    for column, cell in enumerate(cells) if cell['initial_fuel'] > 0])
        if self._scenario['profile'].get('emergency_egress_policy') == 'return-only-buffer-exit-v1':
            record['emergency_withdrawals'] = {identifier: copy.deepcopy(unit['journey']['emergency_egress'])
                for identifier, unit in self.ops.resources.items()
                if (unit.get('journey') or {}).get('_trusted_return_egress') is True}
        if self._scenario['profile'].get('causal_coverage'):
            record['event_effects'] = copy.deepcopy(getattr(self, '_event_effects', []))
            for identifier, group in self.ops.groups.items():
                record['population'][identifier].update(position=group['position'][:],
                                                       destination_id=group.get('destination_id'))
        self._footprint.update(footprint)
        self._counter.update(record['new_footprint'])
        self._audit.append(record)
        if self.artifact_dir and not initial:
            path = self.artifact_dir / 'private' / 'audit.jsonl'
            with path.open('a') as stream:
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + '\n')

    def outcomes(self):
        return self._counter.result(self.ops.groups, self.minute, self.terminated, self.abandoned, self.censored,
                                    self.ops.orders)

    def _info(self, error, wake):
        value = dict(interface_version=VERSION, minute=self.minute, error=error, wake_reason=wake)
        if self._input_censored:
            value['model_limit'] = 'unresolved_land_or_water_not_a_firebreak'
        if self.terminated or self.truncated:
            value['outcomes'] = self.outcomes()
        return value

    def trajectory(self):
        return copy.deepcopy(dict(interface_version=VERSION, task_id=self._scenario['id'], seed=self._scenario['seed'],
            inputs_sha256=self.inputs['sha256'], network_sha256=self.network.sha256,
            scenario_sha256=self._scenario['sha256'], implementation_sha256=implementation_digest(),
            initial_observation=self._initial_observation, queries=self._query_log,
            rejected_calls=self._invalid_log, transitions=self._actions))

    def checkpoint(self):
        fields = ('minute', 'decision', 'invalid_streak', 'terminated', 'truncated', 'censored', 'abandoned',
            'reward', '_scenario', '_known_resources', '_known_groups', '_known_destinations', '_known_wind',
            '_known_revision', '_offers', '_issued_orders', '_commands', '_approaches', '_closed_centers',
            '_dose', '_events', '_audit', '_actions', '_prehistory', '_initial_observation',
            '_known_air_unavailable_until', '_offer_serial', '_query_log', '_signals', '_invalid_log', '_known_wind_at',
            '_work_log')
        state = {key: copy.deepcopy(getattr(self, key)) for key in fields}
        state.update(memory=list(self._memory.values()), footprint=sorted(self._footprint),
            _input_censored=self._input_censored,
            known_restrictions=sorted(self._known_restrictions),
            restrictions=sorted(self._restrictions), air_unavailable_until=getattr(self, '_air_unavailable_until', 0),
            pending_requests=sorted(self._pending_requests),
            outcome_counter=self._counter.checkpoint(),
            fire=self.fire.checkpoint(), operations=self.ops.checkpoint(), communications=self.bus.checkpoint())
        value = dict(version=VERSION, implementation_sha256=implementation_digest(), inputs_sha256=self.inputs['sha256'],
                     network_sha256=self.network.sha256, state=state)
        value = json.loads(json.dumps(value, allow_nan=False))
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        value = copy.deepcopy(checkpoint)
        if (value.get('version') != VERSION or value.get('inputs_sha256') != self.inputs['sha256']
                or value.get('network_sha256') != self.network.sha256
                or value.get('implementation_sha256') != implementation_digest()
                or digest({key: item for key, item in value.items() if key != 'sha256'}) != value.get('sha256')):
            raise ValueError('Checkpoint identity or integrity differs')
        state = value['state']
        self.fire = DayFire.restore(state.pop('fire'))
        self.ops = Operations.restore(state.pop('operations'), return_planner=self._return_route)
        self.bus = EventBus.restore(state.pop('communications'))
        self._counter = OutcomeCounter.restore(state.pop('outcome_counter'))
        self._memory = {(cell['x'], cell['y']): cell for cell in state.pop('memory')}
        self._footprint = set(state.pop('footprint'))
        self._restrictions = set(state.pop('restrictions'))
        self._known_restrictions = set(state.pop('known_restrictions'))
        self._pending_requests = set(state.pop('pending_requests'))
        self._air_unavailable_until = state.pop('air_unavailable_until')
        for key, item in state.items():
            setattr(self, key, item)
        self._configure_router()
        if self.artifact_dir:
            self._start_artifacts()
        return self.get_situation()

    def save_checkpoint(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(self.checkpoint(), sort_keys=True, allow_nan=False).encode(), mtime=0))
        path.chmod(0o600)

    def _append_artifacts(self, transition):
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        with (self.artifact_dir / 'trajectory.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(transition=transition, observation=self.get_situation()),
                                    sort_keys=True, allow_nan=False) + '\n')

    def _start_artifacts(self):
        if self.artifact_dir.exists() and any(self.artifact_dir.iterdir()):
            raise ValueError('Use a fresh artifact directory for a reset or resumed segment')
        private = self.artifact_dir / 'private'
        private.mkdir(parents=True, exist_ok=True)
        private.chmod(0o700)
        with (private / 'audit.jsonl').open('w') as stream:
            for record in self._audit:
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + '\n')
        (private / 'audit.jsonl').chmod(0o600)
        header = self.trajectory()
        header.pop('transitions')
        (self.artifact_dir / 'header.json').write_text(json.dumps(header, sort_keys=True, allow_nan=False) + '\n')

    def verify_episode(self):
        if not self.terminated and not self.truncated:
            raise RuntimeError('Finish the episode before verification')
        replay = IncidentDayEnv(self.inputs, self.network.document, expected_inputs_sha256=self.inputs['sha256'],
            expected_network_sha256=self.network.sha256, allow_unapproved_data=self.inputs['approval_status'] != 'approved_synthetic_fixture')
        replay.reset(self._scenario['id'], self._scenario['seed'])
        for transition in self._actions:
            for query in [query for query in self._query_log if query['before_decision'] == replay.decision]:
                result = getattr(replay, query['tool'])(**query['arguments'])
                if digest(result) != query['result_sha256']:
                    raise ValueError('Public route query did not replay exactly')
            replay.step(transition['action'])
            if replay._actions[-1] != transition:
                raise ValueError('Incident-day transition did not replay exactly')
        if digest(replay._audit) != digest(self._audit) or replay.outcomes() != self.outcomes():
            raise ValueError('Independent audit differs from replay')
        independent = recompute(self._audit, terminal=self.terminated, abandoned=self.abandoned, censored=self.censored)
        for key, value in self.outcomes().items():
            if type(value) is float:
                if not math.isclose(value, independent[key], rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError('Independent reward does not agree')
            elif independent[key] != value:
                raise ValueError('Independent outcome does not agree')
        return dict(passed=True, outcomes=independent, exact_replay=True, independent_reward_agreement=True)
