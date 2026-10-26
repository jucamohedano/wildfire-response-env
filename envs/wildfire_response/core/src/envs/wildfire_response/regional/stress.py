"""Configuration-held-out synthetic stresses on the unchanged complete-height kernel."""
import copy
import hashlib
import json
import math
from pathlib import Path

from .grounded.environment import replay_development_trajectory
from .ops.generator import digest
from .prepared.data import _unique, load_inputs
from .prepared.tasks import get_task as prepared_task, layout
from .topographic.data import crop_inputs, load_domain
from .topographic.environment import TopographicEnv, TopographicScenarioEnv
from .topographic.environment import implementation_digest as parent_implementation


VERSION = 'wildfire-topographic-stress-v1'
CATALOG_VERSION = 'wildfire-topographic-stress-catalog-v1'
SPLITS = dict(train=80, validation=20, test=20)
VARIANTS = 2
FAMILIES = ('smoke', 'false_alarm', 'duplicate_burst', 'conflicting_locations', 'wind_shift', 'multiple_fires')
TEXTS = ('EXERCISE. I can see smoke near this location. Please check.',
         'EXERCISE. There may be a fire near this location. I am not certain.')


def implementation_digest():
    return digest(dict(parent=parent_implementation(),
                       stress=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def factors(index):
    replica, family = divmod(index, len(FAMILIES))
    profile = replica % 4
    return dict(family=FAMILIES[family], offset=[replica % 5 - 2, (replica // 5) * 2 - 3],
        ignition_minute=(replica % 3) * 2, report_delay=(0, 4, 8, 10)[profile],
        initial_wind=[(1., 1.7, 2.4)[replica % 3], 0],
        shift_minute=14 + (replica % 5) * 2,
        ground_hold_minutes=(0, 12, 24, 40)[profile], hold_second_base=profile == 3,
        request_delay=(0, 2, 4, 1)[profile], broadcast_extra_delay=(0, 2, 4, 2)[profile],
        broadcast_failures=1 if profile in (1, 3) else 0, readback_mismatch=profile != 0,
        aircraft_ready_minute=(0, 24, 48, 60)[profile])


def materialize(inputs, configuration, identifier, seed, variant):
    task = prepared_task(inputs, 'prepared-dev-000')
    scenario = task['scenario']
    homes, _ = layout(inputs)
    grid, settings = inputs['grid'], configuration
    available = [[column, row] for row in range(4, grid['height'] - 4)
                 for column in range(4, grid['width'] - 4)
                 if inputs['fuel_valid_mask'][row][column] and inputs['study_mask'][row][column]
                 and all(math.dist([column, row], home) >= 2 for home in homes)]
    if len(available) < 2:
        raise ValueError('Stress cases need supported interior ignition locations')
    points = []
    for home in homes:
        desired = [home[0] - 4 + settings['offset'][0], home[1] + settings['offset'][1]]
        point = min((point for point in available if point not in points),
                    key=lambda point: (math.dist(point, desired), point))
        points.append(point[:])
    west, east = points
    first = settings['ignition_minute']
    incidents = [] if settings['family'] == 'false_alarm' else [(first, west)]
    if settings['family'] == 'multiple_fires':
        incidents.append((first + 8, east))
    events = [dict(tick=minute, kind='ignition', location=point[:]) for minute, point in incidents]
    if settings['family'] in ('wind_shift', 'multiple_fires'):
        events.append(dict(tick=settings['shift_minute'], kind='wind', wind=[-.5, 1.7]))
    reports = []
    for report_index, (minute, point) in enumerate(incidents or [(first, west)]):
        reports.append(dict(id=f'claim-{report_index}', observed_at=minute,
            received_at=minute + settings['report_delay'], location=point[:], source=f'exercise-source-{report_index}',
            source_role='resident' if report_index == 0 else 'responder',
            channel='synthetic_phone' if report_index == 0 else 'synthetic_radio', text=TEXTS[variant]))
    if settings['family'] == 'duplicate_burst':
        original = reports[0]
        reports.extend(dict(copy.deepcopy(original), id=f'forward-{index}',
            received_at=original['received_at'] + index * 2, channel='synthetic_chat') for index in range(1, 9))
    if settings['family'] == 'conflicting_locations':
        reports.append(dict(id='conflict-0', observed_at=first, received_at=first + settings['report_delay'] + 2,
            location=east[:], source='exercise-chat-source', source_role='resident',
            channel='synthetic_chat', text=TEXTS[variant]))
    reports.append(dict(id='unlocated-0', observed_at=first, received_at=first + settings['report_delay'] + 4,
        location=None, source='exercise-unlocated-source', source_role='resident', channel='synthetic_phone',
        text='EXERCISE. There is smoke, but I cannot describe the location.'))
    held = {}
    if settings['ground_hold_minutes']:
        held.update({'engine-valverde': settings['ground_hold_minutes'],
                     'crew-valverde': settings['ground_hold_minutes']})
        if settings['hold_second_base']:
            held['engine-cabezudos'] = settings['ground_hold_minutes'] + 8
    scenario.update(id=identifier, family='synthetic_topographic_stress', seed=seed,
        events=sorted(events, key=lambda event: (event['tick'], event['kind'] != 'wind')),
        reports=sorted(reports, key=lambda report: (report['received_at'], report['id'])),
        wind=settings['initial_wind'][:], aircraft_ready_at=settings['aircraft_ready_minute'], minimum_ticks=32)
    scenario['transport'].update(resource_unavailable_until=held, request_extra_delay_minutes=settings['request_delay'])
    scenario['protection'].update(first_broadcast_extra_delay_minutes=settings['broadcast_extra_delay'],
                                  broadcast_failures=settings['broadcast_failures'])
    scenario['grounded']['clarification_locations'] = {'unlocated-0': west[:]}
    task.update(id=identifier, scenario_id=identifier, seed=seed, task_set='topographic_stress',
                generator_version=VERSION, communication={'mission_swap_once': settings['readback_mismatch']})
    return task


def configuration_digest(task):
    scenario = copy.deepcopy(task['scenario'])
    for key in ('id', 'seed'):
        scenario.pop(key)
    for report in scenario['reports']:
        report.pop('text')
    return digest(dict(scenario=scenario, communication=task['communication']))


def build_catalog(prepared, domain):
    inputs = crop_inputs(prepared, domain)
    groups = []
    for index in range(sum(SPLITS.values())):
        split = 'train' if index < 80 else 'validation' if index < 100 else 'test'
        settings = factors(index)
        canonical = materialize(inputs, settings, 'configuration', 0, 0)
        fingerprint = configuration_digest(canonical)
        group_id = 'group-' + fingerprint[:16]
        cases = [dict(id=f'stress-{split}-{fingerprint[:16]}-v{variant}',
                      seed=2810000 + index * 100 + variant, variant=variant) for variant in range(VARIANTS)]
        groups.append(dict(id=group_id, split=split, configuration_sha256=fingerprint,
                           factors=settings, cases=cases))
    if len({group['configuration_sha256'] for group in groups}) != len(groups):
        raise ValueError('Configuration variants collide across catalog groups')
    catalog = dict(version=CATALOG_VERSION, role='private_evaluator_scenario_configuration',
        prepared_inputs_sha256=prepared['sha256'], topographic_inputs_sha256=domain['sha256'],
        source_groups=1, split_groups=dict(SPLITS), variants_per_group=VARIANTS, groups=groups,
        split_basis='Synthetic configuration groups on one landscape, not held-out real incidents or regions',
        conditions='Source-clock initial night response; measured weather is context; all operational dynamics synthetic')
    catalog['sha256'] = digest(catalog)
    return catalog


def load_catalog(source, prepared, domain, expected_sha256):
    if isinstance(source, dict):
        catalog = copy.deepcopy(source)
    else:
        path = Path(source)
        if path.is_symlink():
            raise ValueError('Catalog cannot be a symlink')
        with path.open('rb') as stream:
            raw = stream.read(500001)
        if len(raw) > 500000:
            raise ValueError('Catalog exceeds size cap')
        catalog = json.loads(raw, object_pairs_hook=_unique)
    expected = build_catalog(prepared, domain)
    if (catalog != expected or expected_sha256 != expected['sha256']
            or digest({key: value for key, value in catalog.items() if key != 'sha256'}) != expected_sha256):
        raise ValueError('Catalog differs from canonical configuration or caller pin')
    return catalog


def get_task(inputs, catalog, identifier):
    for group in catalog['groups']:
        for case in group['cases']:
            if case['id'] == identifier:
                task = materialize(inputs, group['factors'], identifier, case['seed'], case['variant'])
                if configuration_digest(task) != group['configuration_sha256']:
                    raise ValueError('Materialized configuration differs from frozen group')
                task.update(group_id=group['id'], split=group['split'])
                return task
    raise ValueError('Unknown opaque stress task ID')


class StressScenarioEnv(TopographicScenarioEnv):
    def _events(self, minute):
        if minute == 0 and self.task['aircraft_ready_at'] > 0:
            self.resources['aircraft-las-arenas']['state'] = 'unavailable'
        super()._events(minute)


class StressEnv(TopographicEnv):
    interface_version = VERSION
    task_version = CATALOG_VERSION
    scenario_version = VERSION

    def __init__(self, prepared_inputs, topographic_inputs, catalog, *, expected_prepared_sha256,
                 expected_topographic_sha256, expected_catalog_sha256, slope_enabled,
                 allow_synthetic_conditions=False, max_decisions=60, ticks_per_action=2):
        prepared = load_inputs(prepared_inputs, expected_sha256=expected_prepared_sha256)
        domain = load_domain(topographic_inputs, prepared, expected_topographic_sha256)
        self._catalog = load_catalog(catalog, prepared, domain, expected_catalog_sha256)
        super().__init__(prepared, domain, expected_prepared_sha256=expected_prepared_sha256,
            expected_topographic_sha256=expected_topographic_sha256, slope_enabled=slope_enabled,
            allow_synthetic_conditions=allow_synthetic_conditions,
            max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _task_manifest(self):
        return dict(version=CATALOG_VERSION, catalog_sha256=self._catalog['sha256'],
                    source_groups=1, split_groups=dict(SPLITS))

    def _resolve_task(self, task_id):
        return get_task(self._active_inputs, self._catalog, task_id)

    def _new_core(self):
        return StressScenarioEnv(self._active_inputs, self._domain, slope_enabled=self._slope_enabled,
                                max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=None, seed=None):
        if task_id is None:
            task_id = self._catalog['groups'][0]['cases'][0]['id']
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(implementation_sha256=implementation_digest(),
            stress_catalog_sha256=self._catalog['sha256'], malaga_mode='topographic_configuration_stress')
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['exercise_mode'] = 'topographic_configuration_stress'
        public['assumptions']['stress'] = (
            'Synthetic configuration-held-out catalog on one source landscape; not historical dispatches. '
            'Night-response setting means aircraft readiness does not override the daylight gate.')
        return public

    def _verify_public_trace(self, trajectory):
        return verify_stress_trajectory(trajectory, self._inputs, self._domain, self._catalog)


def verify_stress_trajectory(trajectory, prepared, domain, catalog):
    enabled = trajectory.get('slope_enabled')
    if type(enabled) is not bool:
        raise ValueError('Stress replay requires an explicit slope setting')
    def create(unused_pack, **kwargs):
        return StressEnv(prepared, domain, catalog, expected_prepared_sha256=prepared['sha256'],
            expected_topographic_sha256=domain['sha256'], expected_catalog_sha256=catalog['sha256'],
            slope_enabled=enabled, allow_synthetic_conditions=True, **kwargs)
    result = replay_development_trajectory(trajectory, prepared['episode_pack'], create)
    censored = trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored']
    result.update(boundary_censored=censored, suitable_for_training=not censored,
                  scope='Canonical synthetic stress replay, not incident-level generalisation or optimality')
    return result
