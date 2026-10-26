"""Public-observation policies for the assigned dispatch exercises."""
import random
import math

from .communications import ORDER_FIELDS
from .evidence import MAX_EVIDENCE_AGE, local_threat


def passive(observation):
    return {}


def notice_needed(observation, recipient, purpose):
    attempts = [record for record in observation.get('message_deliveries', [])
                if record['recipient'] == recipient and record['purpose'] == purpose]
    if attempts:
        return attempts[-1]['status'] == 'failed' and not any(
            record['status'] in ('pending', 'delivered') for record in attempts)
    return not any(notice['recipient'] == recipient and notice['purpose'] == purpose
                   for notice in observation['issued_notices'])


def _matches(order, objective, resources):
    unit = resources[order['resource_id']]
    return (order.get('sector_id') == objective['sector_id']
            and order.get('mission') in objective['acceptable_missions']
            and unit['kind'] in objective['acceptable_resource_kinds'])


class DispatchBaseline:
    def __init__(self):
        self._visited = set()

    def __call__(self, observation):
        if not any(objective['kind'] == 'resource_arrival' for objective in observation['task_objectives']):
            sectors = {sector['id']: sector for sector in observation['sectors']}
            self._visited.update(tuple(sectors[unit['sector_id']]['center'])
                                 for unit in observation['resources'] if unit['state'] == 'on_scene')
            observation = dict(observation, task_objectives=[
                dict(id='visit:' + report['id'], kind='resource_arrival',
                     sector_id='report:' + report['id'], acceptable_resource_kinds=['engine'],
                     acceptable_missions=['recon'], deadline_minute=120)
                for report in observation['reports'] if report.get('location') is not None
                and tuple(report['location']) not in self._visited])
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
        available = [unit for unit in observation['resources'] if unit['state'] == 'available'
                     and unit['id'] not in observation.get('pending_resource_requests', [])]
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
        self._control = ControlBaseline()

    def __call__(self, observation):
        policy = self._dispatch if observation['task_set'] == 'dispatch' else self._control
        proposed = policy(observation)
        action = {field: [entry for entry in proposed[field] if self._random.random() < .5]
                  for field in ('requests', 'releases', 'readback_corrections', 'triage', 'messages')
                  if field in proposed}
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


def blanket_warning(observation):
    return dict(messages=[dict(recipient=district['id'], purpose='evacuate')
                          for district in observation['districts'] if district['status'] == 'unwarned'
                          and notice_needed(observation, district['id'], 'evacuate')])


class ControlBaseline:
    def __call__(self, observation):
        action = dict(requests=[], authorizations=[], releases=[], messages=[], triage=[],
                      readback_confirmations=[], readback_corrections=[])
        memory = {(cell['x'], cell['y']): cell for cell in observation['sensor_memory']}
        fresh = {point: cell for point, cell in memory.items()
                 if observation['minute'] - cell['observed_at'] <= MAX_EVIDENCE_AGE}
        assessments = {record['report_id']: record for record in observation['report_assessments']}
        groups = {}
        for report in observation['reports']:
            if report.get('location') is None:
                continue
            point = tuple(report['location'])
            cell = fresh.get(point)
            if cell is not None and cell['observed_at'] >= report['observed_at']:
                state = 'fire_observed' if cell['burning'] else 'no_fire_observed'
                if assessments.get(report['id'], {}).get('assessment') != state:
                    groups.setdefault((point, report['observed_at'], state), []).append(report['id'])
        for (_, _, state), identifiers in groups.items():
            for offset in range(0, len(identifiers), 20):
                action['triage'].append(dict(report_ids=identifiers[offset:offset + 20], assessment=state))
        action['triage'] = action['triage'][:12]
        for district in observation['districts']:
            nearby_reports = [report for report in observation['reports']
                              if report.get('location') is not None
                              and math.dist(report['location'], district['home']) <= 3]
            if (district['status'] == 'unwarned' and notice_needed(observation, district['id'], 'evacuate')
                    and local_threat(dict(observation, reports=nearby_reports), district)):
                action['messages'].append(dict(recipient=district['id'], purpose='evacuate'))
        resources = {unit['id']: unit for unit in observation['resources']}
        offers = [offer for offer in observation['offers']
                  if offer['status'] == 'offered' and offer['expires_at'] > observation['minute']]
        readbacks = {readback['offer_id']: readback for readback in observation['readbacks']}
        for offer in offers:
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
        burning = [point for point, cell in fresh.items() if cell['burning']]
        sectors = {sector['id']: sector for sector in observation['sectors']}
        for unit in resources.values():
            if (unit['state'] == 'on_scene' and not any(
                    math.dist(sectors[unit['sector_id']]['center'], point) <= 3 for point in burning)):
                action['releases'].append(unit['id'])
        candidates = {}
        for report in observation['reports']:
            location = report.get('location')
            if location is None:
                continue
            point = tuple(location)
            cell = fresh.get(point)
            previous = assessments.get(report['id'], {})
            if (previous.get('assessment') == 'no_fire_observed'
                    and previous['evidence_observed_at'] >= report['observed_at']
                    and not any(math.dist(point, fire) <= 3 for fire in burning)):
                continue
            if cell is None or cell['observed_at'] < report['observed_at'] or cell['burning']:
                candidates.setdefault(point, 'report:' + report['id'])
        for point in burning:
            if not any(math.dist(point, target) <= 3 for target in candidates):
                identifier = f'observed:{point[0]}:{point[1]}'
                if identifier in sectors:
                    candidates[point] = identifier
        assigned = [sectors[offer['sector_id']]['center'] for offer in offers]
        assigned.extend(sectors[unit['sector_id']]['center'] for unit in resources.values()
                        if unit['state'] in ('mobilizing', 'en_route', 'on_scene')
                        and unit['id'] not in action['releases'])
        targets = sorted(candidates, key=lambda point: (
            min(math.dist(point, district['home']) for district in observation['districts']), point))
        available = [unit for unit in observation['resources'] if unit['state'] == 'available'
                     and unit['id'] not in observation.get('pending_resource_requests', [])
                     and (unit['kind'] != 'aircraft' or observation['aircraft_operable'])]
        for point in targets:
            if any(math.dist(point, target) <= 3 for target in assigned) or not available:
                continue
            unit = available.pop(0)
            action['requests'].append(dict(resource_id=unit['id'], sector_id=candidates[point], mission='suppress'))
            assigned.append(point)
        return action
