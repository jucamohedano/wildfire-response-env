"""Six synthetic development checks on one source-informed landscape, not a split."""
import copy
import math

from ..malaga.tasks import get_task as original_task
from ..ops.generator import digest


VERSION = 'wildfire-prepared-development-tasks-v1'
CASE_IDS = tuple(f'prepared-dev-{index:03}' for index in range(6))
FAMILIES = ('urgent_fire', 'false_alarm', 'duplicate_burst', 'conflicting_delayed_reports',
            'synthetic_wind_shift', 'failed_protection_delivery')


def layout(inputs):
    grid = inputs['grid']
    available = [[column, row] for row in range(2, grid['height'] - 2)
                 for column in range(3, grid['width'] - 3) if inputs['study_mask'][row][column]]
    if not available:
        raise ValueError('No interior exercise locations')
    homes = [min(available, key=lambda point: (math.dist(point, [int(grid['width'] * fraction), grid['height'] // 2]), point))
             for fraction in (.4, .7)]
    if math.dist(*homes) < 6:
        raise ValueError('Exercise population groups need separate interior homes')
    ignitions = [min((point for point in available if inputs['fuel_valid_mask'][point[1]][point[0]]
                     and point not in homes), key=lambda point: (math.dist(point, [home[0] - 2, home[1]]), point))
                 for home in homes]
    if ignitions[0] == ignitions[1]:
        raise ValueError('Exercise needs distinct recognized ignition locations')
    return homes, ignitions


def get_task(inputs, identifier):
    if identifier not in CASE_IDS:
        raise ValueError('Unknown prepared development task')
    variant = CASE_IDS.index(identifier)
    task = original_task(inputs['episode_pack'], 'malaga-dev-v0')
    homes, ignitions = layout(inputs)
    west, east = ignitions
    scenario = task['scenario']
    scenario.update(id=identifier, seed=771100 + variant, minimum_ticks=24,
                    events=[dict(tick=0, kind='ignition', location=west[:])])
    reports = [dict(id='claim-0', observed_at=0, received_at=0, location=west[:],
                    source='exercise-caller-0', channel='synthetic_phone', source_role='resident',
                    text='EXERCISE: A caller reports smoke near this location. Verify the claim.')]
    if variant == 1:
        scenario['events'] = []
        reports[0]['location'] = homes[0][:]
    if variant == 2:
        reports.extend({**copy.deepcopy(reports[0]), 'id': f'claim-{index}', 'received_at': index * 2,
                        'text': 'EXERCISE: This repeats the earlier claim, not independent confirmation.'}
                       for index in (1, 2))
    if variant == 3:
        scenario['events'].append(dict(tick=16, kind='ignition', location=east[:]))
        reports.extend([
            dict(id='claim-1', observed_at=0, received_at=8, location=east[:], source='exercise-caller-1',
                 channel='synthetic_chat', source_role='resident', text='EXERCISE: Another caller reports smoke here; location uncertain.'),
            dict(id='claim-2', observed_at=16, received_at=24, location=None, source='exercise-caller-2',
                 channel='synthetic_phone', source_role='resident', text='EXERCISE: A delayed caller cannot locate the smoke.')])
        scenario['grounded']['clarification_locations'] = {'claim-2': east[:]}
    if variant == 4:
        scenario['events'].append(dict(tick=14, kind='wind', wind=[-1, 1]))
    if variant == 5:
        scenario['protection']['broadcast_failures'] = 1
    scenario['reports'] = reports
    task.update(id=identifier, scenario_id=identifier, seed=scenario['seed'], task_set='prepared_development',
                generator_version=VERSION, group_id='malaga-2021-single-source-group',
                communication={'mission_swap_once': variant == 3})
    return task


def task_manifest(inputs):
    return dict(version=VERSION, split='development', source_groups=1, inputs_sha256=inputs['sha256'],
                cases=[dict(id=identifier, family=FAMILIES[index], seed=771100 + index,
                            sha256=digest(get_task(inputs, identifier))) for index, identifier in enumerate(CASE_IDS)],
                limitations='Synthetic smoke checks; not held-out evaluation or reconstructed dispatches')
