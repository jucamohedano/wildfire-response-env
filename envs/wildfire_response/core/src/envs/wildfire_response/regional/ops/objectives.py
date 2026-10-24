"""Pure objective scoring and trajectory verification for ops tasks."""

import math
import hashlib
from collections.abc import Mapping


REWARD_VERSION = 'wildfire-ops-objective-fraction-v3'
TERMINAL_KINDS = {'evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline'}
_OBJECTIVE_FIELDS = {
    'id',
    'kind',
    'sector_id',
    'acceptable_resource_kinds',
    'acceptable_missions',
    'deadline_minute',
}


def validate_objectives(objectives):
    """Validate and return a copy of a non-empty objective list."""
    if not isinstance(objectives, list) or not objectives:
        raise ValueError('objectives must be a non-empty list')

    validated = []
    identifiers = set()
    predicates = set()
    for index, objective in enumerate(objectives):
        label = f'objectives[{index}]'
        if not isinstance(objective, dict):
            raise ValueError(f'{label} must be a record')
        kind = objective.get('kind')
        if not isinstance(kind, str):
            raise ValueError(f'{label}.kind must be a string')
        expected_fields = {'id', 'kind', 'deadline_minute'} if kind in TERMINAL_KINDS else _OBJECTIVE_FIELDS
        fields = set(objective)
        unknown = fields - expected_fields
        missing = expected_fields - fields
        if unknown:
            raise ValueError(f'{label} has unknown field(s): {", ".join(sorted(unknown))}')
        if missing:
            raise ValueError(f'{label} is missing field(s): {", ".join(sorted(missing))}')

        identifier = _nonempty_string(objective['id'], f'{label}.id')
        if identifier in identifiers:
            raise ValueError(f'duplicate objective id: {identifier}')
        identifiers.add(identifier)

        kind = _nonempty_string(objective['kind'], f'{label}.kind')
        if kind not in {'resource_arrival', *TERMINAL_KINDS}:
            raise ValueError(f'{label}.kind is unsupported')

        deadline = objective['deadline_minute']
        if type(deadline) is not int or deadline <= 0:
            raise ValueError(f'{label}.deadline_minute must be a positive integer')
        if kind in TERMINAL_KINDS:
            if kind in predicates:
                raise ValueError(f'{label} duplicates an existing objective predicate')
            predicates.add(kind)
            validated.append(dict(id=identifier, kind=kind, deadline_minute=deadline))
            continue

        sector_id = _nonempty_string(objective['sector_id'], f'{label}.sector_id')
        resource_kinds = sorted(set(_nonempty_string_list(
            objective['acceptable_resource_kinds'], f'{label}.acceptable_resource_kinds')))
        missions = sorted(set(_nonempty_string_list(
            objective['acceptable_missions'], f'{label}.acceptable_missions')))
        predicate = (kind, sector_id, tuple(resource_kinds), tuple(missions), deadline)
        if predicate in predicates:
            raise ValueError(f'{label} duplicates an existing objective predicate')
        if any(
                previous['kind'] == 'resource_arrival'
                and objective['sector_id'] == previous['sector_id']
                and set(objective['acceptable_resource_kinds']) & set(previous['acceptable_resource_kinds'])
                and set(objective['acceptable_missions']) & set(previous['acceptable_missions'])
                for previous in validated):
            raise ValueError(f'{label} overlaps an existing arrival predicate and could duplicate credit')
        predicates.add(predicate)

        validated.append({
            'id': identifier,
            'kind': kind,
            'sector_id': sector_id,
            'acceptable_resource_kinds': resource_kinds,
            'acceptable_missions': missions,
            'deadline_minute': deadline,
        })
    return validated


def objective_step(observation, objectives, completed_ids, *, terminal=False, evidence=None):
    """Score supplied objective specs without establishing their authority.

    This low-level helper assumes its caller has already validated that the
    objective specs are trusted for the episode. Use
    :func:`verify_ops_trajectory` to verify a recorded trajectory against the
    checked-in task manifest.
    """
    validated = validate_objectives(objectives)
    completed = _completed_set(completed_ids, validated)
    minute, resources = _observation_resources(observation)

    newly_completed = []
    for objective in validated:
        if objective['id'] in completed or minute > objective['deadline_minute']:
            continue
        if objective['kind'] in TERMINAL_KINDS:
            satisfied = terminal and _terminal_satisfies(observation, objective['kind'], evidence)
        else:
            satisfied = any(_resource_satisfies(resource, objective) for resource in resources)
        if satisfied:
            newly_completed.append(objective['id'])
    return newly_completed, len(newly_completed) / len(validated)


def _terminal_satisfies(observation, kind, evidence):
    if not isinstance(evidence, Mapping) or not evidence.get('schedule_complete'):
        return False
    if kind == 'evidence_triage':
        assessed = {record['report_id'] for record in observation['report_assessments']
                    if record['assessment'] in ('fire_observed', 'no_fire_observed')}
        return all(report['id'] in assessed for report in observation['reports']
                   if report.get('location') is not None)
    if kind == 'population_safety':
        return all(district['exposed'] == 0 and district['status'] not in ('evacuating', 'blocked')
                   for district in observation['districts'])
    if kind == 'incident_resolved':
        return evidence['active_fire_cells'] == 0
    return evidence['redundant_notices'] == 0 and evidence['unsupported_evacuations'] == 0


