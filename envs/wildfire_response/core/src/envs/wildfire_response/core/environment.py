"""Programmatic, observation-bounded emergency coordination episodes."""
import copy
import json

from simulator.engine import Simulation
from . import reward
from .scenarios import get_task

SCHEMA_VERSION = 'wildfire-response-v1'
ORDER_KEYS = ('scout_orders', 'extinguisher_orders', 'truck_orders')
VEHICLE_FIELDS = ('drone_id', 'truck_id', 'role', 'x', 'y', 'mode', 'status', 'target',
                  'waypoints', 'evacuation_group', 'observed_fire')


class EpisodeSimulation(Simulation):
    def observe(self):
        key = (self.tick, tuple((vehicle.get('drone_id') or vehicle.get('truck_id'),
                                vehicle['x'], vehicle['y'], vehicle.get('sensor_radius'))
                               for vehicle in self.vehicles()))
        if getattr(self, '_observation_key', None) != key:
            result = super().observe()
            self._observation_key = key
            return result
        return self.observation

    def add_fire(self, column, row):
        self._observation_key = None
        return super().add_fire(column, row)

    def suppress(self, candidates, jets, success_probability):
        self._observation_key = None
        return super().suppress(candidates, jets, success_probability)

    def update_completion(self):
        """Episode lifecycle belongs to the wrapper, including future ignitions."""


