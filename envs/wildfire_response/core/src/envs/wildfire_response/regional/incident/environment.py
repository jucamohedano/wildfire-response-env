import copy
import hashlib
from pathlib import Path

from ..grounded.environment import replay_development_trajectory
from ..ops.generator import digest
from ..protection.world import ProtectionWorld
from ..topographic.environment import (
    CASE_IDS as TOPOGRAPHIC_CASES, TopographicEnv, TopographicScenarioEnv,
    get_task as topographic_task, implementation_digest as topographic_digest,
)
from .events import RULES, VERSION as EVENT_VERSION, IncidentEvents


VERSION = 'wildfire-reactive-control-development-v1'
TASK_VERSION = 'wildfire-reactive-development-tasks-v1'
SCENARIO_VERSION = 'wildfire-reactive-scenario-v1'
CASE_IDS = tuple(identifier.replace('topo-', 'incident-') for identifier in TOPOGRAPHIC_CASES)


def get_task(inputs, identifier):
    if identifier not in CASE_IDS:
        raise ValueError('Unknown reactive incident task')
    task = topographic_task(inputs, identifier.replace('incident-', 'topo-'))
    task.update(id=identifier, scenario_id=identifier, task_set='reactive_development', generator_version=TASK_VERSION)
    task['scenario'].update(id=identifier, incident_event_version=EVENT_VERSION, incident_event_rules=copy.deepcopy(RULES))
    return task


def implementation_digest():
    directory = Path(__file__).resolve().parent
    source = directory.parents[3]
    files = sorted([*directory.parent.rglob('*.py'), *(source/'simulator').rglob('*.py')])
    return digest(dict(parent=topographic_digest(), files={str(path.relative_to(source)):
                  hashlib.sha256(path.read_bytes()).hexdigest() for path in files}))


class ReactiveScenarioEnv(TopographicScenarioEnv):
    def reset(self, scenario_id, seed=None, *, scenario=None):
        self.incidents = None
        super().reset(scenario_id, seed, scenario=scenario)
        self.incidents = IncidentEvents(self.seed, self.max_decisions * self.ticks_per_action)
        self.incidents.unit_states = {unit['id']: unit['state'] for unit in self.resources.values()}
        self.incidents.detect(self)
        return self.observe(), self._info()

    def _sense(self):
        super()._sense()
        if self.incidents is not None and isinstance(self.sim, ProtectionWorld):
            self.incidents.detect(self)

    def _deliver(self):
        if self.incidents is not None:
            self.incidents.deliver(self)
        super()._deliver()

    def _sectors(self):
        sectors = super()._sectors()
        if self.incidents is not None:
            for event in self.incidents.delivered:
                sectors['event:' + event['id']] = dict(id='event:' + event['id'], center=event['location'][:])
        return sectors

    def observe(self):
        public = super().observe()
        if self.incidents is not None:
            public.update(self.incidents.public_state())
        public['pending_deliveries'] = [item for item in public['pending_deliveries'] if item['kind'] != 'incident_update']
        for resource in public['resources']:
            arrival = None
            if resource['state'] == 'en_route':
                arrival = resource['due_at']
            elif resource['state'] == 'mobilizing':
                arrival = resource['due_at'] + self._travel(resource, resource['target'])
            resource.update(arrival_at=arrival,
                arrival_in_minutes=0 if resource['state'] == 'on_scene' else
                    (max(0, arrival - self.sim.tick) if arrival is not None else None),
                travel_time_basis='Assumed mobilization plus transit; no road routing or traffic model')
        return public


class ReactiveOpsEnv(TopographicEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def _resolve_task(self, task_id):
        return get_task(self._active_inputs, task_id)

    def _task_manifest(self):
        return dict(version=TASK_VERSION, split='development', source_groups=1,
                    incident_event_version=EVENT_VERSION, rules=copy.deepcopy(RULES),
                    domain_sha256=self._domain['sha256'],
                    cases=[dict(id=identifier, sha256=digest(self._resolve_task(identifier))) for identifier in CASE_IDS])

    def _new_core(self):
        return ReactiveScenarioEnv(self._active_inputs, self._domain, slope_enabled=self._slope_enabled,
                                   max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=CASE_IDS[0], seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(incident_event_version=EVENT_VERSION, incident_event_rules=copy.deepcopy(RULES),
                                    implementation_sha256=implementation_digest())
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['clock'] = dict(minute=public['minute'], horizon_minutes=self._max_decisions * self._ticks_per_action,
            decision_minutes=self._ticks_per_action, unit='simulation_minutes',
            basis='Each tick is one exercise minute; playback speed and inference latency do not change physical timing')
        public['capabilities']['reactive_incident_events'] = True
        public['rules']['incident_events'] = (
            'Delivered resident and crew updates are operational claims, not confirmed fire cells. '
            'Only received event locations become legal reconnaissance targets. Active incidents persist until '
            'a delivered resolution; a resolved call does not establish population safety or extinguishment. '
            'Use protection orders, allocations and sensor evidence; no reward is earned by event counts.')
        public['assumptions']['reactive_events'] = (
            'Seeded text templates, two-minute resident delivery, one-minute radio delivery, '
            'eight-minute follow-up cooldown, at most64updates. On-scene suppression capacity: '
            'three exercise cells per engine with water, one per crew; uncalibrated request heuristic. '
            'Travelling resources do not satisfy an on-scene support deficit.')
        return public

    def _verify_public_trace(self, trajectory):
        return verify_reactive_trajectory(trajectory, self._inputs, self._domain)


def verify_reactive_trajectory(trajectory, prepared, domain):
    enabled = trajectory.get('slope_enabled')
    if type(enabled) is not bool:
        raise ValueError('Trace must pin the slope setting')
    def create(unused_pack, **kwargs):
        return ReactiveOpsEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
            expected_topographic_sha256=domain['sha256'], slope_enabled=enabled,
            allow_synthetic_conditions=True, **kwargs)
    result = replay_development_trajectory(trajectory, prepared['episode_pack'], create)
    censored = trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored']
    result.update(boundary_censored=censored, suitable_for_training=not censored,
                  scope='Exact reactive synthetic exercise replay; no historical dispatch or physical calibration claim')
    return result