def verify_ops_trajectory(trajectory):
    """Replay a public ops trace and verify its manifest-bound objective rewards."""
    if not isinstance(trajectory, Mapping):
        raise ValueError('trajectory must be a record')
    required = ('interface_version', 'task_id', 'task_set', 'task_version', 'task_manifest_sha256',
                'communication_protocol_version',
                'evidence_version',
                'scenario_engine_version', 'scenario_sha256',
                'reward_version', 'core_version', 'core_reward_version', 'core_pack_sha256',
                'scenario_id', 'seed',
                'max_decisions', 'ticks_per_action', 'objectives', 'initial_observation')
    missing = [field for field in required if field not in trajectory]
    if missing:
        raise ValueError(f'trajectory is missing required field(s): {", ".join(missing)}')
    from .environment import OPS_INTERFACE_VERSION, OpsControlCenterEnv
    from .communications import PROTOCOL_VERSION
    from .evidence import EVIDENCE_VERSION
    from .scenario_core import SCENARIO_ENGINE_VERSION

    if trajectory['interface_version'] != OPS_INTERFACE_VERSION:
        raise ValueError('trajectory.interface_version does not match this verifier')
    if trajectory['communication_protocol_version'] != PROTOCOL_VERSION:
        raise ValueError('trajectory.communication_protocol_version does not match this verifier')
    if trajectory['evidence_version'] != EVIDENCE_VERSION:
        raise ValueError('trajectory.evidence_version does not match this verifier')
    if trajectory['scenario_engine_version'] != SCENARIO_ENGINE_VERSION:
        raise ValueError('trajectory.scenario_engine_version does not match this verifier')
    task_id = _nonempty_string(trajectory['task_id'], 'trajectory.task_id')
    task_version = _nonempty_string(trajectory['task_version'], 'trajectory.task_version')
    scenario_id = _nonempty_string(trajectory['scenario_id'], 'trajectory.scenario_id')

    from .tasks import OPS_TASK_MANIFEST_SHA256, OPS_TASK_VERSION, get_ops_task
    from ..data import PACK
    from ..environment import REWARD_VERSION as CORE_REWARD_VERSION, VERSION as CORE_VERSION

    if trajectory['reward_version'] != REWARD_VERSION:
        raise ValueError('trajectory.reward_version does not match this verifier')
    if trajectory['core_version'] != CORE_VERSION:
        raise ValueError('trajectory.core_version does not match this verifier')
    if trajectory['core_reward_version'] != CORE_REWARD_VERSION:
        raise ValueError('trajectory.core_reward_version does not match this verifier')
    if trajectory['task_manifest_sha256'] != OPS_TASK_MANIFEST_SHA256:
        raise ValueError('trajectory.task_manifest_sha256 does not match this verifier')
    core_pack_sha256 = hashlib.sha256(PACK.read_bytes()).hexdigest()
    if trajectory['core_pack_sha256'] != core_pack_sha256:
        raise ValueError('trajectory.core_pack_sha256 does not match this verifier')

    try:
        task = get_ops_task(task_id)
    except ValueError as error:
        raise ValueError(f'unknown ops task: {task_id}') from error
    if not isinstance(task, Mapping):
        raise ValueError(f'canonical task {task_id} must be a record')

    canonical_scenario_id = task.get('scenario_id')
    canonical_objectives = task.get('objectives')
    if task.get('id') != task_id:
        raise ValueError(f'canonical task ID does not match requested task: {task_id}')
    if task_version != OPS_TASK_VERSION:
        raise ValueError(
            f'trajectory.task_version mismatch: recorded {task_version!r}, '
            f'expected {OPS_TASK_VERSION!r}')
    if type(trajectory['max_decisions']) is not int or trajectory['max_decisions'] < 1:
        raise ValueError('trajectory.max_decisions must be a positive integer')
    if scenario_id != canonical_scenario_id:
        raise ValueError(
            f'trajectory.scenario_id mismatch: recorded {scenario_id!r}, '
            f'expected {canonical_scenario_id!r}')
    if trajectory['task_set'] != task['task_set']:
        raise ValueError('trajectory.task_set does not match the canonical task manifest')
    objectives = validate_objectives(trajectory['objectives'])
    validate_objectives(canonical_objectives)
    if trajectory['objectives'] != canonical_objectives:
        raise ValueError('trajectory.objectives do not match the canonical task manifest')

    if type(trajectory['ticks_per_action']) is not int or trajectory['ticks_per_action'] < 1:
        raise ValueError('trajectory.ticks_per_action must be a positive integer')
    if any(objective['deadline_minute'] > trajectory['max_decisions'] * trajectory['ticks_per_action']
           for objective in objectives):
        raise ValueError('canonical objective deadlines exceed the recorded episode horizon')
    if any(objective['deadline_minute'] % trajectory['ticks_per_action'] for objective in objectives):
        raise ValueError('canonical objective deadlines do not align with the recorded decision interval')
    env = OpsControlCenterEnv(max_decisions=trajectory['max_decisions'],
                             ticks_per_action=trajectory['ticks_per_action'])
    expected_initial, _ = env.reset(task_id, trajectory['seed'])
    if trajectory['scenario_sha256'] != env.trajectory()['scenario_sha256']:
        raise ValueError('trajectory.scenario_sha256 does not match the canonical scenario')
    if trajectory['initial_observation'] != expected_initial:
        raise ValueError('trajectory initial observation does not replay from its task and seed')

    transitions = trajectory.get('transitions')
    if not isinstance(transitions, list) or not transitions:
        raise ValueError('trajectory.transitions must be a list')

    completed = set()
    completed_order = []
    for index, transition in enumerate(transitions):
        label = f'transitions[{index}]'
        if not isinstance(transition, Mapping):
            raise ValueError(f'{label} must be a record')
        step_fields = ('action', 'action_valid', 'observation', 'objective_reward', 'terminated', 'truncated')
        missing_step_fields = [field for field in step_fields if field not in transition]
        if missing_step_fields:
            raise ValueError(f'{label} is missing field(s): {", ".join(missing_step_fields)}')
        action = transition['action']
        if not isinstance(action, Mapping) or 'explanation' in action:
            raise ValueError(f'{label}.action must be a public structured ops action')

        try:
            result = env.submit_action(dict(action))
        except RuntimeError as error:
            raise ValueError(f'{label} occurs after the episode ended') from error
        actual_observation = result[0]
        if transition['observation'] != actual_observation:
            raise ValueError(f'{label} observation does not replay from its action')
        if transition['action_valid'] is not (result[4]['error'] is None):
            raise ValueError(f'{label} action-valid flag does not replay')
        if transition['terminated'] is not result[2] or transition['truncated'] is not result[3]:
            raise ValueError(f'{label} termination flags do not replay')

        newly_completed, expected_reward = objective_step(actual_observation, objectives, completed,
            terminal=result[2] or result[3], evidence=env._evaluation_evidence())
        recorded_reward = transition['objective_reward']
        if (isinstance(recorded_reward, bool)
                or not isinstance(recorded_reward, (int, float))
                or not math.isfinite(recorded_reward)):
            raise ValueError(f'{label}.objective_reward must be a finite number')
        if not math.isclose(recorded_reward, expected_reward, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f'{label}.objective_reward mismatch: recorded {recorded_reward}, '
                f'expected {expected_reward}')

        for identifier in newly_completed:
            if identifier in completed:
                raise ValueError(f'{label} repeats objective credit for {identifier}')
            completed.add(identifier)
            completed_order.append(identifier)

    if not (transitions[-1]['terminated'] or transitions[-1]['truncated']):
        raise ValueError('trajectory must end at termination or truncation')

    return {
        'total_score': len(completed) / len(objectives),
        'completed_count': len(completed),
        'total_count': len(objectives),
        'completed_ids': completed_order,
        'verified': True,
        'core_replayed': True,
        'by_kind': {objective['kind']: objective['id'] in completed for objective in objectives
                    if objective['kind'] in TERMINAL_KINDS},
        'all_objectives_met': len(completed) == len(objectives),
    }


