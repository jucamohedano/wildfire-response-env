"""Reproducible synthetic task generation. Private events never enter observations."""
import copy
import random

from simulator.engine import Simulation

DATASET_VERSION = 'wildfire-response-tasks-v1'
FAMILIES = ('smoke', 'false_alarm', 'duplicates', 'conflicting', 'wind_shift', 'multiple_fires')
SPLITS = {'train': (0, 80), 'validation': (80, 100), 'test': (100, 120)}
CHANNELS = ('phone_transcript', 'chat', 'responder', 'satellite')


def generate_tasks():
    sim = Simulation(fleet_counts={'scouts': 1, 'extinguishers': 1, 'trucks': 1})
    tasks = []
    configurations = set()
    district_ids = list(sim.groups)
    for index in range(120):
        split = next(name for name, (start, stop) in SPLITS.items() if start <= index < stop)
        seed = 10000 + index
        rng = random.Random(seed)
        family = FAMILIES[index % len(FAMILIES)]
        district = district_ids[(index // len(FAMILIES)) % len(district_ids)]
        home = sim.groups[district]['home']
        candidates = [(column, row) for row in range(1, sim.height - 1) for column in range(1, sim.width - 1)
                      if 4 <= ((column-home[0])**2 + (row-home[1])**2)**.5 <= 9
                      and sim.cells[row][column]['fuel'] > 0
                      and ((column-sim.base[0])**2 + (row-sim.base[1])**2)**.5 >= 6]
        location = list(rng.choice(candidates))
        while (family, tuple(location)) in configurations:
            location = list(rng.choice(candidates))
        configurations.add((family, tuple(location)))
        dx, dy = home[0]-location[0], home[1]-location[1]
        scale = max(abs(dx), abs(dy), 1)
        wind = [round(3*dx/scale, 2), round(3*dy/scale, 2)]
        events = [] if family == 'false_alarm' else [{'tick': 0, 'kind': 'ignition', 'location': location}]
        if family == 'wind_shift':
            events.append({'tick': 24, 'kind': 'wind', 'wind': [-wind[0], -wind[1]]})
        if family == 'multiple_fires':
            second = list(rng.choice([point for point in candidates if list(point) != location]))
            events.append({'tick': 24, 'kind': 'ignition', 'location': second})
        if family != 'false_alarm' and index % 3 == 0 and sim.cells[home[1]][home[0]]['fuel'] > 0:
            events.append({'tick': 32 + index % 9, 'kind': 'ignition', 'location': home[:]})
        reports, annotations = [], []
        for number in range(100 if family == 'duplicates' else 24):
            relevant = number % 4 == 0
            false_claim = family == 'false_alarm' or (family == 'conflicting' and number % 8 == 4)
            claim_location = location[:] if not false_claim else [40, 10]
            if family == 'false_alarm':
                claim_location = location[:]
            observed_at = 0 if number == 0 else rng.randint(0, 32)
            received_at = 0 if number == 0 else observed_at + rng.randint(0, 12)
            channel = rng.choice(CHANNELS)
            text = (f"Smoke near grid {tuple(claim_location)}; please verify."
                    if relevant else rng.choice(('Is the sports centre open?', 'Repeating a smoke warning I heard.',
                                                  'Traffic is slow; I cannot see flames.')))
            noise_location = [rng.randint(1,78), rng.randint(1,54)] if not relevant and number % 3 == 0 else None
            reports.append(dict(id=f'r{number:03d}', observed_at=observed_at, received_at=received_at,
                                source_id=f'person-{number % 9}', source_role=rng.choice(('responder','resident','coordinator')),
                                channel=channel, location=claim_location if relevant else noise_location, text=text))
            annotations.append(dict(report_id=f'r{number:03d}', event_related=relevant,
                                    true_claim=relevant and not false_claim,
                                    duplicate_of='r000' if relevant and number > 0 and not false_claim else None))
        tasks.append(dict(version=DATASET_VERSION, id=f'{split}-{index:03d}', split=split,
                          group_id=f'configuration-{index:03d}', seed=seed, family=family,
                          wind=wind, spread_factor=2.0, fleet={'scouts': 1, 'extinguishers': 1, 'trucks': 1},
                          events=events, reports=sorted(reports, key=lambda report: (report['received_at'], report['id'])),
                          private_annotations=annotations, minimum_ticks=64))
    return tasks


def get_task(scenario_id=None):
    tasks = generate_tasks()
    if scenario_id is None:
        return copy.deepcopy(tasks[0])
    for task in tasks:
        if task['id'] == scenario_id:
            return copy.deepcopy(task)
    raise ValueError(f'Unknown scenario: {scenario_id}')
