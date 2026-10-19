"""One director, delayed base offers, ground missions and finite aircraft sorties."""
import copy
import json
import math

from .data import geometry, load_pack
from .fire import RegionalFire
from .scenarios import get_scenario

VERSION = 'regional-response-v2-alpha.2'
REWARD_VERSION = 'regional-outcome-cost-v2'
WEIGHTS = dict(exposed_population=100, burned_terrain=1, unnecessary_evacuation=.1,
               committed_unit_minutes=.00005, redundant_notices=.002, invalid_actions=.05, abandonment=102,
               unresolved_incident=2)


class RegionalResponseEnv:
    def __init__(self, max_decisions=60, ticks_per_action=2):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 120:
            raise ValueError('max_decisions must be from 1 to 120')
        if type(ticks_per_action) is not int or not 1 <= ticks_per_action <= 10:
            raise ValueError('ticks_per_action must be from 1 to 10')
        self.max_decisions, self.ticks_per_action = max_decisions, ticks_per_action
        self.sim = None

    def reset(self, scenario_id='regional-000', seed=None, *, scenario=None):
        self.pack = load_pack()
        self.task = copy.deepcopy(scenario) if scenario is not None else get_scenario(scenario_id, self.pack)
        self.seed = self.task['seed'] if seed is None else seed
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('Expected a nonnegative seed')
        self.sim = RegionalFire(self.pack, self.seed)
        self.sim.set_wind(x=self.task['wind'][0], y=self.task['wind'][1])
        self.resources = {item['id']: dict(copy.deepcopy(item), state='available', water_litres=item.get('capacity_litres', 0),
                                         sector_id=None, mission=None, due_at=None) for item in self.pack['scenario_inventory']}
        self.resources['aircraft-las-arenas']['state'] = 'unavailable' if self.task['aircraft_ready_at'] else 'available'
        self.events = sorted(copy.deepcopy(self.task['events']), key=lambda item: (item['tick'], item['kind'] != 'wind'))
        self.report_events = sorted(copy.deepcopy(self.task['reports']), key=lambda item: (item['received_at'], item['id']))
        self.ledger, self.offers, self.memory = {}, {}, {}
        self.observed_sectors = {}
        self.queue, self.acks, self.pending_requests = [], [], set()
        self.notice_keys = set()
        self.counters = dict(committed_unit_minutes=0, unnecessary_evacuation=0, redundant_notices=0,
                             invalid_actions=0, abandonment=0)
        self.daylight, self.decisions, self.invalid_streak = True, 0, 0
        self.terminated = self.truncated = False
        self.serial = 0
        self.audit_minutes = []
        self.preview_remaining = 3
        self._events(0)
        self._receive()
        self._sense()
        self.previous_cost = self._cost()
        return self.observe(), self._info()

    def _info(self, error=None):
        return dict(schema_version=VERSION, reward_version=REWARD_VERSION, minute=self.sim.tick,
                    decision=self.decisions, error=error)

    def _events(self, minute):
        for event in [item for item in self.events if item['tick'] <= minute]:
            if event['kind'] == 'ignition':
                self.sim.add_fire(*event['location'])
            elif event['kind'] == 'wind':
                self.sim.set_wind(x=event['wind'][0], y=event['wind'][1])
            elif event['kind'] == 'daylight':
                self.daylight = event['value']
            else:
                raise ValueError('Unknown incident event')
            self.events.remove(event)
        aircraft = self.resources['aircraft-las-arenas']
        if aircraft['state'] == 'unavailable' and minute >= self.task['aircraft_ready_at']:
            aircraft['state'] = 'available'
            self._ack('las-arenas', 'director', 'availability_update', resource_id=aircraft['id'])

    def _receive(self):
        for report in [item for item in self.report_events if item['received_at'] <= self.sim.tick][:20]:
            self.ledger[report['id']] = {key: copy.deepcopy(report[key]) for key in
                ('id', 'observed_at', 'received_at', 'location', 'text', 'source', 'channel', 'source_role') if key in report}
            self.ledger[report['id']]['available_at'] = self.sim.tick
            self.report_events.remove(report)

    def reports(self, identifiers=None):
        if self.sim is None:
            raise RuntimeError('reset required')
        identifiers = list(self.ledger) if identifiers is None else identifiers
        if not isinstance(identifiers, list) or any(identifier not in self.ledger for identifier in identifiers):
            raise ValueError('Only received report IDs may be queried')
        return [copy.deepcopy(self.ledger[identifier]) for identifier in identifiers]

    def _sectors(self):
        sectors = {f'protect:{identifier}': dict(id=f'protect:{identifier}', center=group['home'][:])
                   for identifier, group in self.sim.groups.items()}
        for report in self.ledger.values():
            if report.get('location') is not None:
                sectors['report:'+report['id']] = dict(id='report:'+report['id'], center=report['location'][:])
        sectors.update(copy.deepcopy(self.observed_sectors))
        return sectors

    def _sense_at(self, point, radius, source):
        for row in range(max(0, point[1]-radius), min(self.sim.height, point[1]+radius+1)):
            for column in range(max(0, point[0]-radius), min(self.sim.width, point[0]+radius+1)):
                if math.dist([column, row], point) <= radius:
                    cell = self.sim.cells[row][column]
                    self.memory[(column, row)] = dict(x=column, y=row, burning=self.sim.burning(cell),
                        observed_at=self.sim.tick, source=source)
                    if self.sim.burning(cell):
                        identifier=f'observed:{column}:{row}'
                        self.observed_sectors[identifier]=dict(id=identifier,center=[column,row],observed_at=self.sim.tick)

    def _sense(self):
        for identifier, group in self.sim.groups.items():
            self._sense_at(group['home'], 1, 'synthetic-lookout:'+identifier)
        for unit in self.resources.values():
            if unit['state'] == 'on_scene':
                self._sense_at(unit['target'], 3, unit['id'])

    def _air_operable(self):
        return self.daylight and math.hypot(*self.sim.wind) <= self.pack['assumptions']['aircraft_grounding_wind_index']

    def _travel(self, unit, target):
        return self.pack['assumptions']['base_transit_minutes'][unit['home_base']] + abs(target[0]-self.sim.width//2)//4

    def _ack(self, source, recipient, kind, **fields):
        self.acks.append(dict(minute=self.sim.tick, source=source, recipient=recipient, kind=kind, **fields))

    def _validate(self, action):
        if not isinstance(action, dict) or set(action)-{'requests', 'authorizations', 'releases', 'messages', 'explanation'}:
            raise ValueError('Unknown action fields')
        if len(json.dumps(action, allow_nan=False).encode()) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        if not isinstance(action.get('explanation', ''), str) or len(action.get('explanation', '')) > 1000:
            raise ValueError('Explanation must be at most 1000 characters')
        for field in ('requests', 'authorizations', 'releases', 'messages'):
            if not isinstance(action.get(field, []), list) or len(action.get(field, [])) > 12:
                raise ValueError('Each action list is limited to twelve entries')
        selected = set()
        for request in action.get('requests', []):
            if not isinstance(request, dict) or set(request) != {'resource_id', 'sector_id', 'mission'}:
                raise ValueError('Request needs resource_id, sector_id and mission')
            identifier = request['resource_id']
            if identifier not in self.resources or identifier in selected or identifier in self.pending_requests:
                raise ValueError('Unknown, duplicate or already pending resource request')
            selected.add(identifier)
            if request['sector_id'] not in self._sectors() or request['mission'] not in ('recon', 'suppress'):
                raise ValueError('Choose a public sector and recon/suppress mission')
        for identifier in action.get('authorizations', []):
            offer = self.offers.get(identifier)
            if not offer or offer['status'] != 'offered' or offer['expires_at'] <= self.sim.tick:
                raise ValueError('Authorization requires a live offered allocation')
            if offer['resource_id'] in selected:
                raise ValueError('A resource may occur only once in an action')
            selected.add(offer['resource_id'])
        for identifier in action.get('releases', []):
            unit = self.resources.get(identifier)
            if not unit or identifier in selected or unit['state'] not in ('reserved', 'mobilizing', 'en_route', 'on_scene'):
                raise ValueError('Release requires an assigned resource, once')
            selected.add(identifier)
        for message in action.get('messages', []):
            if not isinstance(message, dict) or set(message) != {'recipient', 'purpose'}:
                raise ValueError('Message needs recipient and purpose')
            recipient, purpose = message['recipient'], message['purpose']
            if recipient in self.sim.groups:
                if purpose not in ('inform', 'evacuate'):
                    raise ValueError('Population messages must inform or evacuate')
            elif recipient in {base['id'] for base in self.pack['facilities']}:
                if purpose != 'status':
                    raise ValueError('Base status messages do not dispatch resources; use requests')
            else:
                raise ValueError('Unknown recipient')

    def _apply(self, action):
        for request in action.get('requests', []):
            self.serial += 1
            self.pending_requests.add(request['resource_id'])
            self.queue.append(dict(kind='request', due=self.sim.tick+1, request_id=f'request-{self.serial}',
                                   data=copy.deepcopy(request), target=self._sectors()[request['sector_id']]['center']))
            unit = self.resources[request['resource_id']]
            self._ack('director', unit['home_base'], 'aid_request_sent', resource_id=unit['id'], request_id=f'request-{self.serial}')
        for identifier in action.get('authorizations', []):
            offer = self.offers[identifier]
            offer['status'] = 'committed'
            unit = self.resources[offer['resource_id']]
            unit.update(state='mobilizing', mission=offer['mission'], sector_id=offer['sector_id'],
                        target=offer['target'][:], due_at=self.sim.tick+self.pack['assumptions']['mobilization_minutes'][unit['kind']])
            self._ack('director', unit['home_base'], 'allocation_authorized', offer_id=identifier, resource_id=unit['id'])
            for other in self.resources.values():
                if (other['home_base'] != unit['home_base'] and other['sector_id'] == unit['sector_id']
                        and other['state'] in ('mobilizing', 'en_route', 'on_scene')):
                    self.queue.append(dict(kind='coordination', due=self.sim.tick+1, data=dict(
                        source=unit['home_base'], recipient=other['home_base'], resource_id=unit['id'], sector_id=unit['sector_id'])))
        for identifier in action.get('releases', []):
            unit = self.resources[identifier]
            for offer in self.offers.values():
                if offer['resource_id'] == identifier and offer['status'] == 'offered':
                    offer['status'] = 'cancelled'
            if unit['state'] == 'reserved':
                unit.update(state='available', due_at=None)
            else:
                unit.update(state='returning', due_at=self.sim.tick+self._travel(unit, unit['target']))
            self._ack('director', unit['home_base'], 'release_ordered', resource_id=identifier)
        for message in action.get('messages', []):
            self.queue.append(dict(kind='message', due=self.sim.tick+(2 if message['recipient'] in self.sim.groups else 1),
                                   data=copy.deepcopy(message)))

    def _deliver(self):
        for item in [entry for entry in self.queue if entry['due'] <= self.sim.tick]:
            self.queue.remove(item)
            data = item['data']
            if item['kind'] == 'request':
                unit = self.resources[data['resource_id']]
                self.pending_requests.remove(unit['id'])
                if unit['state'] != 'available' or (unit['kind'] == 'aircraft' and not self._air_operable()):
                    self._ack(unit['home_base'], 'director', 'request_denied', resource_id=unit['id'], reason='busy_or_unavailable')
                    continue
                identifier = 'offer:'+item['request_id']
                offer = dict(data, id=identifier, status='offered', target=item['target'][:],
                             lead_time_minutes=self.pack['assumptions']['mobilization_minutes'][unit['kind']]+self._travel(unit, item['target']),
                             expires_at=self.sim.tick+self.pack['assumptions']['offer_ttl_minutes'])
                self.offers[identifier] = offer
                unit['state'] = 'reserved'
                self._ack(unit['home_base'], 'director', 'allocation_offered', offer_id=identifier, resource_id=unit['id'])
            elif item['kind'] == 'coordination':
                self._ack(data['source'], data['recipient'], 'mutual_aid_update',
                          resource_id=data['resource_id'], sector_id=data['sector_id'], via='director')
            else:
                recipient, purpose = data['recipient'], data['purpose']
                notice = (recipient, purpose)
                self.counters['redundant_notices'] += notice in self.notice_keys
                self.notice_keys.add(notice)
                if recipient in self.sim.groups:
                    applied = self.sim.apply_communications([dict(kind='zone_alert', district_id=recipient,
                        action=purpose, status='delivered', information='Simulated director instruction')])
                    effect = applied[0]['effect']
                else:
                    effect = 'inventory_status_returned'
                self._ack(recipient, 'director', 'message_delivered', purpose=purpose, effect=effect)

    def _resources(self):
        for offer in self.offers.values():
            if offer['status'] == 'offered' and offer['expires_at'] <= self.sim.tick:
                offer['status'] = 'expired'
                self.resources[offer['resource_id']]['state'] = 'available'
                self._ack('base', 'director', 'offer_expired', offer_id=offer['id'])
        for unit in self.resources.values():
            if unit['kind'] == 'aircraft' and not self._air_operable():
                if unit['state'] == 'mobilizing':
                    unit.update(state='available', due_at=None, sector_id=None, mission=None)
                    self._ack(unit['home_base'], 'director', 'sortie_cancelled', resource_id=unit['id'])
                elif unit['state'] == 'en_route':
                    unit.update(state='returning', due_at=self.sim.tick+self._travel(unit, unit['target']))
                    self._ack(unit['home_base'], 'director', 'sortie_aborted', resource_id=unit['id'])
            if unit['due_at'] is not None and unit['due_at'] <= self.sim.tick:
                state = unit['state']
                if state == 'mobilizing':
                    unit.update(state='en_route', due_at=self.sim.tick+self._travel(unit, unit['target']))
                    self._ack(unit['home_base'], 'director', 'resource_dispatched', resource_id=unit['id'])
                elif state == 'en_route':
                    unit.update(state='on_scene', due_at=None)
                    self._ack(unit['home_base'], 'director', 'resource_arrived', resource_id=unit['id'])
                elif state == 'returning':
                    if unit['kind'] == 'crew':
                        unit.update(state='available', due_at=None, sector_id=None, mission=None)
                        self._ack(unit['home_base'], 'director', 'resource_ready', resource_id=unit['id'])
                    else:
                        delay = self.pack['assumptions']['aircraft_refill_minutes' if unit['kind'] == 'aircraft' else 'engine_refill_minutes']
                        unit.update(state='refilling', due_at=self.sim.tick+delay)
                elif state == 'refilling':
                    unit.update(state='available', due_at=None, water_litres=unit.get('capacity_litres', 0), sector_id=None, mission=None)
                    self._ack(unit['home_base'], 'director', 'resource_ready', resource_id=unit['id'])
            if unit['state'] != 'on_scene':
                continue
            self._sense_at(unit['target'], 3, unit['id'])
            targets = [(cell['x'], cell['y']) for cell in self.memory.values()
                       if cell['burning'] and cell['observed_at'] == self.sim.tick
                       and math.dist([cell['x'], cell['y']], unit['target']) <= 3]
            if unit['kind'] == 'aircraft':
                if unit['mission'] == 'suppress' and targets and self._air_operable() and unit['water_litres']:
                    self.sim.suppress(targets, 5, self.pack['assumptions']['suppression_probability']['aircraft'])
                    unit['water_litres'] = 0
                    self._ack(unit['home_base'], 'director', 'water_drop', resource_id=unit['id'], litres=unit['capacity_litres'])
                unit.update(state='returning', due_at=self.sim.tick+self._travel(unit, unit['target']))
            elif unit['mission'] == 'suppress' and targets:
                if unit['kind'] == 'engine' and not unit['water_litres']:
                    unit.update(state='returning', due_at=self.sim.tick+self._travel(unit, unit['target']))
                    continue
                self.sim.suppress(targets, 3 if unit['kind'] == 'engine' else 1,
                                  self.pack['assumptions']['suppression_probability'][unit['kind']])
                if unit['kind'] == 'engine':
                    unit['water_litres'] = max(0, unit['water_litres']-self.pack['assumptions']['engine_water_use_litres_per_minute'])

    def _components(self):
        population = sum(group['count'] for group in self.sim.groups.values())
        values = dict(self.counters)
        values['unnecessary_evacuation'] /= population*120
        values.update(exposed_population=sum(group['burnt'] for group in self.sim.groups.values())/population,
                      burned_terrain=sum(cell['burned'] for row in self.sim.cells for cell in row)/(self.sim.width*self.sim.height),
                      unresolved_incident=int((self.terminated or self.truncated) and (
                          any(self.sim.burning(cell) for row in self.sim.cells for cell in row)
                          or any(event['kind']=='ignition' for event in self.events))))
        return values

    def _cost(self):
        components = self._components()
        return sum(WEIGHTS[key]*components[key] for key in WEIGHTS)

    def check_action(self, action):
        if self.sim is None or self.terminated or self.truncated:
            raise RuntimeError('A live episode is required')
        if self.preview_remaining == 0:
            raise ValueError('Three action checks per decision; advance time before checking again')
        self.preview_remaining -= 1
        try:
            self._validate(action)
            error = None
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exception:
            error = str(exception)[:200]
        return dict(valid=error is None, error=error, remaining_checks=self.preview_remaining,
                    scope='Current public action contract only; no prediction of allocation, suppression or future conditions')

    def step(self, action):
        if self.sim is None or self.terminated or self.truncated:
            raise RuntimeError('Reset a live episode before stepping')
        error = None
        try:
            self._validate(action)
            self._apply(copy.deepcopy(action))
            self.invalid_streak = 0
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exception:
            error = str(exception)[:200]
            self.counters['invalid_actions'] += 1
            self.invalid_streak += 1
        self.decisions += 1
        for _ in range(self.ticks_per_action):
            self.sim.tick += 1
            self._events(self.sim.tick)
            self._deliver()
            self._resources()
            self.sim.tick -= 1
            self.sim.step()
            self._sense()
            self.counters['committed_unit_minutes'] += sum(unit['state'] not in ('available', 'unavailable') for unit in self.resources.values())
            if not any(event['kind'] == 'ignition' for event in self.task['events']):
                self.counters['unnecessary_evacuation'] += sum(group['count'] for group in self.sim.groups.values() if group['status'] != 'unwarned')
            self.audit_minutes.append(dict(minute=self.sim.tick,
                resource_states={identifier: unit['state'] for identifier, unit in self.resources.items()},
                resource_water={identifier: unit['water_litres'] for identifier, unit in self.resources.items()},
                wind=list(self.sim.wind), daylight=self.daylight,
                population_status={identifier: group['status'] for identifier, group in self.sim.groups.items()}))
        self._receive()
        self.preview_remaining = 3
        self.terminated = (self.sim.tick >= self.task['minimum_ticks'] and not self.events and not self.report_events
            and not self.queue and not any(self.sim.burning(cell) for row in self.sim.cells for cell in row)
            and not any(group['status'] in ('evacuating', 'blocked') for group in self.sim.groups.values())
            and all(unit['state'] in ('available', 'unavailable') for unit in self.resources.values()))
        self.truncated = not self.terminated and (self.decisions >= self.max_decisions or self.invalid_streak >= 3)
        if self.invalid_streak >= 3:
            self.counters['abandonment'] = 1
        current = self._cost()
        reward = self.previous_cost-current
        self.previous_cost = current
        return self.observe(), reward, self.terminated, self.truncated, self._info(error)

    def observe(self):
        if self.sim is None:
            raise RuntimeError('reset required')
        return dict(schema_version=VERSION, minute=self.sim.tick, decision=self.decisions,
            geography=geometry(self.pack), terrain_basis='synthetic scrub; no measured roads or land cover',
            assumptions=dict(inventory='fictional per-base allocations, not historical readiness',
                             travel='assumed off-map transit times, not computed road or flight routes',
                             effectiveness='uncalibrated sector-level effects, not a prediction model'),
            rules=dict(time='one tick is one assumed model minute, not a calibrated forecast',
                objective='Protect people and resolve the incident; active or pending fire at the terminal horizon incurs a penalty',
                wind='relative vector points TO spread; positive X east, positive Y south; not m/s',
                inform='does not initiate population movement', evacuate='movement begins only after delivery',
                dispatch='request -> base offer -> director authorization -> mobilization -> travel -> arrival',
                authority='simulated provincial coordinator; authorization requires an offer; not official dispatch procedure',
                offer_timing='lead_time_minutes is assumed mobilization plus travel after authorization, not a historical measurement',
                aircraft='one finite water load per sortie; returns and refills; no hovering suppression',
                grounding='synthetic daylight/wind gate, not aviation safety guidance'),
            wind=list(self.sim.wind), daylight=self.daylight, aircraft_operable=self._air_operable(),
            bases=[{key: base[key] for key in ('id', 'name', 'type', 'coordinates')} for base in self.pack['facilities']],
            resources=copy.deepcopy(list(self.resources.values())), offers=copy.deepcopy(list(self.offers.values())),
            reports=self.reports(), sectors=list(self._sectors().values()),
            districts=[dict(id=identifier, name=group['name'], population=group['count'],
                home=group['home'][:], position=[group['x'],group['y']], refuge=group['refuge'][:],
                status='exposed' if group['status']=='burnt' else group['status'], exposed=group['burnt'], basis='synthetic')
                for identifier, group in self.sim.groups.items()],
            sensor_memory=copy.deepcopy(sorted(self.memory.values(), key=lambda cell:(cell['y'],cell['x']))),
            acknowledgements=copy.deepcopy(self.acks[-80:]),
            pending_deliveries=[dict(kind=item['kind'], due_at=item['due']) for item in self.queue])

    def evaluator_state(self):
        return dict(components=self._components(), cost=self._cost(), events=copy.deepcopy(self.events),
                    scenario=copy.deepcopy(self.task), fire_checkpoint=self.sim.checkpoint(),
                    audit_minutes=copy.deepcopy(self.audit_minutes), communications=copy.deepcopy(self.acks))
