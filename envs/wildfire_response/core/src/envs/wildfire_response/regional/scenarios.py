"""Derived coordination scenarios; none is a reconstructed 2026 incident."""
import copy
import hashlib
import json
import math
from .data import geometry

FAMILIES = ('local_smoke', 'growing_fire', 'false_alarm', 'competing_incidents', 'aircraft_unavailable', 'wind_shift')
DATASET_VERSION = 'regional-task-pack-v0.2'


def get_scenario(identifier='regional-000', pack=None):
    from .data import load_pack
    pack = load_pack() if pack is None else pack
    identifiers = [f'regional-{index:03d}' for index in range(len(FAMILIES))]
    if identifier not in identifiers:
        for task in generate_tasks(pack):
            if task['id'] == identifier:
                return copy.deepcopy(task)
        raise ValueError('Unknown regional scenario')
    index = identifiers.index(identifier)
    grid = geometry(pack)
    distance = 4 if index in (0, 2) else 2
    west = [int(grid['width']*.4)-distance, grid['height']//2]
    east = [int(grid['width']*.7)-distance, grid['height']//2]
    events = [] if index == 2 else [dict(tick=0, kind='ignition', location=west)]
    if index == 3:
        events.append(dict(tick=12, kind='ignition', location=east))
    if index == 5:
        events.append(dict(tick=20, kind='wind', wind=[-1, 1]))
    reports = [dict(id='report-0', observed_at=0, received_at=0, location=west,
                    text='Smoke reported; location and cause require verification.', source='synthetic-resident')]
    if index == 3:
        reports.append(dict(id='report-1', observed_at=12, received_at=14, location=east,
                            text='A separate smoke column is reported.', source='synthetic-responder'))
    if index in (1, 5):
        events.append(dict(tick=6, kind='ignition', location=[west[0], west[1]+2]))
        reports.append(dict(id='report-1', observed_at=6, received_at=8, location=[west[0],west[1]+2],
                            text='Additional smoke beside the first report.', source='synthetic-lookout'))
    return dict(id=identifier, family=FAMILIES[index], seed=20000+index, events=events, reports=reports,
                wind=[1, 0] if index != 1 else [2, 0], aircraft_ready_at=20 if index == 4 else 0,
                minimum_ticks=20, synthetic=True)


def generate_tasks(pack=None):
    from .data import load_pack
    pack = load_pack() if pack is None else pack
    offsets = [(0,0), (-2,0), (-1,-1), (0,2), (1,-2), (2,3)]
    tasks = []
    for variant, (offset_x, offset_y) in enumerate(offsets):
        split = 'train' if variant < 4 else 'validation' if variant == 4 else 'test'
        for family_index, family in enumerate(FAMILIES):
            index = len(tasks)
            task = get_scenario(f'regional-{family_index:03d}', pack)
            task.update(id=f'regional-{split}-{index:03d}', split=split, seed=30000+index,
                        group_id=f'regional-configuration-{index:03d}', version=DATASET_VERSION)
            for event in task['events']:
                if event['kind'] == 'ignition':
                    event['location'] = [event['location'][0]+offset_x, event['location'][1]+offset_y]
            for report in task['reports']:
                report['location'] = [report['location'][0]+offset_x, report['location'][1]+offset_y]
                report['channel'] = 'synthetic_phone' if report['id'] == 'report-0' else 'synthetic_radio'
            home = [int(geometry(pack)['width']*.4), geometry(pack)['height']//2]
            direction = [home[axis]-task['reports'][0]['location'][axis] for axis in (0,1)]
            length = max(1, math.hypot(*direction))
            strength = 2 if family == 'growing_fire' else 1
            task['wind'] = [round(strength*component/length,2) for component in direction]
            if family == 'wind_shift':
                next(event for event in task['events'] if event['kind']=='wind')['wind'] = [-value for value in task['wind']]
            task['private_annotations'] = []
            if variant >= 2:
                first = task['reports'][0]
                task['reports'].append(dict(first, id='duplicate-0', received_at=10, source='synthetic-secondhand', channel='synthetic_chat'))
                task['private_annotations'].append(dict(report_id='duplicate-0', duplicate_of='report-0'))
            if variant % 2:
                task['reports'].append(dict(id='conflicting-0', observed_at=3, received_at=6,
                    location=[int(geometry(pack)['width']*.8), int(geometry(pack)['height']*.3)],
                    text='Another location is claimed; this account is unconfirmed.', source='synthetic-resident', channel='synthetic_chat'))
                task['private_annotations'].append(dict(report_id='conflicting-0', associated_ignition=False))
            task['reports'].append(dict(id='irrelevant-0', observed_at=2, received_at=4, location=None,
                text='Is the sports centre open?', source='synthetic-resident', channel='synthetic_chat'))
            task['reports'].sort(key=lambda report: (report['received_at'], report['id']))
            configuration = dict(family=family, events=task['events'], reports=task['reports'],
                                 wind=task['wind'], aircraft_ready_at=task['aircraft_ready_at'])
            task['configuration_sha256'] = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
            tasks.append(task)
    return tasks
