"""One incident group, three synthetic initial-response development exercises."""
import copy

from ..data import geometry
from ..ops.generator import digest
from ..protection.tasks import CONFIGURATION
from .pack import clock_minute, engine_pack, validate_pack


VERSION = 'wildfire-malaga-development-tasks-v1'
CASE_IDS = ('malaga-dev-v0', 'malaga-dev-v1', 'malaga-dev-v2')
OBJECTIVES = ('evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline')


def get_task(pack, identifier):
    validate_pack(pack)
    if identifier not in CASE_IDS:
        raise ValueError('Choose an opaque Málaga development case ID')
    variant = CASE_IDS.index(identifier)
    grid = geometry(engine_pack(pack))
    west = [int(grid['width'] * .4) - 4, grid['height'] // 2]
    east = [int(grid['width'] * .7) - 4, grid['height'] // 2]
    events = [dict(tick=0, kind='ignition', location=west[:]),
              dict(tick=0, kind='daylight', value=False)]
    if variant:
        events.append(dict(tick=16, kind='ignition', location=east[:]))
    if variant == 1:
        events.append(dict(tick=14, kind='wind', wind=[-1, 1]))
    reports = [
        dict(id='claim-0', observed_at=0, received_at=0, location=west[:],
             source='exercise-caller-0', channel='synthetic_phone', source_role='resident',
             text='EXERCISE: A caller reports smoke near this location. Verify the claim.'),
        dict(id='claim-1', observed_at=16 if variant else 2,
             received_at=20 if variant else 6, location=None,
             source='exercise-caller-1', channel='synthetic_phone', source_role='resident',
             text='EXERCISE: A caller reports smoke but cannot provide a precise location.'),
        dict(id='claim-2', observed_at=0, received_at=12, location=west[:],
             source='exercise-caller-0', channel='synthetic_chat', source_role='resident',
             text='EXERCISE: This repeats the earlier smoke report; it is not independent confirmation.'),
    ]
    configuration = copy.deepcopy(CONFIGURATION)
    configuration.update(first_broadcast_extra_delay_minutes=6 if variant == 1 else 0,
                         broadcast_failures=1 if variant == 2 else 0)
    scenario = dict(id=identifier, family='malaga_initial_response_development', seed=771000,
        events=events, reports=reports, wind=[2.9, 0] if variant == 2 else [1, 0],
        aircraft_ready_at=0, minimum_ticks=24, synthetic=True,
        transport=dict(message_extra_delay_minutes=0, request_extra_delay_minutes=0,
                       fail_first_evacuation=False, resource_unavailable_until={}),
        grounded=dict(ground_transit_minutes=pack['assumed_ground_transit_minutes'],
                      local_clock_minute=clock_minute(pack), clarification_delay_minutes=2,
                      clarification_locations={'claim-1': [east[0] - 1, east[1]] if variant else west[:]}),
        protection=configuration)
    points = [event['location'] for event in events if event['kind'] == 'ignition']
    points.extend(report['location'] for report in reports if report['location'] is not None)
    if any(not 1 <= point[0] < grid['width'] - 1 or not 1 <= point[1] < grid['height'] - 1
           for point in points):
        raise ValueError('Exercise locations must fit the actual Málaga grid')
    return dict(id=identifier, scenario_id=identifier, scenario=scenario, seed=scenario['seed'],
                split='development', task_set='malaga_initial_response', group_id='incident-group-000',
                generator_version=VERSION, communication={'mission_swap_once': variant == 1},
                objectives=[dict(id=kind, kind=kind, deadline_minute=120) for kind in OBJECTIVES])


def task_manifest(pack):
    return dict(version=VERSION, episode_pack_sha256=pack['sha256'], split='development',
                source_groups=1, cases=[dict(id=identifier, group_id='incident-group-000',
                    seed=get_task(pack, identifier)['seed'], sha256=digest(get_task(pack, identifier)))
                    for identifier in CASE_IDS],
                conditions='Synthetic initial-response window, not six-day historical replay or a held-out split')
