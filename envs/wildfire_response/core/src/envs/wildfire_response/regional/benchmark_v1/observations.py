"""Actor knowledge consists exclusively of static declarations and delivered snapshots."""
import copy


VERSION = 'wildfire-benchmark-delivered-evidence-v1'
DELAYS = dict(population=2, operations=1)
DISTRICT_DYNAMIC = ('position', 'status', 'exposed', 'smoke_dose_units', 'shelter_active',
                    'smoke_present', 'smoke_observed_at')
RESOURCE_DYNAMIC = ('state', 'water_litres', 'sector_id', 'mission', 'due_at', 'target',
                    'arrival_at', 'arrival_in_minutes')
COMMAND_COLLECTIONS = ('protection_orders', 'message_deliveries', 'clarification_requests')


def stamped(record, observed_at, received_at):
    return {**copy.deepcopy(record), 'observed_at': observed_at, 'received_at': received_at}


def without_due(record):
    return {key: copy.deepcopy(value) for key, value in record.items() if key != 'due_at'}


class DeliveredEvidence:
    def __init__(self, observation):
        self.pending = []
        self.population = [dict({key: copy.deepcopy(group[key]) for key in
                                 ('id', 'name', 'population', 'home', 'refuge', 'basis')},
                                **{field: None for field in DISTRICT_DYNAMIC},
                                observed_at=None, received_at=None) for group in observation['districts']]
        self.resources = [dict({key: copy.deepcopy(value) for key, value in unit.items()
                                if key not in RESOURCE_DYNAMIC},
                               **{field: None for field in RESOURCE_DYNAMIC},
                               observed_at=None, received_at=None) for unit in observation['resources']]
        self.operations = dict(sensor_memory=[], observed_sectors=[], wind=None, daylight=None,
                               aircraft_operable=None, observed_at=None, received_at=None)
        self.commands = {key: {} for key in COMMAND_COLLECTIONS}
        self.acknowledgements = []
        self.pending_requests = set()

    def capture(self, observation):
        minute = observation['minute']
        self.pending.append(dict(kind='population', due=minute + DELAYS['population'],
                                 minute=minute, payload=copy.deepcopy(observation['districts'])))
        fields = ('resources', 'sensor_memory', 'wind', 'daylight', 'aircraft_operable',
                  *COMMAND_COLLECTIONS)
        operations = {key: copy.deepcopy(observation[key]) for key in fields}
        operations['observed_sectors'] = [sector for sector in observation['sectors']
                                           if sector['id'].startswith('observed:')]
        self.pending.append(dict(kind='operations', due=minute + DELAYS['operations'],
                                 minute=minute, payload=operations))

    def acknowledge(self, acknowledgement):
        local = acknowledgement['source'] in ('director', 'ops')
        if local:
            self._receive_ack(acknowledgement, acknowledgement['minute'])
        else:
            self.pending.append(dict(kind='ack', due=acknowledgement['minute'] + 1,
                                     minute=acknowledgement['minute'], payload=copy.deepcopy(acknowledgement)))

    def _receive_ack(self, record, minute):
        self.acknowledgements.append(stamped(record, record['minute'], minute))
        resource = record.get('resource_id')
        if record['kind'] == 'aid_request_sent':
            self.pending_requests.add(resource)
        elif record['kind'] in ('allocation_offered', 'request_denied'):
            self.pending_requests.discard(resource)

    def issued(self, observation):
        for key in COMMAND_COLLECTIONS:
            for record in observation[key]:
                identifier = record.get('id', record.get('report_id'))
                if identifier not in self.commands[key]:
                    self.commands[key][identifier] = stamped(without_due(record),
                                                            observation['minute'], observation['minute'])

    def deliver(self, minute):
        for item in list(self.pending):
            if item['due'] > minute:
                continue
            self.pending.remove(item)
            payload, observed, received = item['payload'], item['minute'], item['due']
            if item['kind'] == 'ack':
                self._receive_ack(payload, received)
            elif item['kind'] == 'population':
                self.population = [stamped(group, observed, received) for group in payload]
            else:
                self.resources = [stamped(unit, observed, received) for unit in payload['resources']]
                self.operations = {key: copy.deepcopy(payload[key]) for key in self.operations
                                   if key not in ('observed_at', 'received_at')}
                self.operations.update(observed_at=observed, received_at=received)
                self.operations['sensor_memory'] = [stamped(cell, cell['observed_at'], received)
                                                     for cell in payload['sensor_memory']]
                for key in COMMAND_COLLECTIONS:
                    for record in payload[key]:
                        identifier = record.get('id', record.get('report_id'))
                        previous = self.commands[key].get(identifier)
                        if previous is None or previous['observed_at'] <= observed:
                            self.commands[key][identifier] = stamped(without_due(record), observed, received)

    def present(self, observation):
        public = copy.deepcopy(observation)
        public['districts'] = copy.deepcopy(self.population)
        public['resources'] = copy.deepcopy(self.resources)
        for key in ('sensor_memory', 'wind', 'daylight', 'aircraft_operable'):
            public[key] = copy.deepcopy(self.operations[key])
        public['conditions_observed_at'] = self.operations['observed_at']
        public['conditions_received_at'] = self.operations['received_at']
        public['sectors'] = [sector for sector in public['sectors']
                             if not sector['id'].startswith('observed:')]
        public['sectors'].extend(copy.deepcopy(self.operations['observed_sectors']))
        public['pending_deliveries'] = []
        public['pending_resource_requests'] = sorted(self.pending_requests)
        for key in COMMAND_COLLECTIONS:
            public[key] = copy.deepcopy(list(self.commands[key].values()))
        public['acknowledgements'] = copy.deepcopy(self.acknowledgements[-80:])
        public['last_executed_protection_orders'] = {
            record['recipient']: record['id'] for record in public['protection_orders']
            if record['status'] == 'executed'}
        public['warning_team_busy_until'] = {
            record['resource_id']: record['issued_at'] + 4 for record in public['protection_orders']
            if record['channel'] == 'in_person' and record['status'] != 'duplicate_ignored'}
        known_offer_ids = {record['offer_id'] for record in self.acknowledgements
                           if record['kind'] == 'allocation_offered'}
        public['offers'] = [offer for offer in public['offers'] if offer['id'] in known_offer_ids]
        public['evidence_delivery_version'] = VERSION
        public['observation_version'] = VERSION
        return public
