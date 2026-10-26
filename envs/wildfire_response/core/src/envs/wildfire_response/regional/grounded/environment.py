"""Separate development adapter with a caller-supplied trusted Spanish anchor pack."""
import copy
import json
from collections.abc import Mapping

from ..ops.baselines import ControlBaseline
from ..ops.environment import OpsControlCenterEnv
from ..ops.generator import digest
from ..verifier import verify as verify_accounting
from .anchors import load_anchor_pack, validate_anchor_pack
from .scenario import GroundedScenarioEnv, VERSION as SCENARIO_VERSION
from .tasks import VERSION as TASK_VERSION, get_task, task_manifest


VERSION = 'wildfire-spanish-control-development-v1'


class SpanishDevelopmentEnv(OpsControlCenterEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def __init__(self, anchor_pack, *, max_decisions=60, ticks_per_action=2):
        self._anchor_pack = (validate_anchor_pack(copy.deepcopy(anchor_pack))
                             if isinstance(anchor_pack, dict) else load_anchor_pack(anchor_pack))
        self._manifest = self._task_manifest()
        super().__init__(max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _resolve_task(self, task_id):
        return get_task(self._anchor_pack, task_id)

    def _task_manifest(self):
        return task_manifest(self._anchor_pack)

    def _new_core(self):
        return GroundedScenarioEnv(max_decisions=self._max_decisions,
                                   ticks_per_action=self._ticks_per_action)

    def reset(self, task_id='es-dev-000-v0', seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(interface_version=self.interface_version, task_version=self.task_version,
            scenario_engine_version=self.scenario_version,
            anchor_pack_sha256=self._anchor_pack['sha256'], task_manifest_sha256=digest(self._manifest))
        self._core_trajectory.update(scenario_engine_version=self.scenario_version,
                                     policy='wildfire_spanish_development')
        return observation, self._present_info(info)

    def _present_info(self, info):
        info.update(ops_interface_version=self.interface_version, ops_task_version=self.task_version,
                    scenario_engine_version=self.scenario_version)
        return info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public.update(ops_interface_version=self.interface_version, task_version=self.task_version,
                      scenario_engine_version=self.scenario_version)
        public['capabilities'] = dict(location_clarification=True, shelter_in_place=False,
            in_person_warnings=False, incident_specific_roads=False,
            historical_weather_replay=False, live_messaging=False)
        public['assumptions']['source_grounding'] = (
            'Source-informed synthetic exercise in a common arena, not a historical replay. '
            'Wind, fleet readiness, geography, communications and fire effects remain assumptions.')
        public['rules']['clarification'] = (
            'clarify_reports takes received unlocated report IDs once each. A delayed answer '
            'may provide an approximate claim, not sensor evidence. Shelter and route instructions '
            'are not supported in this version.')
        return public

    def _validated_core_action(self, action):
        if not isinstance(action, dict):
            return super()._validated_core_action(action)
        core_action, assessments = super()._validated_core_action(
            {key: value for key, value in action.items() if key != 'clarifications'})
        if 'clarifications' in action:
            core_action['clarifications'] = copy.deepcopy(action['clarifications'])
        if len(json.dumps(action, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        self._core._validate(core_action)
        return core_action, assessments

    def _commit(self, action):
        result = super()._commit(action)
        return (*result[:4], self._present_info(result[4]))

    def clarify_reports(self, report_ids):
        return self._commit({'clarifications': report_ids})

    def step(self, action):
        return self.submit_action(action)

    def verify_episode(self):
        if not hasattr(self, '_ops_trajectory') or not (self._core.terminated or self._core.truncated):
            raise RuntimeError('Finish an episode before verification')
        result = self._verify_public_trace(self.trajectory())
        accounting = verify_accounting(self._core_trajectory, self._core.evaluator_state())
        return dict(passed=True, objective_result=result, objective_score=result['total_score'],
                    core_accounting=accounting, evaluator_evidence=self._evaluation_evidence())

    def _verify_public_trace(self, trajectory):
        return verify_spanish_trajectory(trajectory, self._anchor_pack)


def verify_spanish_trajectory(trajectory, anchor_pack):
    return replay_development_trajectory(trajectory, anchor_pack, SpanishDevelopmentEnv)


def replay_development_trajectory(trajectory, anchor_pack, environment_type):
    if not isinstance(trajectory, Mapping):
        raise ValueError('Expected a public trajectory')
    for field in ('max_decisions', 'ticks_per_action'):
        if type(trajectory.get(field)) is not int:
            raise ValueError('Recorded episode limits must be integers')
    environment = environment_type(anchor_pack, max_decisions=trajectory['max_decisions'],
                                   ticks_per_action=trajectory['ticks_per_action'])
    environment.reset(trajectory.get('task_id'), trajectory.get('seed'))
    expected = environment.trajectory()
    if set(trajectory) != set(expected) or digest({key: value for key, value in trajectory.items()
                                               if key != 'transitions'}) != digest({
            key: value for key, value in expected.items() if key != 'transitions'}):
        raise ValueError('Trajectory does not match the trusted anchor pack and canonical development task')
    transitions = trajectory['transitions']
    if not isinstance(transitions, list) or not 1 <= len(transitions) <= trajectory['max_decisions']:
        raise ValueError('Expected a bounded complete episode')
    for index, recorded in enumerate(transitions):
        if not isinstance(recorded, dict) or not isinstance(recorded.get('action'), dict):
            raise ValueError('Expected a public structured transition')
        try:
            environment.submit_action(recorded['action'])
        except RuntimeError as error:
            raise ValueError('Transition occurs after episode completion') from error
        if digest(recorded) != digest(environment._ops_trajectory['transitions'][-1]):
            raise ValueError(f'Transition {index} does not replay exactly')
    if not (environment._core.terminated or environment._core.truncated):
        raise ValueError('Trajectory must terminate or truncate')
    completed = set(environment._completed_ids)
    return dict(verified=True, core_replayed=True, total_score=environment.reward,
                all_objectives_met=len(completed) == len(environment._objectives),
                by_kind={objective['kind']: objective['id'] in completed
                         for objective in environment._objectives},
                scope='Canonical simulated development episode; not historical fidelity or optimality')


class ClarifyingControlBaseline:
    def __init__(self):
        self._control = ControlBaseline()

    def __call__(self, observation):
        action = self._control(observation)
        requested = {record['report_id'] for record in observation['clarification_requests']}
        unlocated = [report['id'] for report in observation['reports']
                     if report.get('location') is None and report['id'] not in requested]
        if unlocated:
            action['clarifications'] = unlocated[:4]
        return action