class WildfireResponseEnv:
    def __init__(self, max_decisions=32, ticks_per_action=8):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 128:
            raise ValueError('max_decisions must be an integer from 1 to 128')
        if type(ticks_per_action) is not int or not 1 <= ticks_per_action <= 32:
            raise ValueError('ticks_per_action must be an integer from 1 to 32')
        self.max_decisions = max_decisions
        self.ticks_per_action = ticks_per_action
        self.sim = None

    def reset(self, scenario_id=None, seed=None, *, scenario=None):
        self.task = copy.deepcopy(scenario) if scenario is not None else get_task(scenario_id)
        self.seed = self.task['seed'] if seed is None else seed
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('seed must be a nonnegative integer')
        self.sim = EpisodeSimulation(seed=self.seed, fleet_counts=self.task['fleet'],
                                     incident_id=f"{self.task['id']}:{self.seed}")
        self.sim.ignited = self.sim.called = True
        self.sim.set_wind(x=self.task['wind'][0], y=self.task['wind'][1])
        self.sim.set_spread_factor(self.task['spread_factor'])
        self.sim.report = self.sim.base
        for truck in self.sim.trucks:
            truck['drone_order'] = {'command': 'hold'}
        self.decisions = self.invalid_streak = 0
        self.terminated = self.truncated = False
        self.ledger = {}
        self.dispositions = {}
        self.acks = []
        self.queue = []
        self.notice_keys = set()
        self.report_events = sorted(copy.deepcopy(self.task['reports']), key=lambda item: (item['received_at'], item['id']))
        self.events = sorted(copy.deepcopy(self.task['events']), key=lambda item: (item['tick'], item['kind']))
        self.counters = dict(unnecessary_person_ticks=0, redundant_messages=0, invalid_actions=0, abandoned_episode=0)
        self.prior_action = None
        self.last_batch = []
        self._process_events(0)
        self._receive_reports()
        self.sim.observe()
        self.previous_components = reward.components(self.sim, self.counters)
        self.previous_cost = reward.cost(self.previous_components)
        return self.observe(), self._info()

    def _info(self, error=None):
        return dict(schema_version=SCHEMA_VERSION, reward_version=reward.REWARD_VERSION,
                    tick=self.sim.tick, decision=self.decisions, error=error)

    def _process_events(self, tick):
        due = [item for item in self.events if item['tick'] <= tick]
        self.events = [item for item in self.events if item['tick'] > tick]
        for item in sorted(due, key=lambda event: event['kind'] != 'wind'):
            if item['kind'] == 'wind':
                self.sim.set_wind(x=item['wind'][0], y=item['wind'][1])
            elif item['kind'] == 'ignition':
                column, row = item['location']
                cell = self.sim.cells[row][column]
                if cell['fuel'] > 0 and cell.get('wet', 0) == 0:
                    self.sim.add_fire(column, row)
            else:
                raise ValueError('Unknown scenario event')

    def _receive_reports(self):
        due = [item for item in self.report_events if item['received_at'] <= self.sim.tick][:20]
        self.last_batch = [item['id'] for item in due]
        for item in due:
            public = {key: copy.deepcopy(item[key]) for key in
                      ('id', 'observed_at', 'received_at', 'source_id', 'source_role', 'channel', 'location', 'text')}
            public['available_at'] = self.sim.tick
            self.ledger[item['id']] = public
            self.report_events.remove(item)
        located = [item for item in due if item.get('location')]
        if located and self.sim.report == self.sim.base:
            self.sim.report = tuple(located[0]['location'])

    def reports(self, ids=None):
        if self.sim is None:
            raise RuntimeError('reset must be called first')
        if ids is None:
            return copy.deepcopy(list(self.ledger.values()))
        if not isinstance(ids, list) or any(ident not in self.ledger for ident in ids):
            raise ValueError('Only received report IDs may be queried')
        return [copy.deepcopy(self.ledger[ident]) for ident in ids]

    def observe(self):
        self.sim.observe()
        vehicles = [{key: copy.deepcopy(vehicle[key]) for key in VEHICLE_FIELDS if key in vehicle}
                    for vehicle in self.sim.vehicles()]
        districts = [dict(id=ident, name=group['name'], kind=group['kind'], home=group['home'][:],
                          position=[group['x'], group['y']], population=group['count'], status=group['status'],
                          burnt=group['burnt'], refuge=group['refuge'][:], contact_id=f'contact-{ident}')
                     for ident, group in self.sim.groups.items()]
        known_fire = [(cell['x'], cell['y']) for cell in self.sim.memory.values() if cell['burning']]
        blocked = self.sim.danger_zone(known_fire)
        approaches = []
        for report in self.ledger.values():
            if report.get('location'):
                rx, ry = report['location']
                for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4)):
                    point = [min(self.sim.width-1, max(0, rx+dx)), min(self.sim.height-1, max(0, ry+dy))]
                    if tuple(point) not in blocked and point not in approaches:
                        approaches.append(point)
        patrol = [[8, 8], [40, 8], [72, 8], [8, 48], [40, 48], [72, 48]]
        patrol = [point for point in patrol if tuple(point) not in blocked]
        containment = {drone['drone_id']: self.sim.safe_drone_positions(drone)[:12]
                       for drone in self.sim.extinguishers}
        satellite = copy.deepcopy(self.sim.satellite)
        if satellite is not None:
            satellite['blocks'] = [list(point) for point in satellite['blocks']]
        return dict(schema_version=SCHEMA_VERSION, tick=self.sim.tick, decision=self.decisions,
                    wind=list(self.sim.wind), size=[self.sim.width, self.sim.height], station=list(self.sim.base),
                    districts=districts, vehicles=vehicles, reports=self.reports(), new_report_ids=self.last_batch[:],
                    report_dispositions=copy.deepcopy(self.dispositions),
                    sensor_memory=copy.deepcopy(sorted(self.sim.memory.values(), key=lambda cell: (cell['y'], cell['x']))),
                    satellite=satellite,
                    candidates=dict(reconnaissance=approaches[:24], patrol=patrol, containment=containment,
                                    truck_sectors=[list(point) for point in sorted(known_fire)[:16]]),
                    delivery_acknowledgements=copy.deepcopy(self.acks[-50:]),
                    pending_deliveries=[dict(id=item['id'], due_at=item['due']) for item in self.queue],
                    prior_action=copy.deepcopy(self.prior_action))

    def _validate_action(self, action):
        if not isinstance(action, dict):
            raise ValueError('Action must be an object')
        if len(json.dumps(action, allow_nan=False).encode()) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        if set(action) - {'fleet', 'report_dispositions', 'messages', 'explanation'}:
            raise ValueError('Unknown action fields')
        if not isinstance(action.get('explanation', ''), str) or len(action.get('explanation', '')) > 2000:
            raise ValueError('Explanation must be a string of at most 2000 characters')
        dispositions = action.get('report_dispositions', [])
        messages = action.get('messages', [])
        if not isinstance(dispositions, list) or len(dispositions) > 100:
            raise ValueError('Supply at most 100 dispositions')
        if not isinstance(messages, list) or len(messages) > 12:
            raise ValueError('Supply at most 12 messages')
        seen = set()
        for item in dispositions:
            if not isinstance(item, dict) or item.get('report_id') not in self.ledger:
                raise ValueError('Disposition references an unreceived report')
            if item['report_id'] in seen or item.get('status') not in {'investigate', 'defer', 'dismiss'}:
                raise ValueError('Invalid or duplicate disposition')
            seen.add(item['report_id'])
            support = item.get('supporting_report_ids', [])
            if not isinstance(support, list) or any(ident not in self.ledger for ident in support):
                raise ValueError('Support must reference received report IDs')
        fleet = copy.deepcopy(action.get('fleet'))
        if not isinstance(fleet, dict) or any(not isinstance(fleet.get(key), list) for key in ORDER_KEYS):
            raise ValueError('Complete fleet order arrays required')
        candidate = copy.deepcopy(self.sim)
        candidate.apply(fleet, f'validate-{self.decisions}', candidate.incident_id, candidate.tick)
        vehicles = {vehicle.get('drone_id') or vehicle.get('truck_id') for vehicle in self.sim.vehicles()}
        recipients = set(self.sim.groups) | {f'contact-{ident}' for ident in self.sim.groups} | vehicles
        for item in messages:
            if not isinstance(item, dict) or item.get('recipient') not in recipients:
                raise ValueError('Unknown message recipient')
            if item.get('purpose') not in {'inform', 'investigate', 'evacuate'}:
                raise ValueError('Unknown message purpose')
            recipient = item['recipient']
            if item.get('channel') not in {'radio', 'broadcast', 'call'}:
                raise ValueError('Unknown message channel')
            is_unit = recipient in vehicles
            if is_unit != (item['channel'] == 'radio'):
                raise ValueError('Radio is for units; broadcast/call are for population')
            if recipient.startswith('contact-') != (item['channel'] == 'call'):
                raise ValueError('Calls require synthetic contact IDs')
            if (is_unit and item['purpose'] == 'evacuate') or (not is_unit and item['purpose'] == 'investigate'):
                raise ValueError('Purpose incompatible with recipient')
            support = item.get('report_ids', [])
            if not isinstance(support, list) or any(ident not in self.ledger for ident in support):
                raise ValueError('Message evidence must reference received reports')
        return fleet, dispositions, messages

    def step(self, action):
        if self.sim is None or self.terminated or self.truncated:
            raise RuntimeError('Reset required before stepping a new or completed episode')
        error = None
        try:
            action = copy.deepcopy(action)
            if isinstance(action, dict):
                action.setdefault('explanation', '')
                action.setdefault('messages', [])
                action.setdefault('report_dispositions', [])
                if isinstance(action.get('fleet'), dict):
                    action['fleet'].setdefault('mission', '')
                    action['fleet'].setdefault('reason', '')
                for item in action.get('messages', []) if isinstance(action.get('messages'), list) else []:
                    if isinstance(item, dict):
                        item.setdefault('report_ids', [])
                for item in action.get('report_dispositions', []) if isinstance(action.get('report_dispositions'), list) else []:
                    if isinstance(item, dict):
                        item.setdefault('supporting_report_ids', [])
            fleet, dispositions, messages = self._validate_action(action)
            self.invalid_streak = 0
            self.prior_action = copy.deepcopy(action)
            for item in dispositions:
                self.dispositions[item['report_id']] = copy.deepcopy(item)
            self.queue.append(dict(id=f'fleet-{self.decisions}', due=self.sim.tick+1, kind='fleet', data=fleet))
            for number, message in enumerate(messages):
                delay = {'radio': 1, 'broadcast': 2, 'call': 4}[message['channel']]
                self.queue.append(dict(id=f'message-{self.decisions}-{number}', due=self.sim.tick+delay,
                                       kind='message', data=copy.deepcopy(message)))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            error = str(exc)[:240]
            self.invalid_streak += 1
            self.counters['invalid_actions'] += 1
        self.decisions += 1
        for _ in range(self.ticks_per_action):
            tick = self.sim.tick+1
            self._process_events(tick)
            self._deliver(tick)
            self.sim.step()
            if not self.task['events']:
                self.counters['unnecessary_person_ticks'] += sum(group['count'] for group in self.sim.groups.values()
                                                                if group['status'] != 'unwarned')
        self._receive_reports()
        self.terminated = (self.sim.tick >= self.task.get('minimum_ticks', 64)
                           and not self.events and not self.report_events and not self.queue
                           and not any(self.sim.burning(cell) for row in self.sim.cells for cell in row)
                           and not any(group['status'] in {'evacuating', 'blocked'} for group in self.sim.groups.values()))
        self.truncated = not self.terminated and (self.decisions >= self.max_decisions or self.invalid_streak >= 3)
        if self.invalid_streak >= 3:
            self.counters['abandoned_episode'] = 1
        current = reward.components(self.sim, self.counters)
        current_cost = reward.cost(current)
        value = self.previous_cost-current_cost
        self.previous_components, self.previous_cost = current, current_cost
        return self.observe(), value, self.terminated, self.truncated, self._info(error)

    def _deliver(self, tick):
        due = [item for item in self.queue if item['due'] <= tick]
        self.queue = [item for item in self.queue if item['due'] > tick]
        for item in due:
            ack = dict(id=item['id'], delivered_at=tick, status='delivered')
            if item['kind'] == 'fleet':
                candidate = copy.deepcopy(self.sim)
                try:
                    candidate.apply(copy.deepcopy(item['data']), item['id'], candidate.incident_id, candidate.tick)
                    for truck in candidate.trucks:
                        if truck.get('crew_target') and truck.get('mobilized_at') is None:
                            truck.update(mobilized_at=tick+8, status='mobilizing')
                    self.sim = candidate
                except (ValueError, TypeError, KeyError) as exc:
                    ack.update(status='rejected', error=str(exc)[:200])
                    self.counters['invalid_actions'] += 1
            else:
                message = item['data']
                recipient, purpose = message['recipient'], message['purpose']
                notice = (recipient.removeprefix('contact-'), purpose)
                if notice in self.notice_keys:
                    self.counters['redundant_messages'] += 1
                self.notice_keys.add(notice)
                ack.update(recipient=recipient, purpose=purpose)
                if recipient in self.sim.groups or recipient.startswith('contact-'):
                    district = recipient.removeprefix('contact-')
                    applied = self.sim.apply_communications([dict(kind='zone_alert', district_id=district,
                        action='evacuate' if purpose == 'evacuate' else 'inform', status='delivered',
                        information=f"{purpose.title()} instructions for {district}. Use the designated refuge.")])
                    ack['effect'] = applied[0]['effect']
                else:
                    ack['effect'] = 'unit_informed'
            self.acks.append(ack)

    def evaluator_state(self):
        """Private artifact for scoring/export; never returned by the agent API."""
        return dict(scenario_id=self.task['id'], seed=self.seed, components=copy.deepcopy(self.previous_components),
                    cost=self.previous_cost, counters=copy.deepcopy(self.counters),
                    annotations=copy.deepcopy(self.task.get('private_annotations', [])),
                    checkpoint=self.sim.checkpoint())
