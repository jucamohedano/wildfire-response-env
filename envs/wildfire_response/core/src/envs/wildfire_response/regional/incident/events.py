import copy
import hashlib
import math


VERSION = 'wildfire-reactive-incident-events-v1'
RULES = dict(max_events=64, resident_delay_minutes=2, radio_delay_minutes=1,
             followup_minutes=8, crew_observation_radius_cells=3,
             engine_capacity_cells=3, crew_capacity_cells=1)


def covered_cells(core, observed):
    radius = RULES['crew_observation_radius_cells']
    busy = getattr(getattr(core, 'protection', None), 'warning_busy', {})
    slots = []
    for unit in core.resources.values():
        if (unit['state'] != 'on_scene' or unit['kind'] not in ('engine', 'crew')
                or unit['mission'] != 'suppress' or busy.get(unit['id'], -1) > core.sim.tick
                or unit['kind'] == 'engine' and unit['water_litres'] <= 0):
            continue
        count = RULES['engine_capacity_cells'] if unit['kind'] == 'engine' else RULES['crew_capacity_cells']
        slots.extend([unit['target']] * count)
    assigned = {}

    def assign(cell_index, seen):
        cell = observed[cell_index]
        for slot, point in enumerate(slots):
            if slot in seen or math.dist([cell['x'], cell['y']], point) > radius:
                continue
            seen.add(slot)
            if slot not in assigned or assign(assigned[slot], seen):
                assigned[slot] = cell_index
                return True
        return False

    return sum(assign(index, set()) for index in range(len(observed)))


