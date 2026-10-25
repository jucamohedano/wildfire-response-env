"""Run a bounded study-envelope exercise without importing future incident evidence."""
import copy

from ..grounded.environment import replay_development_trajectory
from ..ops.environment import OpsControlCenterEnv
from ..ops.generator import digest
from ..protection.environment import ProtectionDevelopmentEnv, ProtectionBaseline
from ..protection.protocol import ProtectionProtocol
from ..protection.scenario import ProtectionScenarioEnv
from ..protection.world import ProtectionWorld
from ..fire import RegionalFire
from .pack import engine_pack, load_pack, validate_pack
from .tasks import CASE_IDS, VERSION as TASK_VERSION, get_task, task_manifest


VERSION = 'wildfire-malaga-control-development-v1'
SCENARIO_VERSION = 'wildfire-malaga-scenario-v1'


class MalagaScenarioEnv(ProtectionScenarioEnv):
    def __init__(self, pack, **kwargs):
        self._episode_pack = copy.deepcopy(validate_pack(pack))
        super().__init__(**kwargs)

    def reset(self, scenario_id, seed=None, *, scenario=None):
        if (not isinstance(scenario, dict) or scenario.get('id') != scenario_id
                or not {'grounded', 'protection', 'transport'} <= set(scenario)):
            raise ValueError('Expected a canonical Málaga development scenario')
        self.pack = engine_pack(self._episode_pack)
        self.task = copy.deepcopy(scenario)
        self.seed = self.task['seed'] if seed is None else seed
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('Expected a nonnegative seed')
        self._grounded = copy.deepcopy(self.task['grounded'])
        self._clarifications = {}
        self._transport = copy.deepcopy(self.task['transport'])
        self._deliveries, self._attempts = {}, {}
        held = self._transport.get('resource_unavailable_until', {})
        self.task['aircraft_ready_at'] = max(self.task['aircraft_ready_at'],
                                            held.get('aircraft-las-arenas', 0))
        self.protection = ProtectionProtocol(self.task['protection'])
        self.sim = RegionalFire(self.pack, self.seed)
        self.sim.set_wind(x=self.task['wind'][0], y=self.task['wind'][1])
        self.resources = {item['id']: dict(copy.deepcopy(item), state='available',
            water_litres=item.get('capacity_litres', 0), sector_id=None, mission=None, due_at=None)
            for item in self.pack['scenario_inventory']}
        self.resources['aircraft-las-arenas']['state'] = 'unavailable' if self.task['aircraft_ready_at'] else 'available'
        self.events = sorted(copy.deepcopy(self.task['events']),
                             key=lambda item: (item['tick'], item['kind'] != 'wind'))
        self.report_events = sorted(copy.deepcopy(self.task['reports']),
                                    key=lambda item: (item['received_at'], item['id']))
        self.ledger, self.offers, self.memory = {}, {}, {}
        self.observed_sectors = {}
        self.queue, self.acks, self.pending_requests = [], [], set()
        self.notice_keys = set()
        self.counters = dict(committed_unit_minutes=0, unnecessary_evacuation=0,
            redundant_notices=0, invalid_actions=0, abandonment=0)
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


class MalagaDevelopmentEnv(ProtectionDevelopmentEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def __init__(self, episode_pack, *, allow_synthetic_conditions=False,
                 max_decisions=60, ticks_per_action=2):
        if allow_synthetic_conditions is not True:
            raise ValueError('Historical conditions are not ready; explicitly allow_synthetic_conditions=True for exercises')
        if (type(max_decisions) is not int or type(ticks_per_action) is not int
                or max_decisions * ticks_per_action != 120):
            raise ValueError('Málaga initial-response exercises require a 120-model-minute horizon')
        self._anchor_pack = (validate_pack(copy.deepcopy(episode_pack)) if isinstance(episode_pack, dict)
                             else load_pack(episode_pack))
        self._manifest = self._task_manifest()
        OpsControlCenterEnv.__init__(self, max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _resolve_task(self, task_id):
        return get_task(self._anchor_pack, task_id)

    def _task_manifest(self):
        return task_manifest(self._anchor_pack)

    def _new_core(self):
        return MalagaScenarioEnv(self._anchor_pack, max_decisions=self._max_decisions,
                                 ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=CASE_IDS[0], seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory['malaga_mode'] = 'explicit_synthetic_conditions'
        self._ops_trajectory.update(episode_pack_sha256=self._anchor_pack['sha256'],
            source_bundle_sha256=self._anchor_pack['source_bundle_sha256'],
            runtime_pack_sha256=digest(self._core.pack),
            core_pack_role='Inherited shared parameter defaults; runtime_pack_sha256 identifies the active study pack')
        self._core_trajectory['policy'] = 'wildfire_malaga_development'
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['capabilities'].update(study_envelope_georeferencing=True,
            historical_weather_replay=False, measured_terrain_in_physics=False,
            incident_specific_roads=False, historical_fleet_readiness=False,
            historically_available_population=False)
        public['exercise_mode'] = 'explicit_synthetic_conditions'
        public['geography']['basis'] = 'Retrospectively selected study envelope; not active-fire extent or ignition evidence'
        public['assumptions']['source_grounding'] = (
            'Study envelope and initial local clock are source-informed. All ignitions, wind profiles, '
            'settlement populations, bases, fleet readiness, transit decomposition and fire/protection '
            'effects are exercise assumptions. No historical perimeters or later reports are actor inputs.')
        public['assumptions']['inventory'] = (
            'Fictional exercise inventory. Inherited valverde/cabezudos/las-arenas IDs are compatibility '
            'handles, not actual Malaga bases, locations, deployments or readiness.')
        public['rules']['time'] = (
            'One tick is one assumed model minute. This is a bounded initial-response exercise, '
            'not a compressed six-day incident or a calibrated forecast.')
        public['rules']['georeferencing'] = (
            'Grid kilometre labels locate exercise cells approximately; they do not calibrate '
            'fire spread, walking speed, aircraft travel or suppression in physical units.')
        public['rules']['clarification'] = (
            'Clarify received unlocated reports once; delayed replies remain synthetic unverified claims.')
        return public

    def _verify_public_trace(self, trajectory):
        return verify_malaga_trajectory(trajectory, self._anchor_pack)


def verify_malaga_trajectory(trajectory, episode_pack):
    def create(pack, **kwargs):
        return MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True, **kwargs)

    result = replay_development_trajectory(trajectory, episode_pack, create)
    result['scope'] = 'Canonical synthetic-conditions Malaga exercise; not historical fidelity, physical calibration or optimality'
    return result


__all__ = ['MalagaDevelopmentEnv', 'ProtectionBaseline', 'verify_malaga_trajectory']
