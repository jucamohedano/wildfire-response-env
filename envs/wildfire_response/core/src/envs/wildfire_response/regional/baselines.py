"""Public-observation policies for CPU-only regional acceptance runs."""
import math
import random


def passive(observation):
    return {}


def blanket_warning(observation):
    return dict(messages=[dict(recipient=district['id'], purpose='evacuate')
                          for district in observation['districts'] if district['status'] == 'unwarned'])


class RandomValidBaseline:
    def __init__(self, seed):
        self.random = random.Random(seed)

    def __call__(self, observation):
        action = dict(requests=[], authorizations=[], releases=[], messages=[])
        for offer in observation['offers']:
            if offer['status'] == 'offered' and self.random.random() < .5:
                action['authorizations'].append(offer['id'])
        for unit in observation['resources']:
            if unit['state'] == 'available' and self.random.random() < .4:
                sector = self.random.choice(observation['sectors'])
                action['requests'].append(dict(resource_id=unit['id'], sector_id=sector['id'],
                                               mission=self.random.choice(['recon', 'suppress'])))
            elif unit['state'] == 'on_scene' and self.random.random() < .3:
                action['releases'].append(unit['id'])
        if self.random.random() < .3:
            district = self.random.choice(observation['districts'])
            action['messages'].append(dict(recipient=district['id'], purpose=self.random.choice(['inform','evacuate'])))
        return action


class CooperativeBaseline:
    def __call__(self, observation):
        action = dict(requests=[], authorizations=[], releases=[], messages=[])
        action['authorizations'] = [offer['id'] for offer in observation['offers'] if offer['status'] == 'offered']
        sectors = {sector['id']: sector for sector in observation['sectors']}
        reports = [sector for sector in observation['sectors'] if sector['id'].startswith('report:')]
        fire = [cell for cell in observation['sensor_memory'] if cell['burning'] and cell['observed_at'] == observation['minute']]
        memory={(cell['x'],cell['y']):cell for cell in observation['sensor_memory']}
        for unit in observation['resources']:
            if unit['state'] == 'on_scene':
                target = sectors[unit['sector_id']]['center']
                if not any(math.dist(target, [cell['x'], cell['y']]) <= 3 for cell in fire):
                    action['releases'].append(unit['id'])
            elif unit['state'] == 'available' and (reports or fire):
                unverified = [report for report in observation['reports'] if report.get('location') is not None
                              and (tuple(report['location']) not in memory
                                   or memory[tuple(report['location'])]['observed_at'] < report['observed_at'])]
                active = [sector for sector in observation['sectors'] if any(
                    math.dist(sector['center'],[cell['x'],cell['y']]) <= 3 for cell in fire)]
                combined = active + [sectors['report:'+report['id']] for report in unverified]
                by_location={tuple(sector['center']):sector for sector in combined}
                candidates=sorted(by_location.values(),key=lambda sector:(
                    sector['center'][0]*observation['wind'][0]+sector['center'][1]*observation['wind'][1],sector['id']))
                if candidates:
                    sector = candidates[0 if unit['home_base'] == 'valverde' else -1]
                    action['requests'].append(dict(resource_id=unit['id'], sector_id=sector['id'], mission='suppress'))
        notices = {(ack['source'], ack.get('purpose')) for ack in observation['acknowledgements']
                   if ack['kind'] == 'message_delivered'}
        for district in observation['districts']:
            if district['status'] != 'unwarned' or (district['id'], 'evacuate') in notices:
                continue
            if any(report.get('location') is not None and math.dist(report['location'], district['home']) <= 6
                   for report in observation['reports']):
                action['messages'].append(dict(recipient=district['id'], purpose='evacuate'))
        return action
