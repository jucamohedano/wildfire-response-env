"""Independent outcome/accounting checks, not an independent fire prediction model."""
import math

from .data import load_pack
from .environment import WEIGHTS

TRANSITIONS = {
    'available': {'available', 'reserved'}, 'unavailable': {'unavailable', 'available'},
    'reserved': {'reserved', 'mobilizing', 'available'},
    'mobilizing': {'mobilizing', 'en_route', 'returning', 'available'},
    'en_route': {'en_route', 'on_scene', 'returning'},
    'on_scene': {'on_scene', 'returning'}, 'returning': {'returning', 'refilling', 'available'},
    'refilling': {'refilling', 'available'},
}


def verify(trajectory, evaluator):
    pack = load_pack()
    initial_units = trajectory['initial_observation']['resources']
    units = {unit['id']: unit for unit in initial_units}
    if len(units) != len(initial_units):
        raise ValueError('Duplicate resource identity')
    final = evaluator['fire_checkpoint']['state']
    groups = final['groups']
    initial_groups = {group['id']: group for group in trajectory['initial_observation']['districts']}
    if set(groups) != set(initial_groups):
        raise ValueError('Population identity changed')
    for identifier, group in groups.items():
        if (group['count'] != initial_groups[identifier]['population'] or type(group['count']) is not int
                or group['count'] <= 0 or not 0 <= group['burnt'] <= group['count']):
            raise ValueError('Population accounting changed')
    population = sum(group['count'] for group in groups.values())
    grid = trajectory['initial_observation']['geography']
    if final['width'] != grid['width'] or final['height'] != grid['height'] or len(final['cells']) != grid['height']:
        raise ValueError('Grid dimensions changed')
    for row in final['cells']:
        if len(row) != grid['width'] or any(not math.isfinite(cell['burned']) or not 0 <= cell['burned'] <= 1 for cell in row):
            raise ValueError('Invalid burned-terrain accounting')
    frames = evaluator['audit_minutes']
    if [frame['minute'] for frame in frames] != list(range(1, final['tick']+1)):
        raise ValueError('Missing or repeated minute accounting records')
    if len(frames) != len(trajectory['transitions'])*trajectory['ticks_per_action']:
        raise ValueError('Decision and minute accounting disagree')
    committed, unnecessary = 0, 0
    previous_states = {identifier: unit['state'] for identifier, unit in units.items()}
    previous_water = {identifier: unit['water_litres'] for identifier, unit in units.items()}
    communications = evaluator['communications']
    drops = {(item['minute'],item['resource_id']) for item in communications if item['kind'] == 'water_drop'}
    for frame in frames:
        if set(frame['resource_states']) != set(units) or set(frame['resource_water']) != set(units):
            raise ValueError('Resource inventory not conserved')
        if set(frame['population_status']) != set(groups):
            raise ValueError('Population identity not conserved')
        for identifier, state in frame['resource_states'].items():
            if state not in TRANSITIONS.get(previous_states[identifier], set()):
                raise ValueError('Impossible resource lifecycle transition')
            water = frame['resource_water'][identifier]
            capacity = units[identifier].get('capacity_litres',0)
            if type(water) is not int or not 0 <= water <= capacity:
                raise ValueError('Water outside declared capacity')
            if water > previous_water[identifier] and not (previous_states[identifier] == 'refilling' and state == 'available'):
                raise ValueError('Water appeared without completed refill')
            if units[identifier]['kind'] == 'aircraft' and water < previous_water[identifier]:
                if water != 0 or (frame['minute'],identifier) not in drops:
                    raise ValueError('Aircraft water loss without a recorded full-load drop')
            committed += state not in ('available', 'unavailable')
        if sum(event['kind'] == 'ignition' for event in evaluator['scenario']['events']) == 0:
            unnecessary += sum(groups[identifier]['count'] for identifier, status in frame['population_status'].items()
                               if status != 'unwarned')
        previous_states, previous_water = frame['resource_states'], frame['resource_water']
    airborne, dropped = set(), set()
    for item in communications:
        identifier = item.get('resource_id')
        if item['kind'] == 'water_drop' and (identifier not in units or units[identifier]['kind'] != 'aircraft'):
            raise ValueError('Water drop attributed to an unknown or non-aircraft resource')
        if identifier not in units or units[identifier]['kind'] != 'aircraft':
            continue
        if item['kind'] == 'resource_dispatched':
            airborne.add(identifier)
            dropped.discard(identifier)
        elif item['kind'] == 'water_drop':
            if identifier not in airborne or identifier in dropped or not 1 <= item['minute'] <= len(frames):
                raise ValueError('Repeated drop or drop without an airborne sortie')
            if item['litres'] != units[identifier]['capacity_litres']:
                raise ValueError('Drop volume differs from full payload')
            frame = frames[item['minute']-1]
            if not frame['daylight'] or math.hypot(*frame['wind']) > pack['assumptions']['aircraft_grounding_wind_index']:
                raise ValueError('Drop violates the declared synthetic operating gate')
            dropped.add(identifier)
        elif item['kind'] in ('sortie_aborted', 'resource_ready'):
            airborne.discard(identifier)
    seen, redundant = set(), 0
    for item in communications:
        if item['kind'] == 'message_delivered':
            notice = (item['source'],item['purpose'])
            redundant += notice in seen
            seen.add(notice)
    errors = [step['info']['error'] is not None for step in trajectory['transitions']]
    components = dict(exposed_population=sum(group['burnt'] for group in groups.values())/population,
        burned_terrain=sum(cell['burned'] for row in final['cells'] for cell in row)/(final['width']*final['height']),
        unnecessary_evacuation=unnecessary/(population*120), committed_unit_minutes=committed,
        redundant_notices=redundant, invalid_actions=sum(errors),
        abandonment=int(len(errors)>=3 and all(errors[-3:])),
        unresolved_incident=int((trajectory['transitions'][-1]['terminated'] or trajectory['transitions'][-1]['truncated']) and (
            any(cell['heat']>0 and cell['fuel']>0 for row in final['cells'] for cell in row)
            or sum(event['kind']=='ignition' for event in evaluator['events'])>0)))
    for key, expected in components.items():
        actual = evaluator['components'].get(key)
        if actual is None or not math.isfinite(expected) or not math.isfinite(actual) or abs(actual-expected) > 1e-9:
            raise ValueError(f'Outcome component mismatch: {key}')
    initial_cost = WEIGHTS['exposed_population']*sum(group['exposed'] for group in initial_groups.values())/population
    expected_reward = initial_cost-sum(WEIGHTS[key]*value for key,value in components.items())
    if not math.isfinite(evaluator['cost']) or abs(evaluator['cost']-(initial_cost-expected_reward)) > 1e-9:
        raise ValueError('Evaluator cost disagrees with independently reconstructed cost')
    recorded = trajectory['total_reward']
    if not math.isfinite(recorded) or abs(recorded-expected_reward)>1e-9:
        raise ValueError('Reward does not match independently reconstructed outcomes')
    rewards = [step['reward'] for step in trajectory['transitions']]
    if not all(math.isfinite(value) for value in rewards) or abs(sum(rewards)-recorded)>1e-9:
        raise ValueError('Step rewards do not sum to episode reward')
    return dict(passed=True, components=components, reconstructed_reward=expected_reward,
                checked_minutes=len(frames), scope='accounting and lifecycle invariants; not physical realism or optimality')
