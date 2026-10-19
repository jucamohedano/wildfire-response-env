"""Policies consume only the public observation dictionary."""
import copy
import json
import math
import random
from pathlib import Path
from types import SimpleNamespace

from simulator.policy import DeterministicFleetPolicy


def passive(observation):
    fleet = dict(scout_orders=[], extinguisher_orders=[], truck_orders=[], mission='Hold current positions', reason='Passive baseline')
    for vehicle in observation['vehicles']:
        ident = vehicle.get('drone_id') or vehicle.get('truck_id')
        if vehicle['role'] == 'truck':
            fleet['truck_orders'].append(dict(truck_id=ident, command='hold', reason='Passive baseline'))
        elif vehicle['role'] == 'scout':
            fleet['scout_orders'].append(dict(drone_id=ident, command='hold', waypoints=[], reason='Passive baseline'))
        else:
            fleet['extinguisher_orders'].append(dict(drone_id=ident, command='hold', target_x=round(vehicle['x']),
                                                    target_y=round(vehicle['y']), reason='Passive baseline'))
    return dict(fleet=fleet, report_dispositions=[], messages=[])


def _view(observation, reports):
    vehicles = copy.deepcopy(observation['vehicles'])
    groups = {item['id']: dict(kind=item['kind'], name=item['name'], count=item['population'],
                              status=item['status'], home=item['home']) for item in observation['districts']}
    memory = {f"{cell['x']},{cell['y']}": cell for cell in observation['sensor_memory']}
    fire = [cell for cell in memory.values() if cell['burning'] and cell['observed_at'] == observation['tick']]
    located = [report for report in reports if report['location']]
    report = located[0]['location'] if located else observation['station']
    def alignment():
        result = {}
        sources = [[cell['x'], cell['y']] for cell in fire] or ([report] if located else [])
        for key, group in groups.items():
            measurements = []
            for source in sources:
                dx, dy = group['home'][0]-source[0], group['home'][1]-source[1]
                distance = math.hypot(dx, dy)
                norm = distance*math.hypot(*observation['wind'])
                cosine = (dx*observation['wind'][0]+dy*observation['wind'][1])/norm if norm else 0
                measurements.append(dict(distance=distance, downwind_sector=cosine >= .7))
            result[key] = dict(downwind_sector=any(item['downwind_sector'] for item in measurements), measurements=measurements)
        return result
    return SimpleNamespace(wind=observation['wind'], groups=groups, observation=fire, memory=memory,
        scouts=[item for item in vehicles if item['role'] == 'scout'],
        extinguishers=[item for item in vehicles if item['role'] == 'extinguisher'],
        trucks=[item for item in vehicles if item['role'] == 'truck'], called=bool(located), report=report,
        base=observation['station'], observe=lambda: fire, population_wind_alignment=alignment,
        safe_drone_positions=lambda drone: observation['candidates']['containment'][drone['drone_id']],
        smoke_scout_positions=lambda: [dict(x=point[0], y=point[1]) for point in observation['candidates']['reconnaissance']],
        scout_search_waypoints=lambda scout, limit: observation['candidates']['patrol'][:limit])


class DeterministicBaseline:
    name = 'deterministic'

    def choose_reports(self, observation):
        return [report for report in observation['reports'] if report['location'] and report['text'].startswith('Smoke near grid')]

    def __call__(self, observation):
        reports = self.choose_reports(observation)
        view = _view(observation, reports)
        fleet = DeterministicFleetPolicy().decide(view)
        urgent = set(DeterministicFleetPolicy._urgent_districts(view))
        pending = {(ack.get('recipient'), ack.get('purpose')) for ack in observation['delivery_acknowledgements']
                   if ack['status'] == 'delivered'}
        messages = []
        for district in observation['districts']:
            purpose = 'evacuate' if district['id'] in urgent else 'inform'
            if (district['id'], purpose) in pending or district['status'] != 'unwarned':
                continue
            if purpose == 'evacuate' or view.observation:
                messages.append(dict(recipient=district['id'], purpose=purpose, channel='broadcast',
                                     report_ids=[report['id'] for report in reports[:1]]))
        dispositions = []
        seen = {}
        for report in observation['reports']:
            signature = (tuple(report['location']) if report['location'] else None, report['text'])
            duplicate = seen.get(signature)
            dispositions.append(dict(report_id=report['id'], status='dismiss' if duplicate else
                                     'investigate' if report['location'] else 'defer',
                                     supporting_report_ids=[duplicate] if duplicate else []))
            seen.setdefault(signature, report['id'])
        return dict(fleet=fleet, report_dispositions=dispositions, messages=messages[:12])


class RandomValidBaseline:
    name = 'random'

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def __call__(self, observation):
        action = passive(observation)
        candidates = observation['candidates']
        for order in action['fleet']['extinguisher_orders']:
            choices = [('hold', None)]
            choices += [('scout', point) for point in candidates['reconnaissance']]
            choices += [('contain', [point['x'], point['y']]) for point in candidates['containment'][order['drone_id']]]
            command, point = self.rng.choice(choices)
            order['command'] = command
            if point:
                order.update(target_x=point[0], target_y=point[1])
        for order in action['fleet']['scout_orders']:
            points = candidates['patrol'] + candidates['reconnaissance']
            if points:
                order.update(command='patrol', waypoints=[self.rng.choice(points)])
        for order in action['fleet']['truck_orders']:
            if candidates['truck_sectors']:
                point = self.rng.choice(candidates['truck_sectors'])
                order.update(command='attack_sector', target_x=point[0], target_y=point[1])
        return action


def signature(observation):
    located = [report for report in observation['reports'] if report['location']]
    source = located[0]['location'] if located else observation['station']
    return [observation['wind'][0]/3, observation['wind'][1]/3, source[0]/80, source[1]/56,
            float(any(cell['burning'] for cell in observation['sensor_memory']))]


class RetrievalBaseline(DeterministicBaseline):
    name = 'retrieval'

    def __init__(self, bank):
        self.cases = json.loads(Path(bank).read_text())
        if not isinstance(self.cases, list) or any(case.get('split') != 'train' for case in self.cases):
            raise ValueError('Retrieval bank must contain only training cases')
        self.retrieved_ids = []

    def choose_reports(self, observation):
        current = signature(observation)
        matches = sorted(self.cases, key=lambda case: sum(abs(a-b) for a, b in zip(current, case['signature'])))
        matches = [case for case in matches if sum(abs(a-b) for a, b in zip(current, case['signature']))/5 <= .25][:3]
        self.retrieved_ids = [case['scenario_id'] for case in matches]
        reports = super().choose_reports(observation)
        if matches and reports:
            preferred = matches[0].get('investigated_location')
            if preferred:
                reports.sort(key=lambda report: math.dist(report['location'], preferred))
        return reports
