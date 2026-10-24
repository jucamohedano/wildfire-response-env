"""Operations-facing tools and objective reward over the regional simulator."""
import copy
import hashlib
import json

from ..data import PACK
from ..environment import (REWARD_VERSION as CORE_REWARD_VERSION,
                           VERSION as CORE_VERSION)
from ..verifier import verify
from .communications import PROTOCOL_FIELDS, PROTOCOL_VERSION, ReadbackProtocol
from .evidence import EVIDENCE_FIELDS, EVIDENCE_VERSION, EvidenceLedger
from .objectives import REWARD_VERSION, objective_step, validate_objectives, verify_ops_trajectory
from .tasks import OPS_TASK_MANIFEST_SHA256, OPS_TASK_VERSION, get_ops_task
from .generator import digest
from .scenario_core import SCENARIO_ENGINE_VERSION, ScenarioResponseEnv, replay_core

OPS_INTERFACE_VERSION = 'wildfire-ops-control-center-v4'
CORE_ACTION_FIELDS = {'requests', 'authorizations', 'releases', 'messages'}
ACTION_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)


class OpsControlCenterEnv:
    """Expose read, check, and committed operations for a control-center agent.

    Reads and order checks do not advance simulated time. Every committed
    operation delegates one combined action to exactly one core ``step``.
    Invalid committed actions retain the core's atomic failure and time-step
    behavior.
    """

    def __init__(self, *, max_decisions=60, ticks_per_action=2):
        self._core = ScenarioResponseEnv(max_decisions=max_decisions,
                                         ticks_per_action=ticks_per_action)
        self._max_decisions = max_decisions
        self._ticks_per_action = ticks_per_action

    def reset(self, task_id='control-000', seed=None):
        task = get_ops_task(task_id)
        if task.get('generator_version') and seed is not None and seed != task['seed']:
            raise ValueError('Generated cases pin their seed; select a different variant ID')
        scenario_id = task['scenario_id']
        objectives = validate_objectives(task['objectives'])
        horizon = self._max_decisions * self._ticks_per_action
        if any(objective['deadline_minute'] > horizon for objective in objectives):
            raise ValueError('Task objective deadlines must fit within the episode horizon')
        if any(objective['deadline_minute'] % self._ticks_per_action for objective in objectives):
            raise ValueError('Task objective deadlines must align with the decision interval')
        candidate = ScenarioResponseEnv(max_decisions=self._max_decisions,
                                        ticks_per_action=self._ticks_per_action)
        observation, info = candidate.reset(scenario_id, seed, scenario=task.get('scenario'))
        if 2 * self._ticks_per_action >= 1 + candidate.pack['assumptions']['offer_ttl_minutes']:
            raise ValueError('Decision interval must leave time to correct a readback before offer expiry')
        sectors = {sector['id'] for sector in observation['sectors']}
        sectors.update('report:' + report['id'] for report in candidate.task['reports'])
        resource_kinds = {resource['kind'] for resource in observation['resources']}
        for objective in objectives:
            if objective['kind'] != 'resource_arrival':
                continue
            if objective['sector_id'] not in sectors:
                raise ValueError(f"Objective {objective['id']} names an unknown public sector")
            if not set(objective['acceptable_resource_kinds']) <= resource_kinds:
                raise ValueError(f"Objective {objective['id']} names an unavailable resource kind")
            if not set(objective['acceptable_missions']) <= {'recon', 'suppress'}:
                raise ValueError(f"Objective {objective['id']} names an unsupported mission")
        self._core = candidate
        self._task = task
        self._objectives = objectives
        self._completed_ids = set()
        self._objective_score = 0.0
        self._evidence = EvidenceLedger()
        self._communications = ReadbackProtocol(**task['communication'])
        self._communications.receive(observation)
        public_observation = self._present_episode(observation)
        self._ops_trajectory = dict(interface_version=OPS_INTERFACE_VERSION,
            task_id=task_id, task_set=task['task_set'], task_version=OPS_TASK_VERSION, reward_version=REWARD_VERSION,
            communication_protocol_version=PROTOCOL_VERSION,
            evidence_version=EVIDENCE_VERSION,
            scenario_engine_version=SCENARIO_ENGINE_VERSION, scenario_sha256=digest(self._core.task),
            core_version=CORE_VERSION, core_reward_version=CORE_REWARD_VERSION,
            core_pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest(),
            task_manifest_sha256=OPS_TASK_MANIFEST_SHA256,
            scenario_id=scenario_id,
            seed=self._core.seed, max_decisions=self._core.max_decisions,
            ticks_per_action=self._core.ticks_per_action, objectives=copy.deepcopy(objectives),
            initial_observation=copy.deepcopy(public_observation), transitions=[])
        self._core_trajectory = dict(version=CORE_VERSION,
            scenario_engine_version=SCENARIO_ENGINE_VERSION, scenario_sha256=digest(self._core.task),
            reward_version=CORE_REWARD_VERSION, scenario_id=scenario_id, seed=self._core.seed,
            policy='wildfire_ops_control_center', max_decisions=self._core.max_decisions,
            ticks_per_action=self._core.ticks_per_action, pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest(),
            initial_observation=copy.deepcopy(observation), initial_info=copy.deepcopy(info),
            total_reward=0.0, transitions=[])
        info = copy.deepcopy(info)
        info['simulator_reward_version'] = info['reward_version']
        info['ops_interface_version'] = OPS_INTERFACE_VERSION
        info['ops_task_id'] = task_id
        info['ops_task_version'] = OPS_TASK_VERSION
        info['communication_protocol_version'] = PROTOCOL_VERSION
        info['evidence_version'] = EVIDENCE_VERSION
        info['reward_version'] = REWARD_VERSION
        info['objective_count'] = len(objectives)
        return public_observation, info

    def get_situation(self):
        return self._present_episode(self._core.observe())

    def get_reports(self, report_ids=None):
        if report_ids is not None and not isinstance(report_ids, list):
            raise ValueError('report_ids must be a list of received report IDs')
        identifiers = None if report_ids is None else copy.deepcopy(report_ids)
        return copy.deepcopy(self._core.reports(identifiers))

    def check_orders(self, action):
        self._require_live()
        if self._core.preview_remaining == 0:
            raise ValueError('Three action checks per decision; advance time before checking again')
        self._core.preview_remaining -= 1
        error = None
        try:
            self._validated_core_action(action)
        except ACTION_ERRORS as exception:
            error = str(exception)[:200]
        return dict(valid=error is None, error=error, remaining_checks=self._core.preview_remaining,
                    scope='Current public action and readback contract only; no forecast')

    def request_resources(self, requests):
        return self._commit(dict(requests=requests))

    def authorize_resources(self, offer_ids):
        return self._commit(dict(authorizations=offer_ids))

    def confirm_readbacks(self, readback_ids):
        return self._commit(dict(readback_confirmations=readback_ids))

    def correct_readbacks(self, corrections):
        return self._commit(dict(readback_corrections=corrections))

    def triage_reports(self, assessments):
        return self._commit(dict(triage=assessments))

    def release_resources(self, resource_ids):
        return self._commit(dict(releases=resource_ids))

    def send_messages(self, messages):
        return self._commit(dict(messages=messages))

    def advance(self):
        return self._commit({})

    def submit_action(self, action):
        if isinstance(action, dict) and 'explanation' in action:
            raise ValueError('Ops actions do not accept free-text explanations')
        return self._commit(action)

    def _commit(self, action):
        self._require_live()
        error = None
        previous_observation = self._core.observe()
        assessments = []
        try:
            core_action, assessments = self._validated_core_action(action)
        except ACTION_ERRORS as exception:
            error = str(exception)[:200]
            core_action = {'_invalid_ops_action': True}
        committed_at = self._core.sim.tick
        observation, core_reward, terminated, truncated, core_info = self._core.step(core_action)
        action_valid = error is None and core_info['error'] is None
        if action_valid:
            self._communications.apply(action, committed_at)
            self._evidence.apply(action, previous_observation, assessments)
        self._communications.receive(observation)
        public_observation = self._present_episode(observation)
        newly_completed, reward = objective_step(public_observation, self._objectives, self._completed_ids,
            terminal=terminated or truncated, evidence=self._evaluation_evidence())
        self._completed_ids.update(newly_completed)
        self._objective_score += reward
        public_observation = self._present_episode(observation)
        self._ops_trajectory['transitions'].append(dict(
            action=copy.deepcopy(action) if action_valid else {'_invalid_ops_action': True},
            action_valid=action_valid, observation=copy.deepcopy(public_observation),
            objective_reward=reward, terminated=terminated, truncated=truncated))
        self._core_trajectory['transitions'].append(dict(
            action=copy.deepcopy(core_action), observation=copy.deepcopy(observation),
            reward=core_reward, terminated=terminated, truncated=truncated,
            info=copy.deepcopy(core_info)))
        self._core_trajectory['total_reward'] += core_reward
        info = copy.deepcopy(core_info)
        if error is not None:
            info['error'] = error
        info.update(simulator_reward_version=info['reward_version'],
                    ops_interface_version=OPS_INTERFACE_VERSION, ops_task_id=self._task['id'],
                    ops_task_version=OPS_TASK_VERSION, reward_version=REWARD_VERSION,
                    communication_protocol_version=PROTOCOL_VERSION,
                    evidence_version=EVIDENCE_VERSION,
                    objective_score=self._objective_score,
                    objectives_completed=list(newly_completed), objective_count=len(self._objectives))
        return public_observation, reward, terminated, truncated, info

    def _validated_core_action(self, action):
        if (not isinstance(action, dict)
                or set(action) - CORE_ACTION_FIELDS - PROTOCOL_FIELDS - EVIDENCE_FIELDS):
            raise ValueError('Unknown ops action fields')
        if len(json.dumps(action, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        core_action = {field: value for field, value in action.items() if field in CORE_ACTION_FIELDS}
        self._core._validate(core_action)
        self._communications.validate(action, list(self._core.offers.values()), self._core.sim.tick)
        assessments = self._evidence.validate(action, self._core.observe())
        return core_action, assessments

    def _evaluation_evidence(self):
        return dict(schedule_complete=not self._core.events and not self._core.report_events
                    and not self._core.queue and self._core.invalid_streak < 3
                    and self._core.sim.tick >= self._core.task['minimum_ticks'],
                    active_fire_cells=sum(self._core.sim.burning(cell)
                                          for row in self._core.sim.cells for cell in row),
                    redundant_notices=self._evidence.redundant_notices,
                    unsupported_evacuations=self._evidence.unsupported_evacuations)

    def _require_live(self):
        if self._core.sim is None or self._core.terminated or self._core.truncated:
            raise RuntimeError('Reset a live episode before stepping or checking orders')

    def _present_episode(self, observation):
        public = self._present_task(observation, self._task['id'], self._objectives)
        communication = self._communications.public_state()
        public['readbacks'] = communication['readbacks']
        public['readback_events'] = communication['readback_events']
        public.update(self._evidence.public_state())
        public['pending_deliveries'].extend(communication['pending_readbacks'])
        public['completed_objective_ids'] = sorted(self._completed_ids)
        public['communication_protocol_version'] = PROTOCOL_VERSION
        public['evidence_version'] = EVIDENCE_VERSION
        public['scenario_engine_version'] = SCENARIO_ENGINE_VERSION
        public['task_set'] = self._task['task_set']
        public['rules']['objective'] = (
            'Complete the public task objectives. Control tasks score evidence triage, '
            'population safety, incident resolution and communication discipline at episode end.')
        public['rules']['triage'] = (
            'Assess received report_ids using fire_observed, no_fire_observed or defer. '
            'Conclusive assessments need sensor evidence at that location, at least as new '
            'as the report observation and at most four model minutes old. Link only reports '
            'with the same location and observation time. No observation is a real-world truth label.')
        public['rules']['notices'] = (
            'Use inform without moving people. Evacuation should be supported by a report '
            'not cleared by fresh sensors, or fresh observed fire, within six grid cells '
            'of the district home. This is a synthetic exercise rule, not response doctrine. '
            'Repeat notices count as redundant unless retrying an acknowledged failed delivery. '
            'Pending messages have not delivered instructions; only delivered evacuation starts movement.')
        public['rules']['dispatch'] = (
            'request -> base offer and readback -> correct or confirm readback -> '
            'ops authorization -> mobilization -> travel -> arrival')
        public['rules']['readback'] = (
            'Compare resource_id, sector_id and mission with the offer. Correct mismatches, '
            'wait one model minute for a new readback, then confirm. Confirmation and '
            'authorization may share one action. Readback alone earns no reward.')
        return public

    def trajectory(self):
        """Return a copy of the public ops trace and objective task contract."""
        if not hasattr(self, '_ops_trajectory'):
            raise RuntimeError('reset required')
        return copy.deepcopy(self._ops_trajectory)

    def verify_episode(self):
        """Recompute objective score and replay the underlying simulator trace."""
        if not hasattr(self, '_ops_trajectory'):
            raise RuntimeError('reset required')
        if not self._core.terminated and not self._core.truncated:
            raise RuntimeError('The episode must terminate or truncate before verification')
        objective_result = verify_ops_trajectory(self._ops_trajectory)
        accounting = verify(self._core_trajectory, self._core.evaluator_state())
        core_reward = replay_core(self._core_trajectory, self._task)
        return dict(passed=True, reward_version=REWARD_VERSION,
                    objective_score=objective_result['total_score'],
                    objective_result=objective_result, core_accounting=accounting,
                    evaluator_evidence=self._evaluation_evidence(),
                    core_replay_reward=core_reward)

    @property
    def reward(self):
        return self._objective_score

    @staticmethod
    def _present_task(observation, task_id, objectives):
        public = OpsControlCenterEnv._present(observation)
        public['task_id'] = task_id
        public['task_version'] = OPS_TASK_VERSION
        public['task_objectives'] = copy.deepcopy(objectives)
        public['ops_interface_version'] = OPS_INTERFACE_VERSION
        return public

    @staticmethod
    def _present(observation):
        public = copy.deepcopy(observation)
        public['authority_role'] = 'ops'
        rules = public.get('rules', {})
        if 'authority' in rules:
            rules['authority'] = rules['authority'].replace(
                'simulated provincial coordinator', 'simulated wildfire ops control center')
        if 'dispatch' in rules:
            rules['dispatch'] = rules['dispatch'].replace('director', 'ops')
        rules['authority_role'] = 'ops'
        for acknowledgement in public.get('acknowledgements', []):
            for field in ('source', 'recipient', 'via'):
                if acknowledgement.get(field) == 'director':
                    acknowledgement[field] = 'ops'
        return public