def _nonempty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a non-empty string')
    return value


def _nonempty_string_list(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label} must be a non-empty list of non-empty strings')
    return [_nonempty_string(item, f'{label}[{index}]') for index, item in enumerate(value)]


def _completed_set(completed_ids, objectives):
    if isinstance(completed_ids, (str, bytes)):
        raise ValueError('completed_ids must be a collection of objective IDs')
    try:
        identifiers = list(completed_ids)
    except TypeError as error:
        raise ValueError('completed_ids must be a collection of objective IDs') from error
    known = {objective['id'] for objective in objectives}
    if any(not isinstance(identifier, str) or identifier not in known for identifier in identifiers):
        raise ValueError('completed_ids contains an unknown objective ID')
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('completed_ids contains duplicate objective IDs')
    return set(identifiers)


def _observation_resources(observation):
    if not isinstance(observation, Mapping):
        raise ValueError('observation must be a record')
    minute = observation.get('minute')
    if type(minute) is not int or minute < 0:
        raise ValueError('observation.minute must be a non-negative integer')
    resources = observation.get('resources')
    if not isinstance(resources, list):
        raise ValueError('observation.resources must be a list')
    if any(not isinstance(resource, Mapping) for resource in resources):
        raise ValueError('each observation resource must be a record')
    return minute, resources


def _resource_satisfies(resource, objective):
    return (
        resource.get('state') == 'on_scene'
        and resource.get('sector_id') == objective['sector_id']
        and resource.get('kind') in objective['acceptable_resource_kinds']
        and resource.get('mission') in objective['acceptable_missions']
    )
