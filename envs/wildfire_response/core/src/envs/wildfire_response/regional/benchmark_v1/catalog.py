import copy
import json
from pathlib import Path

from ..incident.events import RULES, VERSION as EVENT_VERSION
from ..ops.generator import digest
from ..stress import configuration_digest, factors as stress_factors, materialize
from ..topographic.data import crop_inputs


VERSION = 'wildfire-benchmark-catalog-v1'
FAMILIES = ('uncertain_smoke', 'false_alarm', 'conflicting_reports', 'wind_shift',
            'overlapping_fires', 'delayed_reinforcement', 'unavailable_resources', 'failed_protection')
SOURCE_FAMILIES = ('smoke', 'false_alarm', 'conflicting_locations', 'wind_shift',
                   'multiple_fires', 'smoke', 'multiple_fires', 'smoke')
SPLITS = dict(train=80, validation=20, test=20)
_CATALOG_CACHE = {}


def settings_for(family_index, replica, *, development=False):
    settings = stress_factors(replica * 6 + family_index % 6)
    settings['family'] = SOURCE_FAMILIES[family_index]
    settings['offset'] = [replica % 5 - 2, (replica // 5) * 2 - 3]
    settings['report_delay'] = (0, 2, 4)[replica % 3]
    if family_index == 5:
        settings.update(request_delay=4 + replica % 3, ground_hold_minutes=12 + replica % 4 * 4)
    if family_index == 6:
        settings.update(ground_hold_minutes=24 + replica % 4 * 4, hold_second_base=True,
                        aircraft_ready_minute=60)
    if family_index == 7:
        settings.update(broadcast_failures=1, broadcast_extra_delay=4 + replica % 3 * 2)
    if family_index == 2 and replica % 3 == 0:
        settings['evacuation_route_fire'] = True
    if development:
        settings['ignition_minute'] += 1
    return settings


def compile_task(inputs, settings, identifier, seed, variant, split):
    task = materialize(inputs, settings, identifier, seed, variant)
    scenario = task['scenario']
    scenario.update(incident_event_version=EVENT_VERSION, incident_event_rules=copy.deepcopy(RULES))
    if 'paired_location' in settings:
        from ..prepared.tasks import layout
        _, ignitions = layout(inputs)
        location = ignitions[settings['paired_location']]
        scenario.update(events=[dict(tick=0, kind='ignition', location=location[:])], wind=[1., 0])
        scenario['reports'] = [dict(id='claim-0', observed_at=0, received_at=0, location=None,
            source='exercise-unlocated', channel='synthetic_phone', source_role='resident',
            text='EXERCISE. A caller sees smoke but cannot locate it. Ask for clarification or investigate.')]
        scenario['grounded']['clarification_locations'] = {'claim-0': location[:]}
        task['communication'] = {'mission_swap_once': False}
    if settings['family'] == 'conflicting_locations':
        original = scenario['reports'][0]
        scenario['reports'].append(dict(copy.deepcopy(original), id='forward-claim-0',
            received_at=original['received_at'] + 2, channel='synthetic_chat',
            text='EXERCISE. Forwarded earlier report; not independent confirmation.'))
        scenario['reports'].sort(key=lambda entry: (entry['received_at'], entry['id']))
    if settings.get('evacuation_route_fire'):
        from ..prepared.tasks import layout
        homes, _ = layout(inputs)
        point = [homes[0][0], homes[0][1] + 1]
        if not inputs['study_mask'][point[1]][point[0]] or not inputs['fuel_valid_mask'][point[1]][point[0]]:
            raise ValueError('Evacuation-route challenge requires supported fuel')
        scenario['events'] = [dict(tick=0, kind='ignition', location=point)]
        scenario['reports'][0].update(location=point[:], observed_at=0, received_at=0)
        for report in scenario['reports']:
            if report['id'] == 'forward-claim-0':
                report.update(location=point[:], observed_at=0)
        scenario['transport'].update(resource_unavailable_until={}, request_extra_delay_minutes=0)
        scenario['protection'].update(first_broadcast_extra_delay_minutes=0, broadcast_failures=0)
    task.update(task_set='wildfire_benchmark_v1', split=split, generator_version=VERSION)
    return task


def build_catalog(prepared, domain):
    cache_key = (digest(prepared), digest(domain))
    if cache_key in _CATALOG_CACHE:
        return copy.deepcopy(_CATALOG_CACHE[cache_key])
    inputs = crop_inputs(prepared, domain)
    groups = []
    for family_index, family in enumerate(FAMILIES):
        for replica in range(15):
            validation_count = 3 if family_index < 4 else 2
            split = 'train' if replica < 10 else 'validation' if replica < 10 + validation_count else 'test'
            settings = settings_for(family_index, replica)
            canonical = compile_task(inputs, settings, 'configuration', 0, 0, split)
            fingerprint = configuration_digest(canonical)
            group = dict(id='group-' + fingerprint[:16], split=split, family=family,
                         configuration_sha256=fingerprint, factors=settings,
                         cases=[dict(id=f'bench-{split}-{fingerprint[:16]}-v{variant}',
                                     seed=6100000 + family_index * 10000 + replica * 10 + variant,
                                     variant=variant) for variant in range(2)])
            groups.append(group)
    development = []
    for index in range(12):
        settings = settings_for(index % 8, index // 8, development=True)
        if index in (0, 8):
            settings = settings_for(0, 0, development=True)
            settings['paired_location'] = int(index == 8)
        fingerprint = configuration_digest(compile_task(inputs, settings, 'configuration', 0, 0, 'development'))
        development.append(dict(id=f'bench-dev-{fingerprint[:16]}', seed=6900000 + index,
            variant=0, family=FAMILIES[index % 8], configuration_sha256=fingerprint, factors=settings))
    fingerprints = [group['configuration_sha256'] for group in groups] + [case['configuration_sha256'] for case in development]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError('Physical configuration collision in benchmark catalog')
    catalog = dict(version=VERSION, role='private_evaluator_configuration', source_groups=1,
        prepared_inputs_sha256=prepared['sha256'], topographic_inputs_sha256=domain['sha256'],
        split_groups=SPLITS.copy(), variants_per_group=2, groups=groups, development=development,
        limitations='Synthetic held-out configurations on one landscape; not independent historical incidents')
    catalog['sha256'] = digest(catalog)
    if len(_CATALOG_CACHE) >= 4:
        _CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))
    _CATALOG_CACHE[cache_key] = copy.deepcopy(catalog)
    return catalog


def validate_catalog(catalog, prepared, domain):
    if not isinstance(catalog, dict):
        path = Path(catalog)
        if path.stat().st_size > 1_000_000:
            raise ValueError('Catalog exceeds size limit')
        catalog = json.loads(path.read_text())
    canonical = build_catalog(prepared, domain)
    if catalog != canonical:
        raise ValueError('Catalog does not match frozen compiler and pinned inputs')
    return copy.deepcopy(canonical)


def task_ids(catalog, split):
    if split == 'development':
        return [case['id'] for case in catalog['development']]
    if split not in SPLITS:
        raise ValueError('Unknown benchmark split')
    return [case['id'] for group in catalog['groups'] if group['split'] == split for case in group['cases']]


def get_task(catalog, active_inputs, task_id):
    for case in catalog['development']:
        if case['id'] == task_id:
            return compile_task(active_inputs, case['factors'], task_id, case['seed'], case['variant'], 'development')
    for group in catalog['groups']:
        for case in group['cases']:
            if case['id'] == task_id:
                task = compile_task(active_inputs, group['factors'], task_id, case['seed'], case['variant'], group['split'])
                task['group_id'] = group['id']
                return task
    raise ValueError('Unknown benchmark task')


def public_manifest(catalog):
    return dict(version=VERSION, catalog_sha256=catalog['sha256'], source_groups=1,
                split_groups=copy.deepcopy(SPLITS), tasks=[dict(id=identifier, split=split)
                    for split in ('development', *SPLITS) for identifier in task_ids(catalog, split)])
