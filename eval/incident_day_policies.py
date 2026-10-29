"""Deterministic, public-interface validation policies for incident day.

These are exercise policies, not operational recommendations.  Intervention
limits and checkpoint identity are part of the policy version.
"""
import json
import math

from envs.wildfire_response.incident_day.baselines import DayDispatcher

POLICY_NAMES = ('passive', 'dispatcher', 'conservative', 'stress')
_VERSION = 2
_ACTIVE_ORDERS = {'issued', 'delivered', 'acknowledged', 'executing', 'blocked'}


class ValidationPolicy:
    def __init__(self, name):
        if name not in POLICY_NAMES:
            raise ValueError('Unknown validation policy')
        self.name = name
        self.dispatcher = DayDispatcher('passive' if name == 'passive' else 'dispatcher')
        self.released = set()
        self.sheltered = set()
        self.status_sent = False
        self.report_cursor = 0

    def checkpoint(self):
        # Dispatcher keys contain mixed types, so sort by their JSON encoding.
        asked = [list(item) for item in self.dispatcher.asked]
        asked.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(',', ':')))
        return dict(version=_VERSION, policy=self.name, asked=asked,
                    released=sorted(self.released), sheltered=sorted(self.sheltered),
                    status_sent=self.status_sent, report_cursor=self.report_cursor)

    def restore(self, state):
        if not isinstance(state, dict) or state.get('version') != _VERSION or state.get('policy') != self.name:
            raise ValueError('Incompatible policy checkpoint')
        try:
            asked_items = state['asked']
            if not isinstance(asked_items, list) or not all(
                isinstance(item, list) and (
                    (len(item) == 2 and isinstance(item[0], str) and type(item[1]) is int)
                    or (len(item) == 3 and item[0] == 'protection'
                        and isinstance(item[1], str) and type(item[2]) is int)
                    or (len(item) == 4 and item[0] == 'closure'
                        and isinstance(item[1], str) and (item[2] is None or isinstance(item[2], str))
                        and type(item[3]) is int)) for item in asked_items):
                raise ValueError('Invalid dispatcher checkpoint')
            asked = {tuple(item) for item in asked_items}
            if not isinstance(state['released'], list) or not isinstance(state['sheltered'], list):
                raise ValueError('Invalid policy checkpoint')
            released = set(state['released'])
            sheltered = set(state['sheltered'])
            status_sent, cursor = state['status_sent'], state['report_cursor']
            if (not all(isinstance(x, str) for x in released | sheltered)
                    or type(status_sent) is not bool or type(cursor) is not int or cursor < 0):
                raise ValueError('Invalid policy checkpoint')
        except (KeyError, TypeError) as error:
            raise ValueError('Invalid policy checkpoint') from error
        self.dispatcher.asked = asked
        self.released, self.sheltered = released, sheltered
        self.status_sent, self.report_cursor = status_sent, cursor

    def _checked(self, environment, action):
        return bool(environment.check_action(action).get('valid'))

    def action(self, public_environment, observation):
        if self.name in ('passive', 'dispatcher'):
            return self.dispatcher.action(public_environment, observation)

        minute = observation['minute']
        if self.name == 'stress':
            # Bounded ledger reads, including reports no longer in the recent view.
            count = observation.get('report_count', 0)
            if self.report_cursor < count:
                records = public_environment.get_reports(after_cursor=self.report_cursor, limit=20)
                if not isinstance(records, list):
                    raise TypeError('get_reports must return a list of delivered reports')
                self.report_cursor += len(records)

        # A limited, scheduled release lets return and refill unfold naturally.
        release_limit = 1 if self.name == 'conservative' else 2
        release_after = 0 if self.name == 'conservative' else 5
        if minute >= release_after and len(self.released) < release_limit:
            for unit in sorted(observation['resources'], key=lambda unit: unit['id']):
                if unit['id'] in self.released or unit['state'] not in ('on_scene', 'working'):
                    continue
                draft = {'releases': [unit['id']]}
                if self._checked(public_environment, draft):
                    self.released.add(unit['id'])
                    return draft

        if self.name == 'conservative':
            # One shelter notice at a known stationary location, never repeatedly.
            if not self.sheltered:
                pending = {order['group_id'] for order in observation.get('protection_orders', [])
                           if order.get('state') in _ACTIVE_ORDERS}
                for group in sorted(observation['districts'], key=lambda group: group['id']):
                    if (group['id'] in pending or group.get('exposed')
                            or group.get('status') not in ('unwarned', 'at_home', 'arrived')):
                        continue
                    positions = [group.get('home')]
                    positions += [destination.get('position') for destination in observation['destinations']
                                  if destination['id'] == group.get('destination_id')]
                    position = group.get('position')
                    if not (isinstance(position, (list, tuple)) and len(position) == 2
                            and all(type(value) in (int, float) and math.isfinite(value) for value in position)
                            and any(isinstance(point, (list, tuple)) and len(point) == 2
                                    and all(type(value) in (int, float) and math.isfinite(value) for value in point)
                                    and math.dist(position, point) < 0.01 for point in positions)):
                        continue
                    order = dict(group_id=group['id'], action='shelter', destination_id=None, approach_id=None)
                    draft = {'protection_orders': [order]}
                    if self._checked(public_environment, draft):
                        self.sheltered.add(group['id'])
                        return draft
            if not self.status_sent and observation['centers']:
                draft = {'status_requests': [dict(center_id=sorted(observation['centers'])[0], subject='situation')]}
                if self._checked(public_environment, draft):
                    self.status_sent = True
                    return draft
            return {'advance_minutes': 60}

        draft = self.dispatcher.action(public_environment, observation)
        # Validate even waits to exercise the public draft interface.  A rejected
        # draft is never committed; its dispatcher state remains deterministic.
        if self._checked(public_environment, draft):
            return draft
        return {'advance_minutes': 60}


def make_policy(name):
    return ValidationPolicy(name)
