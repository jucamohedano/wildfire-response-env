import copy
import math


VERSION = 'wildfire-public-reference-policies-v1'
POLICIES = ('passive', 'indiscriminate', 'cooperative', 'dispatcher')


def communication_view(observation, enabled=True):
    result = copy.deepcopy(observation)
    if not enabled:
        result['incident_events'] = []
        result['active_incidents'] = []
        result['reports'] = []
        result['sectors'] = [sector for sector in result['sectors']
                             if not sector['id'].startswith(('report:', 'event:'))]
        for group in result['districts']:
            group.update(smoke_present=None, smoke_dose_units=None)
    return result


class PublicDispatcher:
    def __init__(self, policy='dispatcher', *, communications=True):
        if policy not in POLICIES:
            raise ValueError('Unknown public reference policy')
        self.policy, self.communications = policy, communications

    def __call__(self, observation):
        if self.policy == 'passive':
            return {}
        observation = communication_view(observation, self.communications)
        minute = observation['minute']
        orders = {}
        fresh = {(cell['x'], cell['y']): cell for cell in observation['sensor_memory']
                 if minute - cell['observed_at'] <= 4}
        reports = observation.get('reports', [])
        events = observation.get('active_incidents', [])
        latest = {entry['id']: entry for entry in observation.get('protection_orders', [])}
        protections = []
        for group in observation['districts']:
            previous = latest.get(observation.get('latest_protection_orders', {}).get(group['id']))
            direct = any(cell['burning'] and math.dist(point, group['home']) <= 3 for point, cell in fresh.items())
            reported = any(report.get('location') is not None and math.dist(report['location'], group['home']) <= 3
                           and (tuple(report['location']) not in fresh
                                or fresh[tuple(report['location'])]['observed_at'] < report['observed_at']
                                or fresh[tuple(report['location'])]['burning'])
                           for report in reports)
            called = any(event.get('district_id') == group['id'] and event['status'] == 'open' for event in events)
            threatened = direct or reported or called or group.get('smoke_present') is True
            if self.policy == 'indiscriminate':
                threatened = True
            if not threatened or group.get('status') == 'safe':
                continue
            refuge = group.get('refuge')
            route_blocked = False
            if refuge and self.policy == 'dispatcher':
                distance = math.dist(group['home'], refuge)
                samples = max(1, math.ceil(distance * 2))
                for index in range(samples + 1):
                    point = tuple(round(group['home'][axis] + (refuge[axis] - group['home'][axis]) * index / samples)
                                  for axis in (0, 1))
                    route_blocked |= bool(fresh.get(point, {}).get('burning'))
            desired = 'shelter' if route_blocked else 'evacuate'
            if previous and previous['action'] == desired and previous['status'] not in ('failed', 'unable_to_comply'):
                continue
            protections.append(dict(recipient=group['id'], action=desired, channel='broadcast',
                                    resource_id=None, supersedes=previous['id'] if previous else None))
        if protections:
            orders['protection_orders'] = protections[:4]
        offers = [offer for offer in observation['offers'] if offer['status'] == 'offered' and offer['expires_at'] > minute]
        readbacks = {entry['offer_id']: entry for entry in observation['readbacks']}
        confirmations, authorizations, corrections = [], [], []
        for offer in offers:
            readback = readbacks.get(offer['id'])
            if readback is None:
                continue
            if readback['status'] == 'awaiting_confirmation':
                if any(readback[field] != offer[field] for field in ('resource_id', 'sector_id', 'mission')):
                    corrections.append(dict(readback_id=readback['id'], **{field: offer[field]
                        for field in ('resource_id', 'sector_id', 'mission')}))
                    continue
                confirmations.append(readback['id'])
            if readback['status'] in ('awaiting_confirmation', 'confirmed'):
                authorizations.append(offer['id'])
        for field, entries in (('authorizations', authorizations), ('readback_confirmations', confirmations),
                               ('readback_corrections', corrections)):
            if entries:
                orders[field] = entries
        if self.policy == 'indiscriminate':
            return orders
        assessments = {entry['report_id']: entry for entry in observation.get('report_assessments', [])}
        triage = []
        for report in reports:
            location = report.get('location')
            cell = fresh.get(tuple(location)) if location is not None else None
            if cell and cell['observed_at'] >= report['observed_at']:
                assessment = 'fire_observed' if cell['burning'] else 'no_fire_observed'
                if assessments.get(report['id'], {}).get('assessment') != assessment:
                    triage.append(dict(report_ids=[report['id']], assessment=assessment))
        if triage:
            orders['triage'] = triage[:12]
        clarified = {entry['report_id'] for entry in observation.get('clarification_requests', [])}
        unknown = [report['id'] for report in reports if report.get('location') is None and report['id'] not in clarified]
        if unknown:
            orders['clarifications'] = unknown[:4]
        sectors = {sector['id']: sector['center'] for sector in observation['sectors']}
        busy_until = observation.get('warning_team_busy_until', {})
        units = observation['resources']
        releases = []
        for unit in units:
            if unit['state'] == 'on_scene' and unit.get('target') is not None and busy_until.get(unit['id'], -1) <= minute:
                nearby = [cell for point, cell in fresh.items() if math.dist(point, unit['target']) <= 3]
                if nearby and not any(cell['burning'] for cell in nearby):
                    releases.append(unit['id'])
                elif unit['kind'] == 'engine' and unit.get('water_litres') == 0:
                    releases.append(unit['id'])
        if releases:
            orders['releases'] = releases[:12]
        targets = []
        for identifier, point in sectors.items():
            if identifier.startswith('protect:'):
                continue
            cell = fresh.get(tuple(point))
            if cell is not None and not cell['burning']:
                continue
            priority = sum(group['population'] for group in observation['districts'] if math.dist(point, group['home']) <= 4)
            if identifier.startswith('event:') and not any('event:' + event['id'] == identifier for event in events):
                continue
            targets.append((priority, identifier, point, 'suppress' if cell and cell['burning'] else 'recon'))
        targets.sort(key=lambda item: (-item[0], item[1]))
        assigned = [sectors[offer['sector_id']] for offer in offers if offer['sector_id'] in sectors]
        assigned.extend(unit['target'] for unit in units if unit.get('target') is not None
                        and unit['state'] in ('mobilizing', 'en_route', 'on_scene') and unit['id'] not in releases)
        available = [unit for unit in units if unit['state'] == 'available'
                     and unit['id'] not in observation.get('pending_resource_requests', [])
                     and (unit['kind'] != 'aircraft' or observation.get('aircraft_operable'))]
        requests = []
        dispatched = []
        pending_targets = [unit['target'] for unit in units if unit.get('target') is not None
                           and unit['state'] in ('mobilizing', 'en_route')]
        pending_targets.extend(sectors[offer['sector_id']] for offer in offers if offer['sector_id'] in sectors)
        for _, identifier, point, mission in targets:
            support = any(event['kind'] == 'crew_support_request' and math.dist(event['location'], point) <= 3 for event in events)
            if any(math.dist(point, chosen) <= 3 for chosen in dispatched):
                continue
            if support and any(math.dist(point, chosen) <= 3 for chosen in pending_targets):
                continue
            if not available or any(math.dist(point, occupied) <= 3 for occupied in assigned) and not support:
                continue
            if self.policy == 'dispatcher':
                estimates = observation.get('travel_estimates', {})
                available.sort(key=lambda unit: (estimates.get(unit['id'], {}).get(identifier, float('inf')),
                    unit['kind'] != 'engine' if mission == 'suppress' else unit['kind'] != 'crew', unit['id']))
            unit = available.pop(0)
            requests.append(dict(resource_id=unit['id'], sector_id=identifier, mission=mission))
            assigned.append(point)
            dispatched.append(point)
        if requests:
            orders['requests'] = requests[:12]
        return orders
