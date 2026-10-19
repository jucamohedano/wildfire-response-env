"""Named public-observation choices, translated without changing tactical selections."""
import copy

from ..baselines import passive

CHOICE_VERSION = 'wildfire-director-choices-v1'


def build_choices(observation):
    fleet = {}
    holds = passive(observation)['fleet']
    arrays = {'scout': 'scout_orders', 'extinguisher': 'extinguisher_orders', 'truck': 'truck_orders'}
    for vehicle in observation['vehicles']:
        role = vehicle['role']
        field = 'truck_id' if role == 'truck' else 'drone_id'
        identifier = vehicle[field]
        array = arrays[role]
        hold = next(order for order in holds[array] if order[field] == identifier)
        hold['reason'] = 'Director selected hold'
        options = {'hold': hold}
        if role in ('scout', 'truck'):
            options['continue'] = {field: identifier, 'command': 'continue', 'reason': 'Director selected continue'}
        candidates = observation['candidates']
        if role == 'scout':
            for kind, points in [('patrol', candidates['patrol']), ('reconnaissance', candidates['reconnaissance'])]:
                for index, point in enumerate(points):
                    options[f'{kind}_{index}'] = dict(drone_id=identifier, command='patrol',
                        waypoints=[copy.deepcopy(point)], reason=f'Director selected {kind}_{index}')
        elif role == 'extinguisher':
            for index, point in enumerate(candidates['reconnaissance']):
                options[f'reconnaissance_{index}'] = dict(drone_id=identifier, command='scout',
                    target_x=point[0], target_y=point[1], reason=f'Director selected reconnaissance_{index}')
            for index, point in enumerate(candidates['containment'][identifier]):
                options[f'containment_position_{index}'] = dict(drone_id=identifier, command='contain',
                    target_x=point['x'], target_y=point['y'], reason=f'Director selected containment_position_{index}')
        else:
            for index, point in enumerate(candidates['truck_sectors']):
                options[f'attack_sector_{index}'] = dict(truck_id=identifier, command='attack_sector',
                    target_x=point[0], target_y=point[1], reason=f'Director selected attack_sector_{index}')
        if role != 'truck':
            for district in observation['districts']:
                if district['status'] != 'unwarned':
                    continue
                order = dict(drone_id=identifier, command='evacuate_' + district['kind'],
                             district_id=district['id'], reason='Director selected physical warning')
                if role == 'scout':
                    order['waypoints'] = []
                else:
                    order.update(target_x=district['home'][0], target_y=district['home'][1])
                options['warn_' + district['id']] = order
        fleet[identifier] = dict(role=role, order_array=array, options=options)
    messages = {}
    for district in observation['districts']:
        for channel, recipient in [('broadcast', district['id']), ('call', district['contact_id'])]:
            for purpose in ('inform', 'evacuate'):
                messages[f'{channel}:{district["id"]}:{purpose}'] = dict(
                    recipient=recipient, purpose=purpose, channel=channel)
    for identifier in fleet:
        for purpose in ('inform', 'investigate'):
            messages[f'radio:{identifier}:{purpose}'] = dict(recipient=identifier, purpose=purpose, channel='radio')
    return dict(version=CHOICE_VERSION, tick=observation['tick'], fleet=fleet, messages=messages)


def object_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def action_schema(observation):
    choices = build_choices(observation)
    text = dict(type='string')
    reports = dict(type='array', items=text, maxItems=100)
    return object_schema(dict(fleet_choices=object_schema({identifier: dict(type='string', enum=list(unit['options']))
        for identifier, unit in choices['fleet'].items()}),
        report_dispositions=dict(type='array', maxItems=100, items=object_schema(dict(
            report_id=text, status=dict(type='string', enum=['investigate', 'defer', 'dismiss']),
            supporting_report_ids=reports))),
        messages=dict(type='array', maxItems=12, items=object_schema(dict(
            choice_id=dict(type='string', enum=list(choices['messages'])), report_ids=reports))),
        explanation=dict(type='string', maxLength=2000)))


def translate(proposal, choices):
    if (not isinstance(proposal, dict) or set(proposal) !=
            {'fleet_choices', 'report_dispositions', 'messages', 'explanation'}):
        raise ValueError('Expected fleet_choices, report_dispositions, messages and explanation only')
    selected = proposal['fleet_choices']
    if not isinstance(selected, dict) or set(selected) != set(choices['fleet']):
        raise ValueError('Select exactly one named option for each vehicle')
    fleet = dict(scout_orders=[], extinguisher_orders=[], truck_orders=[],
                 mission='Model director assignments', reason='Named public-observation choices')
    for identifier, option in selected.items():
        unit = choices['fleet'][identifier]
        if not isinstance(option, str) or option not in unit['options']:
            raise ValueError(f'Unknown option for {identifier}')
        fleet[unit['order_array']].append(copy.deepcopy(unit['options'][option]))
    if not isinstance(proposal['messages'], list) or len(proposal['messages']) > 12:
        raise ValueError('Supply at most twelve named messages')
    messages = []
    for selection in proposal['messages']:
        if not isinstance(selection, dict) or set(selection) != {'choice_id', 'report_ids'}:
            raise ValueError('Message requires choice_id and report_ids only')
        identifier = selection['choice_id']
        if not isinstance(identifier, str) or identifier not in choices['messages']:
            raise ValueError('Unknown named message')
        message = copy.deepcopy(choices['messages'][identifier])
        message['report_ids'] = copy.deepcopy(selection['report_ids'])
        messages.append(message)
    return dict(fleet=fleet, messages=messages, report_dispositions=copy.deepcopy(proposal['report_dispositions']),
                explanation=proposal['explanation'])
