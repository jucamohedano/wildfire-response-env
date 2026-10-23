"""Deterministic base readbacks over public resource offers."""
import copy


PROTOCOL_VERSION = 'wildfire-ops-readback-v1'
PROTOCOL_FIELDS = {'readback_confirmations', 'readback_corrections'}
ORDER_FIELDS = ('resource_id', 'sector_id', 'mission')


class ReadbackProtocol:
    def __init__(self, *, mission_swap_once=False):
        self._fault_remaining = mission_swap_once
        self._readbacks = {}
        self._latest = {}
        self._queue = []
        self._events = []

    def validate(self, action, offers, minute):
        live_offers = {offer['id']: offer for offer in offers
                       if offer['status'] == 'offered' and offer['expires_at'] > minute}
        selected = set()
        confirmed_offers = set()
        for field in PROTOCOL_FIELDS:
            if not isinstance(action.get(field, []), list) or len(action.get(field, [])) > 12:
                raise ValueError('Each readback action list is limited to twelve entries')
        for identifier in action.get('readback_confirmations', []):
            readback, offer = self._pending(identifier, live_offers, selected)
            if any(readback[field] != offer[field] for field in ORDER_FIELDS):
                raise ValueError('Readback differs from the offer; send a correction before confirming')
            confirmed_offers.add(offer['id'])
        for correction in action.get('readback_corrections', []):
            if not isinstance(correction, dict) or set(correction) != {'readback_id', *ORDER_FIELDS}:
                raise ValueError('Correction needs readback_id, resource_id, sector_id and mission')
            _, offer = self._pending(correction['readback_id'], live_offers, selected)
            if any(correction[field] != offer[field] for field in ORDER_FIELDS):
                raise ValueError('Correction must restate the original offered allocation exactly')
        for offer_id in action.get('authorizations', []):
            readback_id = self._latest.get(offer_id)
            readback = self._readbacks.get(readback_id, {})
            if offer_id not in confirmed_offers and readback.get('status') != 'confirmed':
                raise ValueError('Authorization requires a matching confirmed readback')

    def _pending(self, identifier, live_offers, selected):
        if not isinstance(identifier, str):
            raise ValueError('Readback IDs must be strings')
        readback = self._readbacks.get(identifier)
        if (not readback or identifier in selected
                or readback['status'] != 'awaiting_confirmation'
                or self._latest.get(readback['offer_id']) != identifier
                or readback['offer_id'] not in live_offers):
            raise ValueError('Choose a live pending readback once per action')
        selected.add(identifier)
        return readback, live_offers[readback['offer_id']]

    def apply(self, action, minute):
        for identifier in action.get('readback_confirmations', []):
            readback = self._readbacks[identifier]
            readback['status'] = 'confirmed'
            self._event(minute, 'ops', readback['source'], 'readback_confirmed', readback)
        for correction in action.get('readback_corrections', []):
            readback = self._readbacks[correction['readback_id']]
            readback['status'] = 'correction_pending'
            self._queue.append(dict(due_at=minute + 1, previous_id=readback['id'],
                                    order={field: correction[field] for field in ORDER_FIELDS}))
            self._event(minute, 'ops', readback['source'], 'readback_correction_sent', readback)

    def receive(self, observation):
        minute = observation['minute']
        offers = {offer['id']: offer for offer in observation['offers']}
        resources = {unit['id']: unit for unit in observation['resources']}
        for offer in offers.values():
            if offer['status'] == 'offered' and offer['id'] not in self._latest:
                order = {field: offer[field] for field in ORDER_FIELDS}
                if self._fault_remaining:
                    order['mission'] = 'suppress' if order['mission'] == 'recon' else 'recon'
                    self._fault_remaining = False
                self._create(offer, resources, order, minute, attempt=1)
        for item in list(self._queue):
            if item['due_at'] > minute:
                continue
            self._queue.remove(item)
            previous = self._readbacks[item['previous_id']]
            offer = offers[previous['offer_id']]
            if offer['status'] == 'offered' and offer['expires_at'] > minute:
                previous['status'] = 'superseded'
                self._create(offer, resources, item['order'], item['due_at'],
                             attempt=previous['attempt'] + 1)
            else:
                previous['status'] = 'closed'
        for readback in self._readbacks.values():
            if (offers[readback['offer_id']]['status'] != 'offered'
                    and readback['status'] in ('awaiting_confirmation', 'correction_pending')):
                readback['status'] = 'closed'

    def _create(self, offer, resources, order, minute, attempt):
        identifier = f"readback:{offer['id']}:{attempt}"
        readback = dict(id=identifier, offer_id=offer['id'], attempt=attempt,
                        source=resources[offer['resource_id']]['home_base'], recipient='ops',
                        received_at=minute, status='awaiting_confirmation', **order)
        readback['text'] = (f"READBACK. RESOURCE {order['resource_id']}. "
                            f"SECTOR {order['sector_id']}. MISSION {order['mission'].upper()}. "
                            'CONFIRM OR CORRECT.')
        self._readbacks[identifier] = readback
        self._latest[offer['id']] = identifier
        self._event(minute, readback['source'], 'ops', 'readback_received', readback)

    def _event(self, minute, source, recipient, kind, readback):
        self._events.append(dict(minute=minute, source=source, recipient=recipient, kind=kind,
                                 readback_id=readback['id'], offer_id=readback['offer_id']))

    def public_state(self):
        return dict(readbacks=copy.deepcopy(list(self._readbacks.values())),
                    readback_events=copy.deepcopy(self._events[-80:]),
                    pending_readbacks=[dict(kind='readback', due_at=item['due_at'])
                                       for item in self._queue])
