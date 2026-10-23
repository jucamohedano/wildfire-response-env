"""Trusted development tasks for the wildfire ops objective environment."""

import copy
import hashlib
import json

from .objectives import validate_objectives


OPS_TASK_VERSION = 'wildfire-ops-task-pack-v0.1.0'


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
        'group_id': f'regional-{index:03d}',
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

for _task in _TASKS.values():
    validate_objectives(_task['objectives'])

OPS_TASK_MANIFEST_SHA256 = hashlib.sha256(
    json.dumps(_TASKS, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
).hexdigest()


def get_ops_task(task_id):
    """Return an isolated copy of a development task by ID."""
    if not isinstance(task_id, str) or task_id not in _TASKS:
        raise ValueError(f'Unknown ops task: {task_id!r}')
    task = copy.deepcopy(_TASKS[task_id])
    task['objectives'] = validate_objectives(task['objectives'])
    return task


def list_ops_tasks():
    """Return isolated copies of all development tasks in manifest order."""
    return [get_ops_task(task_id) for task_id in _TASKS]
