"""Public-only operational-day reference, never an optimal firefighting policy."""


class DayDispatcher:
    def __init__(self, policy='dispatcher'):
        if policy not in ('passive', 'dispatcher'):
            raise ValueError('Unknown day policy')
        self.policy = policy
        self.asked = set()

    def action(self, environment, observation):
        if self.policy == 'passive':
            return {'advance_minutes': 60}
        offers = [offer['id'] for offer in observation['offers']
                  if offer['status'] == 'offered' and offer['expires_at'] > observation['minute']]
        if offers:
            return {'authorizations': offers[:6]}
        active = [cell for cell in observation['sensor_memory']
                  if cell['burning'] and observation['minute'] - cell['observed_at'] <= 120]
        for group in observation['districts']:
            closed = any(destination['id'] == group.get('destination_id') and destination['status'] != 'open'
                         for destination in observation['destinations'])
            smoke_reported = any(report['source'] == group['id'] and report['kind'] == 'resident_update'
                                 and report['actionable'] for report in observation['reports'])
            if closed or group.get('exposed') or smoke_reported:
                for destination in observation['destinations']:
                    key = ('protection', group['id'], destination['id'], observation['minute'] // 60)
                    if destination['status'] == 'open' and key not in self.asked:
                        self.asked.add(key)
                        result = environment.evacuation_options(group['id'], destination['id'])
                        if result['options']:
                            return {'protection_orders': [dict(group_id=group['id'], action='evacuate',
                                destination_id=destination['id'], approach_id=result['options'][0]['id'])]}
        pending = set(observation['pending_resource_requests'])
        requested = []
        for unit in observation['resources']:
            if not active:
                break
            if unit['state'] != 'available' or unit['id'] in pending:
                continue
            if unit['kind'] == 'aircraft' and not observation['aircraft_operable']:
                continue
            if any(offer['resource_id'] == unit['id'] and offer['status'] in ('offered', 'authorized')
                   and offer['expires_at'] > observation['minute'] for offer in observation['offers']):
                continue
            result = environment.route_options(unit['id'])
            if result['options']:
                requested.append(dict(resource_id=unit['id'], sector_id='incident',
                                      approach_id=result['options'][0]['id']))
        if requested:
            return {'requests': requested[:6]}
        for center in observation['centers']:
            key = (center, observation['minute'] // 180)
            if key not in self.asked:
                self.asked.add(key)
                return {'status_requests': [dict(center_id=center, subject='resources')]}
        return {'advance_minutes': 60}
