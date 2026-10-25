"""Opt-in complete-height terrain; boundary contact censors instead of rewarding escape."""
import copy
import hashlib
from pathlib import Path

from ..grounded.environment import replay_development_trajectory
from ..malaga.pack import engine_pack
from ..ops.generator import digest
from ..prepared.data import load_inputs
from ..prepared.environment import PreparedDataEnv, PreparedScenarioEnv, implementation_digest as prepared_digest
from ..prepared.tasks import CASE_IDS as PARENT_CASE_IDS, get_task as parent_task
from ..protection.protocol import ProtectionProtocol
from ..protection.world import ProtectionWorld
from .data import crop_inputs, load_domain
from .fire import BOUNDARY_GUARD_CELLS, COUPLING, KERNEL_VERSION, TopographicFire


VERSION = 'wildfire-topographic-control-development-v1'
TASK_VERSION = 'wildfire-topographic-development-tasks-v1'
SCENARIO_VERSION = 'wildfire-topographic-scenario-v1'
CASE_IDS = tuple(identifier.replace('prepared-', 'topo-') for identifier in PARENT_CASE_IDS)
BOUNDARY_CASE_ID = 'topo-boundary-probe'


def implementation_digest():
    directory = Path(__file__).resolve().parent
    return digest(dict(prepared_implementation=prepared_digest(), files=[dict(name=name,
        sha256=hashlib.sha256((directory / name).read_bytes()).hexdigest())
        for name in ('data.py', 'fire.py', 'environment.py')]))


