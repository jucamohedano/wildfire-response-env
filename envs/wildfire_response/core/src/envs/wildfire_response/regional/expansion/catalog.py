"""Bounded independent-factor sampling with location-layout grouped splits."""
import copy
from collections import Counter
import random

from ..benchmark_v1.catalog import FAMILIES, SOURCE_FAMILIES, build_catalog as legacy_catalog
from ..benchmark_v1.catalog import compile_task as legacy_compile
from ..ops.generator import digest
from ..stress import configuration_digest
from ..topographic.data import crop_inputs


VERSION = 'wildfire-synthetic-expansion-catalog-v1'
DESIGN_SEED = 20261026
SPLIT_COUNTS = dict(train=80, validation=20, test=20)
LEVELS = dict(
    offset_x=tuple(range(-4, 5)), offset_y=tuple(range(-4, 5)),
    ignition_minute=(0, 6, 12, 24), report_delay=(0, 2, 4, 8, 12),
    initial_wind=((.7, 0), (1.7, 0), (0, 1.7), (-1.7, 0), (0, -1.7)),
    shift_minute=(12, 24, 40, 56), ground_hold_minutes=(0, 12, 24, 40),
    hold_second_base=(False, True), request_delay=(0, 2, 4, 6),
    broadcast_extra_delay=(0, 2, 4, 8), broadcast_failures=(0, 1),
    readback_mismatch=(False, True), duplicate_count=(0, 2, 6, 12))
_CACHE = {}


def compile_task(inputs, settings, identifier, seed, variant, split):
    task = legacy_compile(inputs, settings, identifier, seed, variant, split)
    scenario = task['scenario']
    original = scenario['reports'][0]
    scenario['reports'].extend(dict(copy.deepcopy(original), id=f'copy-{index}',
        received_at=original['received_at'] + 2 * (index + 1), channel='synthetic_chat',
        text='EXERCISE. Forwarded earlier report; not independent confirmation.')
        for index in range(settings['duplicate_count']))
    scenario['reports'].sort(key=lambda report: (report['received_at'], report['id']))
    if len(scenario['reports']) > 100:
        raise ValueError('Expansion exceeds the report budget')
    for event in scenario['events']:
        if not 0 <= event['tick'] < 120:
            raise ValueError('Scheduled event lies outside the response window')
        if event['kind'] == 'ignition':
            column, row = event['location']
            if not inputs['study_mask'][row][column] or not inputs['fuel_valid_mask'][row][column]:
                raise ValueError('Ignition needs supported terrain and fuel')
    for report in scenario['reports']:
        if not 0 <= report['observed_at'] <= report['received_at'] < 120:
            raise ValueError('Report timing is inconsistent')
    task.update(task_set='wildfire_synthetic_expansion_v1', generator_version=VERSION)
    return task


def layout_key(task):
    scenario = task['scenario']
    ignitions = sorted({tuple(event['location']) for event in scenario['events']
                        if event['kind'] == 'ignition'})
    claims = sorted({tuple(report['location']) for report in scenario['reports']
                     if report['location'] is not None})
    return digest(dict(ignitions=ignitions, claims=claims))


def split_for_layout(key):
    bucket = int(digest(dict(design=DESIGN_SEED, layout=key))[:16], 16) % 6
    return 'train' if bucket < 4 else 'validation' if bucket == 4 else 'test'


