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
        active_order_states = ('issued', 'delivered', 'acknowledged', 'executing', 'blocked')
        orders = observation.get('protection_orders', [])
        for group in observation['districts']:
            group_orders = [order for order in orders if order.get('group_id') == group['id']
                            and order.get('state') in active_order_states
                            and order.get('action') == 'evacuate' and order.get('destination_id') is not None]
            latest_order = max(group_orders, key=lambda order: (order.get('issued_at', -1),
                               order.get('observed_at', -1), order.get('id', '')), default=None)
            target = latest_order['destination_id'] if latest_order else group.get('destination_id')
            closed_destination = next((destination for destination in observation['destinations']
                                       if destination['id'] == target and destination['status'] != 'open'), None)
            closed = closed_destination is not None
            pending_open = (latest_order is not None and latest_order['state'] in ('issued', 'delivered', 'acknowledged')
                            and any(destination['id'] == target and destination['status'] == 'open'
                                    for destination in observation['destinations']))
            if pending_open:
                continue
            reports = [report for report in observation['reports']
                       if report['source'] == group['id'] and report['kind'] == 'resident_update']
            latest = max(reports, key=lambda report: (report['observed_at'], report['received_at']), default=None)
            recent = bool(latest and 0 <= observation['minute'] - latest['observed_at'] <= 60
                          and latest['observed_at'] >= group.get('observed_at', -1))
            current_condition = latest['payload'].get('condition') if recent else None
            smoke_reported = current_condition in ('nearby_smoke', 'exposed')
            settled = group.get('status') in ('arrived', 'sheltering')
            if not closed and (group.get('status') == 'evacuating' or
                               (settled and current_condition != 'exposed')):
                continue
            if closed or (not settled and group.get('exposed')) or smoke_reported:
                previous_attempts = [item[2] for item in self.asked if len(item) == 3
                                     and item[0] == 'protection' and item[1] == group['id']]
                closure_key = (('closure', group['id'], target, closed_destination.get('observed_at', -1))
                               if closed else None)
                cooling = any(0 <= observation['minute'] - attempt < 60 for attempt in previous_attempts)
                new_closure = (closed and closure_key not in self.asked
                               and closed_destination.get('observed_at', -1) >= max(previous_attempts, default=0))
                if cooling and not new_closure:
                    continue
                choices = []
                for destination in observation['destinations']:
                    if destination['status'] == 'open' and destination['id'] != group.get('destination_id'):
                        occupants = {other['id']: other.get('count', 0) for other in observation['districts']
                                     if other['id'] != group['id'] and other.get('destination_id') == destination['id']}
                        for order in orders:
                            if (order.get('destination_id') == destination['id'] and order.get('group_id') != group['id']
                                    and order.get('state') in active_order_states):
                                occupants.update({other['id']: other.get('count', 0) for other in observation['districts']
                                                  if other['id'] == order.get('group_id')})
                        if sum(occupants.values()) + group.get('count', 0) > destination.get('capacity', float('inf')):
                            continue
                        result = environment.evacuation_options(group['id'], destination['id'])
                        for option in result['options']:
                            choices.append((option['travel_minutes'], destination['id'], option['id']))
                self.asked.add(('protection', group['id'], observation['minute']))
                if choices:
                    _, destination_id, approach_id = min(choices)
                    if closure_key is not None:
                        self.asked.add(closure_key)
                    return {'protection_orders': [dict(group_id=group['id'], action='evacuate',
                        destination_id=destination_id, approach_id=approach_id)]}
        pending = set(observation['pending_resource_requests'])
        requested = []
        for unit in observation['resources']:
            if not active:
                break
            if unit['state'] not in ('available', 'needs_assistance', 'blocked') or unit['id'] in pending:
                continue
            if unit.get('water_litres', 0) <= 0 and unit['kind'] in ('engine', 'aircraft'):
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
