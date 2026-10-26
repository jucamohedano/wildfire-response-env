"""Prepared fuel physics and causal measured weather context without slope effects."""
import copy
from datetime import timedelta
import hashlib
import math
from pathlib import Path

from ..fire import RegionalFire
from ..grounded.environment import replay_development_trajectory
from ..malaga.environment import MalagaDevelopmentEnv
from ..malaga.pack import engine_pack
from ..malaga.environment import MalagaScenarioEnv
from ..ops.generator import digest
from ..protection.protocol import ProtectionProtocol
from ..protection.world import ProtectionWorld
from .data import FUEL_MAPPING, MAPPING_VERSION, UNKNOWN_FUEL, load_inputs, utc_time
from .tasks import CASE_IDS, VERSION as TASK_VERSION, get_task, layout, task_manifest


VERSION = 'wildfire-prepared-control-development-v1'
SCENARIO_VERSION = 'wildfire-prepared-scenario-v1'
WEATHER_DELAY_MINUTES = 5


def implementation_digest():
    directory = Path(__file__).resolve().parent
    files = [directory / name for name in ('data.py', 'tasks.py', 'environment.py')]
    files.extend(directory.parent / name for name in ('fire.py', 'environment.py', 'verifier.py'))
    files.extend(directory.parent / 'protection' / name for name in ('world.py', 'protocol.py', 'environment.py'))
    files.append(directory.parents[3] / 'simulator/engine.py')
    return digest([dict(name=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in files])


class PreparedFire(RegionalFire):
    def __init__(self, pack, seed, inputs):
        super().__init__(pack, seed)
        grid = inputs['grid']
        self.width, self.height = grid['width'], grid['height']
        self.cells = []
        for row in range(self.height):
            cells = []
            for column in range(self.width):
                code = inputs['fuel_classes'][row][column]
                mapped = FUEL_MAPPING.get(code, UNKNOWN_FUEL)
                terrain, fuel = mapped['terrain'], mapped['initial_fuel']
                if not inputs['study_mask'][row][column]:
                    terrain, fuel = 'bare', 0.0
                cells.append(dict(terrain=terrain, fuel=fuel, initial_fuel=fuel, heat=0.0,
                                  age=0, wet=0, burned=0.0))
            self.cells.append(cells)
        homes, _ = layout(inputs)
        for group, home in zip(self.groups.values(), homes):
            desired = [home[0], min(self.height - 3, home[1] + pack['assumptions']['synthetic_refuge_offset_cells'])]
            eligible = [[column, row] for row in range(1, self.height - 1)
                        for column in range(1, self.width - 1) if inputs['study_mask'][row][column]]
            refuge = min(eligible, key=lambda point: (math.dist(point, desired), point))
            group.update(x=float(home[0]), y=float(home[1]), home=home[:], refuge=refuge)


class PreparedScenarioEnv(MalagaScenarioEnv):
    def __init__(self, inputs, **kwargs):
        self.inputs = inputs
        super().__init__(inputs['episode_pack'], **kwargs)

    def reset(self, scenario_id, seed=None, *, scenario=None):
        if not isinstance(scenario, dict) or scenario.get('id') != scenario_id:
            raise ValueError('Expected a canonical prepared scenario')
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
        self.sim = PreparedFire(self.pack, self.seed, self.inputs)
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

    def observe(self):
        public = super().observe()
        grid = self.inputs['grid']
        public['geography'] = {key: copy.deepcopy(grid[key]) for key in
                               ('width', 'height', 'cell_km', 'crs', 'transform', 'northwest_lon_lat')}
        public['terrain_basis'] = 'EFFIS categorical baseline; synthetic class-to-kernel mapping; unknown cells use declared scrub fallback'
        public['landscape'] = dict(fuel_classes=copy.deepcopy(self.inputs['fuel_classes']),
            fuel_valid_mask=copy.deepcopy(self.inputs['fuel_valid_mask']), study_mask=copy.deepcopy(self.inputs['study_mask']),
            fuel_mapping_version=MAPPING_VERSION, slope_enabled=False,
            fuel_mapping={str(code): copy.deepcopy(parameters) for code, parameters in FUEL_MAPPING.items()},
            unknown_fuel_mapping=copy.deepcopy(UNKNOWN_FUEL),
            parameter_basis='Synthetic normalized fuel and existing demo terrain profiles, not calibrated biophysics',
            unknown_fuel_policy='Synthetic scrub fallback, not observed non-burnable terrain',
            source_epoch='Unverified; not a measured September2021 fuel inventory')
        start = utc_time(self.inputs['episode_start_utc'])
        now = start + timedelta(minutes=self.sim.tick)
        records = []
        for record in self.inputs['weather']['records']:
            observed = utc_time(record['observed_at_utc'])
            received = observed + timedelta(minutes=WEATHER_DELAY_MINUTES)
            if received <= now and (now - observed).total_seconds() <= 3600:
                records.append({**copy.deepcopy(record), 'received_at_utc': received.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                'delivery_basis': 'Assumed five-minute exercise delay, not historical publication'})
        public['weather_context'] = dict(station=self.inputs['weather']['station'], records=records,
            now_utc=now.strftime('%Y-%m-%dT%H:%M:%SZ'), physics_coupled=False,
            limitations='Coastal proxy; direction convention/interval semantics unknown. Relative simulator wind remains synthetic.')
        return public


class PreparedDataEnv(MalagaDevelopmentEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def __init__(self, inputs, *, expected_sha256, allow_synthetic_conditions=False,
                 max_decisions=60, ticks_per_action=2):
        self._inputs = load_inputs(inputs, expected_sha256=expected_sha256)
        super().__init__(self._inputs['episode_pack'], allow_synthetic_conditions=allow_synthetic_conditions,
                         max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _resolve_task(self, task_id):
        return get_task(self._inputs, task_id)

    def _task_manifest(self):
        return task_manifest(self._inputs)

    def _new_core(self):
        return PreparedScenarioEnv(self._inputs, max_decisions=self._max_decisions,
                                   ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=CASE_IDS[0], seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(prepared_inputs_sha256=self._inputs['sha256'],
            implementation_sha256=implementation_digest(),
            fuel_mapping_sha256=digest(dict(mapping=FUEL_MAPPING, unknown=UNKNOWN_FUEL)),
            fuel_mapping_version=MAPPING_VERSION, weather_delivery_delay_minutes=WEATHER_DELAY_MINUTES,
            slope_enabled=False, malaga_mode='prepared_data_synthetic_physics')
        self._core_trajectory['policy'] = 'wildfire_prepared_development'
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['exercise_mode'] = 'prepared_data_synthetic_physics'
        public['capabilities'].update(measured_weather_context=True, measured_fuel_classes=True,
            measured_terrain_in_physics=False, historical_weather_replay=False, slope_effects=False)
        public['geography']['basis'] = 'Prepared EPSG:25830 grid, exact cell-centre lookup available; study envelope not fire extent'
        public['assumptions']['source_grounding'] = (
            'Measured coastal station weather is context only, with assumed delivery delay. Fuel classes are a '
            'baseline of unverified epoch mapped to uncalibrated kernel profiles. Ignitions, population, '
            'fleet, relative wind and communications are synthetic; slope remains disabled pending terrain validation.')
        public['rules']['georeferencing'] = 'All positions use this metric grid; centres come from the compiled EPSG transform, not legacy equirectangular coordinates.'
        return public

    def cell_lon_lat(self, column, row):
        grid = self._inputs['grid']
        if type(column) is not int or type(row) is not int or not (0 <= column < grid['width'] and 0 <= row < grid['height']):
            raise ValueError('Cell outside prepared grid')
        return self._inputs['grid']['centre_lon_lat'][row][column][:]

    def _verify_public_trace(self, trajectory):
        return verify_prepared_trajectory(trajectory, self._inputs)


def verify_prepared_trajectory(trajectory, inputs):
    def create(unused_pack, **kwargs):
        return PreparedDataEnv(inputs, expected_sha256=inputs['sha256'], allow_synthetic_conditions=True, **kwargs)
    result = replay_development_trajectory(trajectory, inputs['episode_pack'], create)
    result['scope'] = 'Pinned prepared-data exercise and exact reward replay; not physical calibration, historical reconstruction or optimality'
    return result
