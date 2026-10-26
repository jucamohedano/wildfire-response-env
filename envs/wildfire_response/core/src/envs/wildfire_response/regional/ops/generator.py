"""Versioned factor catalog; variants stay in their incident-configuration group."""
import copy
import hashlib
import json
import math
import random
import re
from functools import lru_cache
from pathlib import Path

from ..data import geometry, load_pack
from ..scenarios import FAMILIES


GENERATOR_VERSION = 'wildfire-control-generator-v1'
VARIANTS = 3
SPLITS = {'train': 80, 'validation': 20, 'test': 20}
_CASE_PATTERN = re.compile(r'^control-(train|validation|test)-(\d{3})-v([012])$')
_OFFSETS = ((0, 0), (-1, 1), (1, -1), (-2, 0), (0, 2))
_TEXTS = ('Smoke is visible near the claimed location. Please verify.',
          'A caller reports a possible fire at this location; details are uncertain.',
          'An unconfirmed smoke observation needs a field check.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def configuration_groups():
    groups = []
    for index in range(120):
        family_index = index % len(FAMILIES)
        variant = index // len(FAMILIES)
        split = 'train' if index < 80 else 'validation' if index < 100 else 'test'
        factors = dict(family=FAMILIES[family_index], pressure=variant % 3,
            report_delay_minutes=(0, 4, 10)[(variant // 3) % 3],
            wind_index=(1.0, 2.0, 3.0)[(variant + family_index) % 3],
            wind_change=bool((variant // 9) % 2) or family_index == 5,
            availability=(variant + family_index) % 4,
            transport_profile=(variant // 2 + family_index) % 3,
            noise=(variant + family_index) % 3, offset=list(_OFFSETS[variant % 5]))
        groups.append(dict(id=f'control-group-{index:03d}', index=index, split=split,
                           factors=factors, configuration_sha256=digest(factors)))
    return groups


def catalog_manifest():
    return dict(version=GENERATOR_VERSION, groups=configuration_groups(), variants_per_group=VARIANTS,
                split_groups=SPLITS, seed_rule='1000000 + group_index * 100 + variant',
                generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def case_ids(split):
    if split not in SPLITS:
        raise ValueError('Choose train, validation or test')
    return [f"control-{split}-{group['index']:03d}-v{variant}"
            for group in configuration_groups() if group['split'] == split
            for variant in range(VARIANTS)]


@lru_cache(maxsize=360)
def _materialize(identifier):
    match = _CASE_PATTERN.fullmatch(identifier)
    if match is None:
        raise ValueError(f'Unknown generated control task: {identifier!r}')
    split, group_text, variant_text = match.groups()
    index, variant = int(group_text), int(variant_text)
    groups = configuration_groups()
    if index >= len(groups) or groups[index]['split'] != split:
        raise ValueError('Generated task ID does not match its configuration split')
    group = groups[index]
    factors = group['factors']
    seed = 1000000 + index * 100 + variant
    generator = random.Random(seed)
    grid = geometry(load_pack())
    west = [int(grid['width'] * .4) - (2 if factors['pressure'] else 4) + factors['offset'][0],
            grid['height'] // 2 + factors['offset'][1]]
    east = [int(grid['width'] * .7) - 2, grid['height'] // 2 + factors['offset'][1]]
    first_tick = (0, 2, 4)[variant]
    events = []
    incidents = []
    if factors['family'] != 'false_alarm':
        incidents.append((first_tick, west))
        if factors['family'] in ('growing_fire', 'competing_incidents') or factors['pressure'] == 2:
            second = east if factors['family'] == 'competing_incidents' else [west[0], west[1] + 2]
            incidents.append((first_tick + (6, 12, 20)[factors['pressure']], second))
        if factors['pressure'] == 2:
            incidents.append((32, [east[0] - 3, east[1] - 2]))
        events = [dict(tick=tick, kind='ignition', location=point[:]) for tick, point in incidents]
    home = [int(grid['width'] * .4), grid['height'] // 2]
    direction = [home[axis] - west[axis] for axis in (0, 1)]
    length = max(1, math.hypot(*direction))
    wind = [round(factors['wind_index'] * component / length, 3) for component in direction]
    if factors['wind_change']:
        events.append(dict(tick=24 + variant * 4, kind='wind', wind=[-wind[1], wind[0]]))
    reports = []
    for report_index, (tick, point) in enumerate(incidents or [(first_tick, west)]):
        location = point[:]
        if factors['noise'] == 2:
            location[0] += generator.choice((-1, 1))
        report = dict(id=f'report-{report_index}', observed_at=tick,
            received_at=tick + factors['report_delay_minutes'], location=location,
            text=generator.choice(_TEXTS), source=f'synthetic-source-{report_index}',
            channel=('synthetic_phone', 'synthetic_satellite', 'synthetic_radio')[report_index % 3],
            source_role=('resident', 'satellite_summary', 'responder')[report_index % 3])
        reports.append(report)
        for duplicate in range(1 + factors['noise'] * 2):
            reports.append(dict(report, id=f'duplicate-{report_index}-{duplicate}',
                received_at=report['received_at'] + 2 + duplicate * 2,
                source=f'synthetic-forward-{report_index}-{duplicate}', channel='synthetic_chat'))
    if factors['noise']:
        reports.append(dict(id='conflicting-0', observed_at=3, received_at=6 + factors['report_delay_minutes'],
            location=[int(grid['width'] * .8), int(grid['height'] * .3)],
            text='A second location is claimed without confirmation.', source='synthetic-chat-user',
            channel='synthetic_chat', source_role='resident'))
    reports.append(dict(id='unlocated-0', observed_at=2, received_at=4, location=None,
        text='A message arrived without a usable location.', source='synthetic-unlocated',
        channel='synthetic_chat', source_role='resident'))
    availability = factors['availability']
    held = ({}, {'engine-valverde': 32}, {'engine-valverde': 48, 'crew-valverde': 48},
            {'engine-valverde': 48, 'engine-cabezudos': 64})[availability]
    profile = factors['transport_profile']
    transport = dict(message_extra_delay_minutes=(0, 2, 6)[profile],
        request_extra_delay_minutes=(0, 2, 4)[profile], fail_first_evacuation=profile == 2,
        resource_unavailable_until=held)
    scenario = dict(id=identifier, family=factors['family'], seed=seed,
        events=events, reports=sorted(reports, key=lambda report: (report['received_at'], report['id'])),
        wind=wind, aircraft_ready_at=36 if factors['family'] == 'aircraft_unavailable' else 0,
        minimum_ticks=20, synthetic=True, transport=transport)
    validate_scenario(scenario)
    return dict(id=identifier, scenario_id=identifier, scenario=scenario, seed=seed,
        split=split, task_set='control', group_id=group['id'], family=factors['family'],
        variant=variant, generator_version=GENERATOR_VERSION,
        configuration_sha256=group['configuration_sha256'],
        challenge_class='stress' if factors['pressure'] == 2 or availability == 3 else 'standard',
        communication={'mission_swap_once': profile != 0},
        objectives=[dict(id=kind, kind=kind, deadline_minute=120) for kind in (
            'evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline')])


def get_generated_task(identifier):
    if not isinstance(identifier, str):
        raise ValueError('Generated task ID must be a string')
    verify_catalog_lock()
    return copy.deepcopy(_materialize(identifier))


def lock_manifest():
    catalog = catalog_manifest()
    cases = [dict(id=identifier, sha256=digest(_materialize(identifier)))
             for split in SPLITS for identifier in case_ids(split)]
    return dict(version=GENERATOR_VERSION, catalog_sha256=digest(catalog),
                cases_sha256=digest(cases), generator_sha256=catalog['generator_sha256'],
                base_pack_sha256=hashlib.sha256(Path(__file__).resolve().parents[2].joinpath(
                    'data', 'huelva_regional_v2.json').read_bytes()).hexdigest(),
                groups=120, variants_per_group=VARIANTS, cases=360,
                split_groups=SPLITS, split_cases={split: count * VARIANTS for split, count in SPLITS.items()},
                protocol='Static validation covers all cases; test policy outcomes require explicit consumption')


@lru_cache(maxsize=1)
def verify_catalog_lock():
    path = Path(__file__).with_name('dataset_lock.json')
    if not path.exists() or json.loads(path.read_text(encoding='utf-8')) != lock_manifest():
        raise ValueError('Generated dataset changed; create a versioned catalog lock before evaluation')
    return digest(json.loads(path.read_text(encoding='utf-8')))


def validate_scenario(scenario):
    grid = geometry(load_pack())
    reports = scenario['reports']
    winds = [scenario['wind']] + [event['wind'] for event in scenario['events'] if event['kind'] == 'wind']
    if any(not isinstance(wind, list) or len(wind) != 2
           or any(type(component) not in (int, float) or not math.isfinite(component)
                  or not -3 <= component <= 3 for component in wind) for wind in winds):
        raise ValueError('Wind components must be finite numbers between -3 and 3')
    if not 1 <= len(reports) <= 100 or len({report['id'] for report in reports}) != len(reports):
        raise ValueError('Scenario needs one to 100 unique reports')
    for event in scenario['events']:
        if type(event['tick']) is not int or not 0 <= event['tick'] <= 100:
            raise ValueError('Incident events must occur within the episode window')
    for report in reports:
        if (type(report['observed_at']) is not int or type(report['received_at']) is not int
                or not 0 <= report['observed_at'] <= report['received_at'] <= 100):
            raise ValueError('Reports must have ordered, bounded observation and receipt times')
    points = [event['location'] for event in scenario['events'] if event['kind'] == 'ignition']
    points += [report['location'] for report in reports if report.get('location') is not None]
    if any(len(point) != 2 or any(type(coordinate) is not int for coordinate in point)
           or not 1 <= point[0] < grid['width'] - 1 or not 1 <= point[1] < grid['height'] - 1
           for point in points):
        raise ValueError('Ignitions and report locations must lie inside validated scrub terrain')
    transport = scenario['transport']
    if any(type(transport[field]) is not int or not 0 <= transport[field] <= 12
           for field in ('message_extra_delay_minutes', 'request_extra_delay_minutes')):
        raise ValueError('Transport delays must be integer minutes from zero to twelve')
    if type(transport['fail_first_evacuation']) is not bool:
        raise ValueError('Failure profile must be a boolean')
    inventory = {unit['id'] for unit in load_pack()['scenario_inventory']}
    if any(identifier not in inventory or type(minute) is not int or not 0 <= minute <= 100
           for identifier, minute in transport['resource_unavailable_until'].items()):
        raise ValueError('Availability assumptions must name existing units and bounded minutes')
    return True
