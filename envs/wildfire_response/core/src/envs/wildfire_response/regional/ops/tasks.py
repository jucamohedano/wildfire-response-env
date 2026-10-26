"""Trusted development tasks for the wildfire ops objective environment."""

import copy
import hashlib
import json

from .objectives import validate_objectives
from .generator import catalog_manifest, case_ids, get_generated_task


OPS_TASK_VERSION = 'wildfire-ops-task-pack-v0.4.0'


def _arrival_objective(identifier, report_id, deadline_minute):
    return {
        'id': identifier,
        'kind': 'resource_arrival',
        'sector_id': f'report:{report_id}',
        'acceptable_resource_kinds': ['engine'],
        'acceptable_missions': ['recon'],
        'deadline_minute': deadline_minute,
    }


_TASKS = {
    f'ops-{index:03d}': {
        'id': f'ops-{index:03d}',
        'scenario_id': f'regional-{index:03d}',
        'split': 'development',
        'task_set': 'dispatch',
        'group_id': f'regional-{index:03d}',
        'communication': {'mission_swap_once': index in (1, 3)},
        'objectives': (
            [
                _arrival_objective('arrive-report-0', 'report-0', 40),
                _arrival_objective('arrive-report-1', 'report-1', 60),
            ]
            if index == 3
            else [_arrival_objective('arrive-report-0', 'report-0', 40)]
        ),
    }
    for index in range(6)
}

_TASKS.update({
    f'control-{index:03d}': dict(
        id=f'control-{index:03d}', scenario_id=f'regional-train-{index if index in (1, 3) else 18 + index:03d}',
        split='development', task_set='control',
        group_id=f'regional-configuration-{index if index in (1, 3) else 18 + index:03d}',
        communication={'mission_swap_once': index in (1, 3)},
        objectives=[dict(id=kind, kind=kind, deadline_minute=120) for kind in (
            'evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline')])
    for index in range(6)
})

for _task in _TASKS.values():
    validate_objectives(_task['objectives'])

OPS_TASK_MANIFEST_SHA256 = hashlib.sha256(
    json.dumps(dict(development=_TASKS, generated=catalog_manifest()),
               sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
).hexdigest()


def get_ops_task(task_id):
    """Return an isolated copy of a development task by ID."""
    if isinstance(task_id, str) and task_id.startswith(('control-train-', 'control-validation-', 'control-test-')):
        return get_generated_task(task_id)
    if not isinstance(task_id, str) or task_id not in _TASKS:
        raise ValueError(f'Unknown ops task: {task_id!r}')
    task = copy.deepcopy(_TASKS[task_id])
    task['objectives'] = validate_objectives(task['objectives'])
    return task


def list_ops_tasks(task_set=None, *, split='development'):
    """Return isolated copies of all development tasks in manifest order."""
    if task_set not in (None, 'dispatch', 'control'):
        raise ValueError('Choose dispatch or control tasks')
    if split != 'development':
        if task_set == 'dispatch':
            raise ValueError('Generated splits contain control tasks only')
        return [get_generated_task(identifier) for identifier in case_ids(split)]
    return [get_ops_task(task_id) for task_id, task in _TASKS.items()
            if task_set is None or task['task_set'] == task_set]