def get_task(inputs, identifier):
    probe = identifier == BOUNDARY_CASE_ID
    if identifier not in CASE_IDS and not probe:
        raise ValueError('Unknown topographic development task')
    index = 0 if probe else CASE_IDS.index(identifier)
    task = parent_task(inputs, PARENT_CASE_IDS[index])
    task.update(id=identifier, scenario_id=identifier, task_set='topographic_development',
                generator_version=TASK_VERSION)
    task['scenario']['id'] = identifier
    if probe:
        task['scenario']['events'] = [dict(tick=2, kind='ignition', location=[1, inputs['grid']['height'] // 2])]
        task['scenario']['reports'] = []
        task['scenario']['seed'] = 771199
        task['seed'] = 771199
    return task


def task_manifest(inputs, domain):
    return dict(version=TASK_VERSION, split='development', source_groups=1,
        domain_sha256=domain['sha256'], crop=domain['crop'],
        cases=[dict(id=identifier, seed=get_task(inputs, identifier)['seed'],
                    sha256=digest(get_task(inputs, identifier))) for identifier in CASE_IDS],
        boundary_probe=dict(id=BOUNDARY_CASE_ID, role='censoring_diagnostic_not_training_task'),
        limitations='One source group; cropping is a declared domain choice, not independent held-out evidence')


class TopographicScenarioEnv(PreparedScenarioEnv):
    def __init__(self, inputs, domain, *, slope_enabled, **kwargs):
        self.domain = domain
        self.slope_enabled = slope_enabled
        self.boundary_censored = False
        super().__init__(inputs, **kwargs)

    def reset(self, scenario_id, seed=None, *, scenario=None):
        if not isinstance(scenario, dict) or scenario.get('id') != scenario_id:
            raise ValueError('Expected a canonical topographic scenario')
        self.pack = engine_pack(self._episode_pack)
        self.task = copy.deepcopy(scenario)
        self.seed = self.task['seed'] if seed is None else seed
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('Expected a nonnegative seed')
        self._grounded = copy.deepcopy(self.task['grounded'])
        self._clarifications = {}
        self._transport = copy.deepcopy(self.task['transport'])
        self._deliveries, self._attempts = {}, {}
        self.protection = ProtectionProtocol(self.task['protection'])
        self.boundary_censored = False
        self.sim = TopographicFire(self.pack, self.seed, self.inputs, self.domain['elevations_metres'], slope_enabled=self.slope_enabled)
        self.sim.set_wind(x=self.task['wind'][0], y=self.task['wind'][1])
        self.resources = {item['id']: dict(copy.deepcopy(item), state='available',
            water_litres=item.get('capacity_litres', 0), sector_id=None, mission=None, due_at=None)
            for item in self.pack['scenario_inventory']}
        self.events = sorted(copy.deepcopy(self.task['events']), key=lambda item: (item['tick'], item['kind'] != 'wind'))
        self.report_events = sorted(copy.deepcopy(self.task['reports']), key=lambda item: (item['received_at'], item['id']))
        self.ledger, self.offers, self.memory = {}, {}, {}
        self.observed_sectors = {}
        self.queue, self.acks, self.pending_requests = [], [], set()
        self.notice_keys = set()
        self.counters = dict(committed_unit_minutes=0, unnecessary_evacuation=0, redundant_notices=0,
                             invalid_actions=0, abandonment=0)
        self.daylight, self.decisions, self.invalid_streak = True, 0, 0
        self.terminated = self.truncated = False
        self.serial = 0
        self.audit_minutes = []
        self.preview_remaining = 3
        self._events(0)
        self._receive()
        self._sense()
        self.previous_cost = self._cost()
        self.sim = ProtectionWorld(self.sim, self.task['protection'])
        return self.observe(), self._info()

    def step(self, action):
        observation, reward, terminated, truncated, info = super().step(action)
        if self.sim.boundary_contact_at is not None:
            self.boundary_censored = True
            self.terminated, self.truncated = False, True
            current = self._cost()
            reward += self.previous_cost - current
            self.previous_cost = current
            return self.observe(), reward, False, True, self._info(info['error'])
        return observation, reward, terminated, truncated, info

    def _events(self, minute):
        super()._events(minute)
        if self.sim.boundary_contact_at is None and any(self.sim.burning(cell)
            for row, cells in enumerate(self.sim.cells) for column, cell in enumerate(cells)
            if row < BOUNDARY_GUARD_CELLS or row >= self.sim.height - BOUNDARY_GUARD_CELLS
            or column < BOUNDARY_GUARD_CELLS or column >= self.sim.width - BOUNDARY_GUARD_CELLS):
            self.sim.boundary_contact_at = minute

    def _info(self, error=None):
        info = super()._info(error)
        info.update(boundary_censored=self.boundary_censored,
                    boundary_contact_at=None if self.sim is None else self.sim.boundary_contact_at,
                    suitable_for_training=not self.boundary_censored)
        return info

    def observe(self):
        public = super().observe()
        public['landscape'].update(slope_enabled=self.slope_enabled,
            elevations_metres=copy.deepcopy(self.domain['elevations_metres']),
            height_support='Complete for every simulation cell and adjacent edge; no unknown-to-flat fallback',
            source_domain_crop=copy.deepcopy(self.domain['crop']),
            boundary_policy='Censor episode on fire contact with the outer two-cell guard; outside is unmodelled, not a firebreak')
        public['topographic_status'] = dict(slope_enabled=self.slope_enabled, kernel_version=KERNEL_VERSION,
            boundary_censored=self.boundary_censored, boundary_contact_at=self.sim.boundary_contact_at)
        return public


class TopographicEnv(PreparedDataEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def __init__(self, prepared_inputs, topographic_inputs, *, expected_prepared_sha256,
                 expected_topographic_sha256, slope_enabled, allow_synthetic_conditions=False,
                 max_decisions=60, ticks_per_action=2):
        if type(slope_enabled) is not bool:
            raise ValueError('Choose slope enabled/disabled explicitly')
        prepared = load_inputs(prepared_inputs, expected_sha256=expected_prepared_sha256)
        self._domain = load_domain(topographic_inputs, prepared, expected_topographic_sha256)
        self._active_inputs = crop_inputs(prepared, self._domain)
        self._slope_enabled = slope_enabled
        super().__init__(prepared, expected_sha256=expected_prepared_sha256,
                         allow_synthetic_conditions=allow_synthetic_conditions,
                         max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _resolve_task(self, task_id):
        return get_task(self._active_inputs, task_id)

    def _task_manifest(self):
        return task_manifest(self._active_inputs, self._domain)

    def _new_core(self):
        return TopographicScenarioEnv(self._active_inputs, self._domain, slope_enabled=self._slope_enabled,
                                     max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=CASE_IDS[0], seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(implementation_sha256=implementation_digest(),
            topographic_inputs_sha256=self._domain['sha256'], topographic_kernel_version=KERNEL_VERSION,
            slope_coupling=copy.deepcopy(COUPLING), slope_enabled=self._slope_enabled,
            boundary_guard_cells=BOUNDARY_GUARD_CELLS,
            malaga_mode='complete_domain_experimental_topography')
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['exercise_mode'] = 'complete_domain_experimental_topography'
        public['capabilities'].update(slope_effects=self._slope_enabled, height_context=True,
                                      measured_terrain_in_physics=self._slope_enabled,
                                      incident_specific_roads=False)
        public['rules']['georeferencing'] = 'Every cell uses the declared cropped EPSG:25830 affine grid and one shared height surface.'
        public['rules']['objective'] += ' Boundary-censored episodes earn no objective credit and are excluded from learning labels.'
        public['assumptions']['slope'] = (
            'Experimental eligible ignition probability multiplier exp(3*clip(edge_grade,-0.5,0.5)). '
            'Grade is adjacent-cell height difference divided by horizontal centre distance. '
            'All edge heights are supported. No slope correction to people or vehicle travel. '
            'Parameters are synthetic, not calibrated wildfire prediction.')
        public['assumptions']['source_grounding'] = (
            'Bounded fully supported MDT05-derived height domain, documented metre interpretation, '
            'retrospective fuel baseline and measured coastal weather context. Fleet, population, '
            'wind physics, communications and fire/protection effects remain synthetic.')
        return public

    def _objective_step(self, observation, terminal):
        if self._core.boundary_censored:
            return [], 0.0
        return super()._objective_step(observation, terminal)

    def cell_lon_lat(self, column, row):
        grid = self._active_inputs['grid']
        if type(column) is not int or type(row) is not int or not (0 <= column < grid['width'] and 0 <= row < grid['height']):
            raise ValueError('Cell outside complete terrain domain')
        return grid['centre_lon_lat'][row][column][:]

    def _verify_public_trace(self, trajectory):
        return verify_topographic_trajectory(trajectory, self._inputs, self._domain)

    def verify_episode(self):
        result = super().verify_episode()
        result.update(boundary_censored=self._core.boundary_censored,
                      suitable_for_training=not self._core.boundary_censored)
        return result


def verify_topographic_trajectory(trajectory, prepared, domain):
    enabled = trajectory.get('slope_enabled')
    if type(enabled) is not bool:
        raise ValueError('Trace must pin the experimental slope setting')
    def create(unused_pack, **kwargs):
        return TopographicEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
            expected_topographic_sha256=domain['sha256'], slope_enabled=enabled,
            allow_synthetic_conditions=True, **kwargs)
    result = replay_development_trajectory(trajectory, prepared['episode_pack'], create)
    censored = trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored']
    result.update(boundary_censored=censored, suitable_for_training=not censored,
                  scope='Exact bounded topographic exercise replay; synthetic coupling, not historical or operational validity')
    return result