def build_catalog(prepared, domain):
    cache_key = (digest(prepared), digest(domain))
    if cache_key in _CACHE:
        return copy.deepcopy(_CACHE[cache_key])
    inputs = crop_inputs(prepared, domain)
    old = legacy_catalog(prepared, domain)
    seen = {group['configuration_sha256'] for group in old['groups']}
    seen.update(case['configuration_sha256'] for case in old['development'])
    rng = random.Random(DESIGN_SEED)
    groups = []
    for family_index, family in enumerate(FAMILIES):
        counts = Counter()
        for attempt in range(12000):
            chosen = {name: copy.deepcopy(rng.choice(levels)) for name, levels in LEVELS.items()}
            settings = {name: value for name, value in chosen.items() if name not in ('offset_x', 'offset_y')}
            settings.update(family=SOURCE_FAMILIES[family_index],
                offset=[chosen['offset_x'], chosen['offset_y']], aircraft_ready_minute=60)
            settings['initial_wind'] = list(settings['initial_wind'])
            if family == 'delayed_reinforcement':
                settings['request_delay'] = max(2, settings['request_delay'])
                settings['ground_hold_minutes'] = max(12, settings['ground_hold_minutes'])
            if family == 'unavailable_resources':
                settings.update(hold_second_base=True,
                                ground_hold_minutes=max(24, settings['ground_hold_minutes']))
            if family == 'failed_protection':
                settings['broadcast_failures'] = 1
            canonical = compile_task(inputs, settings, 'configuration', 0, 0, 'train')
            fingerprint = configuration_digest(canonical)
            key = layout_key(canonical)
            split = split_for_layout(key)
            if fingerprint in seen or counts[split] >= SPLIT_COUNTS[split]:
                continue
            seen.add(fingerprint)
            index = len(groups)
            groups.append(dict(id='expanded-group-' + fingerprint[:16], family=family, split=split,
                layout_sha256=key, configuration_sha256=fingerprint, factors=settings,
                cases=[dict(id=f'expanded-{split}-{fingerprint[:16]}-v{variant}',
                            seed=82000000 + index * 2 + variant, variant=variant) for variant in range(2)]))
            counts[split] += 1
            if dict(counts) == SPLIT_COUNTS:
                break
        else:
            raise ValueError(f'Cannot fill layout-grouped expansion for {family}; do not weaken splits')
    catalog = dict(version=VERSION, role='private_evaluator_configuration', design_seed=DESIGN_SEED,
        prepared_inputs_sha256=prepared['sha256'], topographic_inputs_sha256=domain['sha256'],
        parent_catalog_sha256=old['sha256'], source_groups=1,
        split_groups={split: count * len(FAMILIES) for split, count in SPLIT_COUNTS.items()},
        variants_per_group=2, groups=groups, development=[],
        limitations='Synthetic configurations on one known landscape, not new historical incidents; '
                     'layout-grouped splits are not geographic generalisation or optimality evidence')
    catalog['sha256'] = digest(catalog)
    if len(_CACHE) >= 2:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[cache_key] = copy.deepcopy(catalog)
    return catalog


def get_task(catalog, inputs, task_id):
    for group in catalog['groups']:
        for case in group['cases']:
            if case['id'] == task_id:
                task = compile_task(inputs, group['factors'], task_id, case['seed'], case['variant'], group['split'])
                if configuration_digest(task) != group['configuration_sha256']:
                    raise ValueError('Expanded task differs from its configuration pin')
                task['group_id'] = group['id']
                return task
    raise ValueError('Unknown expanded task ID')


def public_manifest(catalog):
    return dict(version=VERSION, catalog_sha256=catalog['sha256'], source_groups=1,
        split_groups=copy.deepcopy(catalog['split_groups']),
        tasks=[dict(id=case['id'], split=group['split']) for group in catalog['groups'] for case in group['cases']])


def coverage(catalog):
    result = {}
    for split in SPLIT_COUNTS:
        groups = [group for group in catalog['groups'] if group['split'] == split]
        result[split] = dict(groups=len(groups), tasks=sum(len(group['cases']) for group in groups),
            layouts=len({group['layout_sha256'] for group in groups}),
            families=dict(Counter(group['family'] for group in groups)),
            offsets=sorted({tuple(group['factors']['offset']) for group in groups}),
            levels={field: sorted({str(group['factors'][field]) for group in groups})
                    for field in LEVELS if field not in ('offset_x', 'offset_y')})
    return dict(version=VERSION, splits=result,
        interpretation='Observed marginal levels, not exhaustive pairwise or Cartesian coverage. '
                       'Shift timing applies only to wind-shift/multi-fire families; aircraft remain night-grounded.')
