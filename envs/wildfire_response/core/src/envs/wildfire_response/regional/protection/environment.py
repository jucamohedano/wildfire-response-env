"""Versioned protection development mode with a hard scalar population-safety gate."""
import json
import math

from ..grounded.environment import SpanishDevelopmentEnv, ClarifyingControlBaseline
from ..grounded.environment import replay_development_trajectory
from ..ops.evidence import local_threat
from ..ops.objectives import objective_step
from .protocol import VERSION as PROTOCOL_VERSION
from .scenario import ProtectionScenarioEnv, VERSION as SCENARIO_VERSION
from .tasks import get_task, task_manifest, VERSION as TASK_VERSION


VERSION = 'wildfire-protection-control-development-v1'
REWARD_VERSION = 'wildfire-protection-safety-gate-v2'
REWARD_UNITS = dict(population_safety=12, evidence_triage=1,
                    incident_resolved=1, communication_discipline=1)
REWARD_WEIGHTS = {kind: units / sum(REWARD_UNITS.values()) for kind, units in REWARD_UNITS.items()}


class ProtectionDevelopmentEnv(SpanishDevelopmentEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def _task_manifest(self):
        return task_manifest(self._anchor_pack)

    def _resolve_task(self, task_id):
        return get_task(self._anchor_pack, task_id)

    def _new_core(self):
        return ProtectionScenarioEnv(max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id='protect-dev-000-v0', seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(reward_version=REWARD_VERSION,
                                    reward_weights=REWARD_WEIGHTS.copy(),
                                    protection_protocol_version=PROTOCOL_VERSION)
        self._core_trajectory['policy'] = 'wildfire_protection_development'
        return observation, info

    def _present_info(self, info):
        super()._present_info(info)
        info.update(reward_version=REWARD_VERSION, reward_weights=REWARD_WEIGHTS.copy(),
                    protection_protocol_version=PROTOCOL_VERSION)
        return info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['capabilities'].update(shelter_in_place=True, in_person_warnings=True,
                                      versioned_protection_orders=True)
        public['protection_protocol_version'] = PROTOCOL_VERSION
        public['reward_weights'] = REWARD_WEIGHTS.copy()
        public['rules']['clarification'] = 'Clarify received unlocated reports once; replies remain unverified claims.'
        public['rules']['evacuate'] = 'Population movement starts after execution, not delivery or acknowledgement.'
        public['rules']['notices'] = (
            'Legacy population messages may inform only. Use versioned protection_orders for '
            'shelter, evacuate and release. Support is judged from current public reports, '
            'sensors or synthetic smoke telemetry, not private historical outcomes.')
        public['rules']['objective'] = (
            'At episode end, population-safety failure yields zero scalar reward. '
            'Otherwise population safety contributes 80%, with the remaining 20% split equally '
            'between evidence triage, incident resolution and communication discipline. '
            'All four are required for full credit. Always report component flags.')
        public['rules']['protection'] = (
            'Use protection_orders with recipient, action shelter/evacuate/release, channel broadcast/in_person, '
            'resource_id or null, and supersedes equal to the latest issued ID or null. '
            'Delivery is not acknowledgement or execution. An in-flight old instruction may act before '
            'a correction arrives; after a newer receipt, older sequences cannot execute. '
            'A crew must be on scene within two cells for in-person warning and is occupied four minutes. '
            'Fresh public home-cell evidence must be clear. Release ends shelter, not an evacuation '
            'already moving. Shelter cannot teleport people home or prevent direct fire exposure.')
        public['assumptions']['protection'] = (
            'Uncalibrated abstract smoke dose: radius two cells, threshold 120, open rate 10/tick, '
            'sheltered rate 2/tick. No medical, building or real route prediction.')
        return public

    def _validated_core_action(self, action):
        if not isinstance(action, dict):
            return super()._validated_core_action(action)
        core_action, assessments = super()._validated_core_action(
            {key: value for key, value in action.items() if key != 'protection_orders'})
        if 'protection_orders' in action:
            core_action['protection_orders'] = action['protection_orders']
        if len(json.dumps(action, allow_nan=False).encode()) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        self._core._validate(core_action)
        return core_action, assessments

    def _objective_step(self, observation, terminal):
        completed, _ = objective_step(observation, self._objectives, self._completed_ids,
                                      terminal=terminal, evidence=self._evaluation_evidence())
        if not terminal:
            return [], 0.0
        if not self._core.sim.population_safe_now():
            completed = [identifier for identifier in completed if identifier != 'population_safety']
        protocol = self._core.protection
        if protocol.redundant_orders or protocol.unsupported_orders:
            completed = [identifier for identifier in completed if identifier != 'communication_discipline']
        completed_kinds = {objective['kind'] for objective in self._objectives
                           if objective['id'] in completed}
        score = (sum(REWARD_UNITS[kind] for kind in completed_kinds) / sum(REWARD_UNITS.values())
                 if 'population_safety' in completed_kinds else 0.0)
        return completed, score

    def issue_protection_orders(self, orders):
        return self._commit({'protection_orders': orders})

    def _verify_public_trace(self, trajectory):
        return verify_protection_trajectory(trajectory, self._anchor_pack)

    def verify_episode(self):
        result = super().verify_episode()
        result.update(reward_version=REWARD_VERSION,
                      reward_weights=REWARD_WEIGHTS.copy(),
                      protection_checkpoint=self._core.sim.protection_checkpoint())
        return result


def verify_protection_trajectory(trajectory, anchor_pack):
    return replay_development_trajectory(trajectory, anchor_pack, ProtectionDevelopmentEnv)


class ProtectionBaseline:
    def __init__(self, *, prefer_shelter=True):
        self._control = ClarifyingControlBaseline()
        self._prefer_shelter = prefer_shelter

    def __call__(self, observation):
        action = self._control(observation)
        action['messages'] = [message for message in action.get('messages', []) if message['purpose'] != 'evacuate']
        busy = {identifier for identifier, until in observation['warning_team_busy_until'].items()
                if until > observation['minute']}
        action['releases'] = [identifier for identifier in action.get('releases', []) if identifier not in busy]
        records = {record['id']: record for record in observation['protection_orders']}
        orders = []
        for district in observation['districts']:
            latest = records.get(observation['latest_protection_orders'].get(district['id']))
            threatened = local_threat(observation, district) or district.get('smoke_present', False)
            desired = None
            if threatened and not district['exposed'] and district['status'] != 'safe':
                at_home = math.dist(district['position'], district['home']) < .01
                desired = ('shelter' if self._prefer_shelter and at_home
                           and district['status'] not in ('evacuating', 'blocked')
                           and district.get('smoke_dose_units', 0) < 84 else 'evacuate')
                if latest and latest['status'] == 'unable_to_comply':
                    desired = 'evacuate'
            elif not threatened and latest and latest['action'] == 'shelter':
                desired = 'release'
            if desired is None or (latest and latest['action'] == desired
                                   and latest['status'] not in ('failed', 'unable_to_comply')):
                continue
            channel, resource_id = 'broadcast', None
            if latest and latest['status'] == 'failed':
                candidates = [unit for unit in observation['resources'] if unit['kind'] == 'crew'
                              and unit['state'] == 'on_scene'
                              and math.dist(unit['target'], district['home']) <= 2
                              and observation['warning_team_busy_until'].get(unit['id'], -1) <= observation['minute']
                              and unit['id'] not in {order['resource_id'] for order in orders}]
                if candidates:
                    channel, resource_id = 'in_person', candidates[0]['id']
                    action['releases'] = [identifier for identifier in action.get('releases', []) if identifier != resource_id]
            orders.append(dict(recipient=district['id'], action=desired, channel=channel,
                               resource_id=resource_id, supersedes=latest['id'] if latest else None))
        if orders:
            action['protection_orders'] = orders
        return action
