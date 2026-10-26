"""Development exercises informed by private EGIF anchors, not historical replays."""
import copy
from datetime import datetime
import math
import re

from ..data import geometry, load_pack
from ..ops.generator import digest, validate_scenario
from .anchors import validate_anchor_pack


VERSION = 'wildfire-spanish-development-tasks-v1'
_CASE = re.compile(r'es-dev-(\d{3})-v([012])\Z')
PROFILES = ('steady', 'shift', 'gust')
OBJECTIVES = ('evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline')


def case_ids(pack):
    pack = validate_anchor_pack(copy.deepcopy(pack))
    return [f'es-dev-{index:03d}-v{variant}' for index in range(len(pack['anchors'])) for variant in range(3)]


def get_task(pack, identifier):
    pack = validate_anchor_pack(copy.deepcopy(pack))
    match = _CASE.fullmatch(identifier) if isinstance(identifier, str) else None
    if match is None or int(match[1]) >= len(pack['anchors']):
        raise ValueError('Choose an opaque Spanish development case ID')
    index, variant = map(int, match.groups())
    anchor = pack['anchors'][index]
    grid = geometry(load_pack())
    home = [int(grid['width'] * .4), grid['height'] // 2]
    point = [home[0] - (2 if anchor['stratum'] == 'large' else 4), home[1]]
    detected = datetime.fromisoformat(anchor['detection_local'])
    clock_minute = detected.hour * 60 + detected.minute
    daylight = 7 <= detected.hour < 20
    transit = max(1, min(60, math.ceil(anchor['ground_response_minutes'] / 2)))
    events = [dict(tick=0, kind='ignition', location=point[:]),
              dict(tick=0, kind='daylight', value=daylight)]
    followup_point = [point[0], point[1] + 1]
    if anchor['stratum'] == 'large':
        secondary = [int(grid['width'] * .7) - 4, home[1]]
        events.append(dict(tick=2, kind='ignition', location=secondary))
        followup_point = [secondary[0] - 1, secondary[1]]
    wind = [2.9, 0] if variant == 2 else [1, 0]
    if variant == 1:
        events.append(dict(tick=14, kind='wind', wind=[-1, 1]))
    reports = [
        dict(id='claim-0', observed_at=0, received_at=0, location=point[:],
             source='synthetic-caller-0', channel='synthetic_phone', source_role='resident',
             text='Veo humo cerca de una carretera. La causa no está confirmada.'),
        dict(id='claim-1', observed_at=2, received_at=4, location=None,
             source='synthetic-caller-1', channel='synthetic_phone', source_role='resident',
             text='Veo humo. No sé explicar dónde estoy. ¿Pueden ayudarme a precisar el lugar?'),
        dict(id='claim-2', observed_at=0, received_at=8, location=point[:],
             source='synthetic-caller-0', channel='synthetic_chat', source_role='resident',
             text='Repito mi aviso anterior: hay humo en el mismo lugar.'),
    ]
    scenario = dict(id=identifier, family='spanish_development', seed=730000 + index,
                    events=events, reports=reports, wind=wind, aircraft_ready_at=0,
                    minimum_ticks=20, synthetic=True,
                    transport=dict(message_extra_delay_minutes=2 if variant == 2 else 0,
                                   request_extra_delay_minutes=0, fail_first_evacuation=variant == 2,
                                   resource_unavailable_until={}),
                    grounded=dict(ground_transit_minutes=transit,
                                  local_clock_minute=clock_minute,
                                  clarification_locations={'claim-1': followup_point},
                                  clarification_delay_minutes=2))
    validate_scenario(scenario)
    return dict(id=identifier, scenario_id=identifier, scenario=scenario, seed=scenario['seed'],
                split='development', task_set='spanish_grounded', group_id=anchor['id'],
                generator_version=VERSION, communication={'mission_swap_once': variant == 1},
                objectives=[dict(id=kind, kind=kind, deadline_minute=120) for kind in OBJECTIVES])


def task_manifest(pack):
    return dict(version=VERSION, anchor_pack_sha256=pack['sha256'], split='development',
                cases=[dict(id=identifier, group_id=get_task(pack, identifier)['group_id'],
                            seed=get_task(pack, identifier)['seed'], sha256=digest(get_task(pack, identifier)))
                       for identifier in case_ids(pack)],
                assumptions=dict(
                    geography='Common synthetic Huelva-derived arena; no incident-specific terrain or routes',
                    detection='Relative tick zero is the source detection anchor, not known ignition time',
                    daylight='Source local detection hour with assumed 07:00-20:00 gate; no solar calculation',
                    ground_transit='Half historical detection-to-ground-arrival duration, rounded up and bounded 1-60; assumed decomposition, not measured travel',
                    wind='Three assumed relative-index profiles, not historical m/s or reconstructed forecasts',
                    fleet='Existing five-unit hypothetical inventory; source totals do not establish readiness',
                    communications='Synthetic claim templates, delays, clarifications and faults; not transcripts',
                    outcomes='Historical control/extinction times and burned area never schedule simulator outcomes'))