class IncidentEvents:
    def __init__(self, seed, horizon):
        self.seed, self.horizon = seed, horizon
        self.created = 0
        self.delivered = []
        self.conditions = {}
        self.unit_states = {}

    def _emit(self, core, incident_id, signature, *, kind, source, role, channel,
              location, text, status='open', priority='normal', repeat=False, **fields):
        minute = core.sim.tick
        previous = self.conditions.get(incident_id)
        if previous and previous['signature'] == signature:
            if not repeat or minute - previous['minute'] < RULES['followup_minutes']:
                return
        delay = RULES['resident_delay_minutes'] if channel == 'synthetic_phone' else RULES['radio_delay_minutes']
        future = {**self.conditions, incident_id: dict(status=status, priority=priority)}
        reserve = sum(1 + (condition['priority'] != 'urgent') for condition in future.values()
                      if condition['status'] == 'open')
        if self.created + 1 + reserve > RULES['max_events'] or minute + delay > self.horizon:
            return
        wording = ('EXERCISE UPDATE. ', 'SIMULATED CALL. ' if channel == 'synthetic_phone' else 'SIMULATED RADIO. ')
        variant = hashlib.sha256(f'{self.seed}:{incident_id}:{self.created}'.encode()).digest()[0] % len(wording)
        event = dict(incident_id=incident_id, kind=kind, source=source, source_role=role,
                     channel=channel, location=location[:], observed_at=minute,
                     text=wording[variant] + text, status=status, priority=priority, **fields)
        core.queue.append(dict(kind='incident_update', due=minute + delay, data=event))
        self.created += 1
        self.conditions[incident_id] = dict(signature=signature, minute=minute, status=status,
                                            priority=priority, location=location[:])

    def deliver(self, core):
        due = [item for item in core.queue if item['kind'] == 'incident_update' and item['due'] <= core.sim.tick]
        for item in sorted(due, key=lambda entry: entry['due']):
            core.queue.remove(item)
            event = copy.deepcopy(item['data'])
            previous = next((entry for entry in reversed(self.delivered)
                             if entry['incident_id'] == event['incident_id']), None)
            event.update(id=f'incident-event-{len(self.delivered) + 1}', received_at=core.sim.tick,
                         related_event_id=previous['id'] if previous else None)
            self.delivered.append(event)

    def detect(self, core):
        population = core.sim.population_state()
        for identifier, group in core.sim.groups.items():
            state = population[identifier]
            incident = 'smoke:' + identifier
            protected = state['shelter_active'] or group['status'] in ('evacuating', 'safe')
            if state['smoke_present']:
                urgent = state['smoke_dose_units'] >= core.task['protection']['exposure_threshold'] / 2
                if protected:
                    self._emit(core, incident, 'protected', kind='resident_protection_update',
                        source='resident:' + identifier, role='resident', channel='synthetic_phone',
                        location=[round(group['x']), round(group['y'])], district_id=identifier,
                        text=f'{identifier}: we have started following the protection instruction; smoke is still present.',
                        status='resolved')
                else:
                    self._emit(core, incident, ('smoke', urgent), kind='resident_smoke_call',
                        source='resident:' + identifier, role='resident', channel='synthetic_phone',
                        location=[round(group['x']), round(group['y'])], district_id=identifier,
                        text=f'{identifier}: smoke is still here. We need protection instructions.' if incident in self.conditions
                             else f'{identifier}: we can see smoke from our location. Please advise.',
                        priority='urgent' if urgent else 'normal', repeat=True)
            elif incident in self.conditions and self.conditions[incident]['signature'] != 'clear':
                self._emit(core, incident, 'clear', kind='resident_smoke_cleared',
                    source='resident:' + identifier, role='resident', channel='synthetic_phone',
                    location=[round(group['x']), round(group['y'])], district_id=identifier,
                    text=f'{identifier}: we no longer observe smoke here. This does not confirm the whole fire is out.',
                    status='resolved')
        for unit in core.resources.values():
            previous = self.unit_states.get(unit['id'], 'available')
            self.unit_states[unit['id']] = unit['state']
            if previous != unit['state'] and unit.get('target') is not None:
                self._emit(core, 'unit:' + unit['id'], (unit['state'], unit.get('due_at')),
                    kind='resource_status_update', source=unit['id'], role='responder', channel='synthetic_radio',
                    location=unit['target'], resource_id=unit['id'], status='resolved',
                    text=f"{unit['id']}: {unit['state'].replace('_', ' ')}. Water remaining: {unit.get('water_litres', 0)} litres.")
            if unit['state'] != 'on_scene':
                incident = 'support:' + unit['id']
                if self.conditions.get(incident, {}).get('status') == 'open':
                    self._emit(core, incident, 'withdrawn', kind='crew_support_withdrawn',
                        source=unit['id'], role='responder', channel='synthetic_radio',
                        location=unit.get('target') or self.conditions[incident]['location'],
                        resource_id=unit['id'], status='resolved',
                        text=f"{unit['id']}: reporting unit has left the scene. Withdraw this unit's request; reassess local support needs.")
                continue
            radius = RULES['crew_observation_radius_cells']
            observed = [cell for cell in core.memory.values()
                        if cell['observed_at'] == core.sim.tick and cell['burning']
                        and math.dist([cell['x'], cell['y']], unit['target']) <= radius]
            capacity = covered_cells(core, observed)
            shortage = len(observed) > capacity
            incident = 'support:' + unit['id']
            if shortage or incident in self.conditions:
                self._emit(core, incident, shortage, kind='crew_support_request' if shortage else 'crew_support_resolved',
                    source=unit['id'], role='responder', channel='synthetic_radio', location=unit['target'],
                    resource_id=unit['id'], priority='urgent' if shortage else 'normal',
                    status='open' if shortage else 'resolved', repeat=shortage,
                    text=(f"{unit['id']}: {len(observed)} nearby burning cells observed; arrived suppression capacity "
                          f'is {capacity} exercise cells. Request additional suppression support.' if shortage else
                          f"{unit['id']}: the local support deficit has cleared. Continue monitoring."))

    def public_state(self):
        latest = {event['incident_id']: event for event in self.delivered}
        return dict(incident_event_version=VERSION, incident_events=copy.deepcopy(self.delivered),
                    event_cursor=len(self.delivered),
                    active_incidents=copy.deepcopy([event for event in latest.values() if event['status'] == 'open']))
