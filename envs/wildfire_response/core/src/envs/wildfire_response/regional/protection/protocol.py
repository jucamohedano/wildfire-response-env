"""Ordered simulated instructions with separate receipt, acknowledgement and action."""
import copy
import math

from ..ops.evidence import local_threat


VERSION = 'wildfire-protection-instructions-v1'
FIELDS = {'recipient', 'action', 'channel', 'resource_id', 'supersedes'}


class ProtectionProtocol:
    def __init__(self, configuration):
        self.configuration = copy.deepcopy(configuration)
        self.orders = {}
        self.latest = {}
        self.received_sequence = {}
        self.effective = {}
        self.warning_busy = {}
        self.redundant_orders = 0
        self.unsupported_orders = 0

    def validate(self, orders, core, action):
        if not isinstance(orders, list) or len(orders) > 4:
            raise ValueError('At most four structured protection orders')
        recipients, teams = set(), set()
        assigned = {request['resource_id'] for request in action.get('requests', [])}
        assigned.update(action.get('releases', []))
        assigned.update(core.offers[identifier]['resource_id'] for identifier in action.get('authorizations', []))
        for order in orders:
            if not isinstance(order, dict) or set(order) != FIELDS:
                raise ValueError('Protection order needs recipient, action, channel, resource_id and supersedes')
            recipient = order['recipient']
            if not isinstance(recipient, str) or recipient not in core.sim.groups or recipient in recipients:
                raise ValueError('Choose each public population recipient once per action')
            recipients.add(recipient)
            if order['action'] not in ('shelter', 'evacuate', 'release'):
                raise ValueError('Choose shelter, evacuate or release')
            if order['supersedes'] != self.latest.get(recipient):
                raise ValueError('Supersedes must identify the latest issued order, or null for the first')
            resource_id = order['resource_id']
            if order['channel'] == 'broadcast':
                if resource_id is not None:
                    raise ValueError('Broadcast orders do not assign a warning team')
            elif order['channel'] == 'in_person':
                unit = core.resources.get(resource_id) if isinstance(resource_id, str) else None
                if (unit is None or unit['kind'] != 'crew' or unit['state'] != 'on_scene'
                        or math.dist(unit['target'], core.sim.groups[recipient]['home']) > 2
                        or self.warning_busy.get(resource_id, -1) > core.sim.tick
                        or resource_id in assigned or resource_id in teams):
                    raise ValueError('In-person warning needs one unoccupied on-scene crew within two grid cells')
                home = core.sim.groups[recipient]['home']
                sensor = next((cell for cell in core.observe()['sensor_memory']
                               if [cell['x'], cell['y']] == home), None)
                if sensor is None or sensor['burning'] or core.sim.tick - sensor['observed_at'] > 4:
                    raise ValueError('In-person contact needs fresh public evidence that the home cell is clear')
                teams.add(resource_id)
            else:
                raise ValueError('Choose broadcast or in_person')
        if any(self.warning_busy.get(identifier, -1) > core.sim.tick for identifier in assigned):
            raise ValueError('A warning team remains occupied until its service completes')

    def issue(self, orders, core):
        observation = core.observe()
        districts = {district['id']: district for district in observation['districts']}
        for order in orders:
            recipient = order['recipient']
            previous = self.orders.get(self.latest.get(recipient))
            sequence = previous['sequence'] + 1 if previous else 1
            earlier = [record for record in self.orders.values()
                       if record['recipient'] == recipient and record['channel'] == 'broadcast'
                       and record['status'] != 'duplicate_ignored']
            delay = self.configuration['broadcast_delay_minutes'] if order['channel'] == 'broadcast' else 4
            if order['channel'] == 'broadcast' and not earlier:
                delay += self.configuration['first_broadcast_extra_delay_minutes']
            identifier = f'protection-{len(self.orders) + 1}'
            record = dict(copy.deepcopy(order), id=identifier, sequence=sequence,
                          issued_at=core.sim.tick, due_at=core.sim.tick + delay, status='pending',
                          delivered_at=None, acknowledged_at=None, executed_at=None,
                          attempted_at=None,
                          effect=None, superseded_by=None, superseded_at_recipient=None, duplicate_of=None)
            record['text'] = (f"EXERCISE. RECIPIENT {recipient}. {order['action'].upper()}. "
                              f"ORDER {identifier}. SEQUENCE {sequence}. ACKNOWLEDGE.")
            if (previous and previous['action'] == order['action']
                    and previous['status'] not in ('failed', 'unable_to_comply')):
                record.update(status='duplicate_ignored', duplicate_of=previous['id'],
                              sequence=previous['sequence'], due_at=None)
                record['text'] = f"EXERCISE. DUPLICATE IGNORED. ORIGINAL ORDER {previous['id']}."
                self.orders[identifier] = record
                self.redundant_orders += 1
                core._ack('director', recipient, 'duplicate_protection_ignored',
                          order_id=identifier, original_order_id=previous['id'])
                continue
            if previous:
                previous['superseded_by'] = identifier
            threatened = (local_threat(observation, districts[recipient])
                          or districts[recipient].get('smoke_present', False))
            self.unsupported_orders += (not threatened if order['action'] in ('shelter', 'evacuate') else threatened)
            self.orders[identifier] = record
            self.latest[recipient] = identifier
            will_fail = (order['channel'] == 'broadcast'
                         and len(earlier) < self.configuration['broadcast_failures'])
            core.queue.append(dict(kind='protection_delivery', due=record['due_at'],
                                   data={'order_id': identifier}, will_fail=will_fail))
            if order['channel'] == 'in_person':
                self.warning_busy[order['resource_id']] = record['due_at']
            core._ack('director', recipient, 'protection_order_issued', order_id=identifier,
                      action=order['action'], sequence=sequence, channel=order['channel'])

    def deliver(self, core):
        priority = {'protection_delivery': 0, 'protection_ack': 1, 'protection_execute': 2}
        due = [item for item in core.queue if item['kind'] in priority and item['due'] <= core.sim.tick]
        for item in sorted(due, key=lambda entry: (entry['due'], priority[entry['kind']], entry['data']['order_id'])):
            core.queue.remove(item)
            record = self.orders[item['data']['order_id']]
            recipient = record['recipient']
            if item['kind'] == 'protection_delivery':
                if item['will_fail']:
                    record['status'] = 'failed'
                    core._ack(recipient, 'director', 'protection_delivery_failed', order_id=record['id'])
                    continue
                record['delivered_at'] = core.sim.tick
                if record['sequence'] < self.received_sequence.get(recipient, 0):
                    record['status'] = 'stale_ignored'
                    core._ack(recipient, 'director', 'stale_protection_ignored', order_id=record['id'])
                    continue
                self.received_sequence[recipient] = record['sequence']
                for older in self.orders.values():
                    if (older['recipient'] == recipient and older['sequence'] < record['sequence']
                            and older['delivered_at'] is not None):
                        if older['superseded_at_recipient'] is None:
                            older['superseded_at_recipient'] = core.sim.tick
                        if older['status'] in ('delivered', 'acknowledged'):
                            older['status'] = 'superseded'
                record['status'] = 'delivered'
                core._ack(recipient, 'director', 'protection_delivered', order_id=record['id'])
                for kind, delay in [('protection_ack', 1), ('protection_execute', 2)]:
                    core.queue.append(dict(kind=kind, due=core.sim.tick + delay,
                                           data={'order_id': record['id']}))
            elif record['sequence'] != self.received_sequence.get(recipient):
                continue
            elif item['kind'] == 'protection_ack':
                record.update(status='acknowledged', acknowledged_at=core.sim.tick)
                core._ack(recipient, 'director', 'protection_acknowledged', order_id=record['id'],
                          readback_action=record['action'])
            else:
                complied, effect = core.sim.execute_protection(recipient, record['action'])
                record.update(status='executed' if complied else 'unable_to_comply',
                              attempted_at=core.sim.tick,
                              executed_at=core.sim.tick if complied else None, effect=effect)
                if complied:
                    self.effective[recipient] = record['id']
                core._ack(recipient, 'director', 'protection_' + record['status'],
                          order_id=record['id'], effect=effect)

    def public_state(self):
        return dict(protection_orders=copy.deepcopy(list(self.orders.values())),
                    latest_protection_orders=copy.deepcopy(self.latest),
                    last_executed_protection_orders=copy.deepcopy(self.effective),
                    warning_team_busy_until=copy.deepcopy(self.warning_busy))
