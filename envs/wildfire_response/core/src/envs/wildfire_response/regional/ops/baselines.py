"""Public-observation policies for the assigned dispatch exercises."""
import random

from .communications import ORDER_FIELDS


def passive(observation):
    return {}


def _matches(order, objective, resources):
    unit = resources[order['resource_id']]
    return (order.get('sector_id') == objective['sector_id']
            and order.get('mission') in objective['acceptable_missions']
            and unit['kind'] in objective['acceptable_resource_kinds'])


class DispatchBaseline:
    def __call__(self, observation):
        action = dict(requests=[], authorizations=[], releases=[],
                      readback_confirmations=[], readback_corrections=[])
        completed = set(observation['completed_objective_ids'])
        objectives = sorted((objective for objective in observation['task_objectives']
                             if objective['id'] not in completed
                             and objective['deadline_minute'] >= observation['minute']),
                            key=lambda objective: (objective['deadline_minute'], objective['id']))
        resources = {unit['id']: unit for unit in observation['resources']}
        offers = [offer for offer in observation['offers']
                  if offer['status'] == 'offered' and offer['expires_at'] > observation['minute']]
        readbacks = {readback['offer_id']: readback for readback in observation['readbacks']}
        for offer in offers:
            if not any(_matches(offer, objective, resources) for objective in objectives):
                action['releases'].append(offer['resource_id'])
                continue
            readback = readbacks.get(offer['id'])
            if readback is None:
                continue
            if readback['status'] == 'awaiting_confirmation':
                if any(readback[field] != offer[field] for field in ORDER_FIELDS):
                    action['readback_corrections'].append(dict(readback_id=readback['id'],
                        **{field: offer[field] for field in ORDER_FIELDS}))
                else:
                    action['readback_confirmations'].append(readback['id'])
                    action['authorizations'].append(offer['id'])
            elif readback['status'] == 'confirmed':
                action['authorizations'].append(offer['id'])
        assignments = offers + [dict(resource_id=unit['id'], **{
            field: unit[field] for field in ('sector_id', 'mission')})
            for unit in resources.values() if unit['state'] in ('mobilizing', 'en_route', 'on_scene')]
        available = [unit for unit in observation['resources'] if unit['state'] == 'available']
        public_sectors = {sector['id'] for sector in observation['sectors']}
        for objective in objectives:
            if (objective['sector_id'] not in public_sectors
                    or any(_matches(order, objective, resources) for order in assignments)):
                continue
            candidates = [unit for unit in available
                          if unit['kind'] in objective['acceptable_resource_kinds']
                          and (unit['kind'] != 'aircraft' or observation['aircraft_operable'])]
            if not candidates:
                continue
            unit = candidates[0]
            available.remove(unit)
            order = dict(resource_id=unit['id'], sector_id=objective['sector_id'],
                         mission=objective['acceptable_missions'][0])
            action['requests'].append(order)
            assignments.append(order)
        for unit in resources.values():
            order = dict(resource_id=unit['id'], sector_id=unit['sector_id'], mission=unit['mission'])
            if unit['state'] == 'on_scene' and not any(
                    _matches(order, objective, resources) for objective in objectives):
                action['releases'].append(unit['id'])
        return action


class RandomValidBaseline:
    """Random subsets of valid dispatch-policy actions, including whole auth bundles."""
    def __init__(self, seed):
        self._random = random.Random(seed)
        self._dispatch = DispatchBaseline()

    def __call__(self, observation):
        proposed = self._dispatch(observation)
        action = {field: [entry for entry in proposed[field] if self._random.random() < .5]
                  for field in ('requests', 'releases', 'readback_corrections')}
        action['authorizations'] = []
        action['readback_confirmations'] = []
        for offer_id in proposed['authorizations']:
            if self._random.random() >= .5:
                continue
            action['authorizations'].append(offer_id)
            action['readback_confirmations'].extend(
                readback['id'] for readback in observation['readbacks']
                if readback['offer_id'] == offer_id
                and readback['id'] in proposed['readback_confirmations'])
        return action
