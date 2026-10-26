"""Single-session OpenEnv transport for the opt-in frozen benchmark."""
import argparse
import os
from typing import Literal

from openenv.core.env_server.interfaces import Environment
from pydantic import Field, model_validator

from wildfire_core.benchmark_v1 import BenchmarkEnv
from envs.wildfire_response.regional.benchmark_v1.catalog import task_ids
from .prepared import (
    Identifier, PreparedAction, PreparedClient, PreparedEnvironment,
    PreparedObservation, PreparedState, create_openenv_app,
)
from .topographic import configured_slope


class BenchmarkAction(PreparedAction):
    operation: Literal['advance', 'check', 'situation', 'trajectory', 'reports', 'events'] = 'advance'
    report_ids: list[Identifier] | None = Field(default=None, max_length=20)
    event_ids: list[Identifier] | None = Field(default=None, max_length=20)
    after_cursor: int = Field(default=0, strict=True, ge=0)
    limit: int = Field(default=20, strict=True, ge=1, le=20)

    def core_action(self):
        return self.model_dump(exclude={'metadata', 'operation', 'report_ids', 'event_ids', 'after_cursor', 'limit'},
                               exclude_unset=True)

    @model_validator(mode='after')
    def read_query(self):
        if self.operation in ('reports', 'events') and self.core_action():
            raise ValueError('Read operations do not accept orders')
        if self.operation != 'reports' and self.report_ids is not None:
            raise ValueError('report_ids is only valid for reports')
        if self.operation != 'events' and (self.event_ids is not None or 'after_cursor' in self.model_fields_set):
            raise ValueError('event_ids and after_cursor are only valid for events')
        if self.event_ids is not None and self.after_cursor != 0:
            raise ValueError('Event IDs cannot be combined with a nonzero cursor')
        if self.operation not in ('reports', 'events') and 'limit' in self.model_fields_set:
            raise ValueError('limit is only valid for bounded reads')
        return self


class BenchmarkEnvironment(PreparedEnvironment):
    def __init__(self, prepared_inputs=None, topographic_inputs=None, *,
                 expected_prepared_sha256=None, expected_topographic_sha256=None,
                 catalog=None, expected_catalog_sha256=None, profile='central',
                 slope_enabled=None, allow_synthetic_conditions,
                 max_decisions=60, ticks_per_action=2):
        Environment.__init__(self)
        prepared_inputs = prepared_inputs if prepared_inputs is not None else os.environ.get('WILDFIRE_PREPARED_INPUTS')
        topographic_inputs = topographic_inputs if topographic_inputs is not None else os.environ.get('WILDFIRE_TOPOGRAPHIC_INPUTS')
        expected_prepared_sha256 = (expected_prepared_sha256 if expected_prepared_sha256 is not None
                                    else os.environ.get('WILDFIRE_PREPARED_SHA256'))
        expected_topographic_sha256 = (expected_topographic_sha256 if expected_topographic_sha256 is not None
                                       else os.environ.get('WILDFIRE_TOPOGRAPHIC_SHA256'))
        if prepared_inputs is None or not expected_prepared_sha256:
            raise ValueError('Set WILDFIRE_PREPARED_INPUTS and WILDFIRE_PREPARED_SHA256')
        if topographic_inputs is None or not expected_topographic_sha256:
            raise ValueError('Set WILDFIRE_TOPOGRAPHIC_INPUTS and WILDFIRE_TOPOGRAPHIC_SHA256')
        self.core = BenchmarkEnv(prepared_inputs, topographic_inputs,
            expected_prepared_sha256=expected_prepared_sha256,
            expected_topographic_sha256=expected_topographic_sha256,
            slope_enabled=configured_slope(slope_enabled),
            allow_synthetic_conditions=allow_synthetic_conditions, catalog=catalog,
            expected_catalog_sha256=expected_catalog_sha256, profile=profile,
            max_decisions=max_decisions, ticks_per_action=ticks_per_action)
        self._episode_id = None
        self._state = PreparedState()

    def reset(self, seed=None, episode_id=None, task_id=None, **kwargs):
        if kwargs:
            raise ValueError('Unknown reset options')
        task_id = task_id if task_id is not None else self.list_tasks('development')[0]['id']
        observation, info = self.core.reset(task_id, seed)
        self._episode_id = episode_id if episode_id is not None else task_id
        self._state = PreparedState(episode_id=self._episode_id, minute=observation['minute'])
        return PreparedObservation(data=observation, reward=0.0, metadata=info)

    def step(self, action, timeout_s=None, **kwargs):
        if kwargs:
            raise ValueError('Unknown step options')
        if self._episode_id is None:
            raise RuntimeError('reset required')
        if action.operation == 'advance':
            observation, reward, terminated, truncated, info = self.core.step(action.core_action())
            self._state = PreparedState(episode_id=self._episode_id,
                step_count=self._state.step_count + 1, minute=observation['minute'],
                terminated=terminated, truncated=truncated)
            return PreparedObservation(data=observation, reward=reward, metadata=info,
                done=terminated or truncated, terminated=terminated, truncated=truncated)
        if action.operation in ('reports', 'events'):
            records = (self.core.get_reports(action.report_ids, limit=action.limit) if action.operation == 'reports'
                       else self.core.get_events(action.event_ids, after_cursor=action.after_cursor, limit=action.limit))
            data = {action.operation: records}
            if action.operation == 'events':
                data['next_cursor'] = action.after_cursor + len(records) if action.event_ids is None else None
            return PreparedObservation(data=data, reward=0.0,
                done=self._state.terminated or self._state.truncated,
                terminated=self._state.terminated, truncated=self._state.truncated)
        return super().step(action, timeout_s=timeout_s)

    @property
    def state(self):
        return self._state.model_copy(deep=True)

    def list_splits(self):
        return ['train', 'validation', 'test', 'development']

    def list_tasks(self, split):
        if split not in self.list_splits():
            raise ValueError('Unknown benchmark split')
        return [dict(id=identifier, split=split) for identifier in task_ids(self.core.catalog, split)]

    def get_task(self, split, index):
        tasks = self.list_tasks(split)
        if type(index) is not int or not 0 <= index < len(tasks):
            raise ValueError('Unknown benchmark task index')
        return tasks[index]

    def get_task_range(self, split, start=None, stop=None):
        tasks = self.list_tasks(split)
        for bound in (start, stop):
            if bound is not None and (type(bound) is not int or not 0 <= bound <= len(tasks)):
                raise ValueError('Invalid task range')
        return tasks[start:stop]


class BenchmarkClient(PreparedClient):
    """Prepared observation/state decoding with the benchmark's bounded action model."""


def create_app(*, slope_enabled=None, **options):
    slope_enabled = configured_slope(slope_enabled)
    return create_openenv_app(
        lambda: BenchmarkEnvironment(slope_enabled=slope_enabled,
                                     allow_synthetic_conditions=True, **options),
        BenchmarkAction, PreparedObservation, env_name='wildfire_benchmark_v1',
        max_concurrent_envs=1, state_cls=PreparedState, mode='simulation')


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--host', choices=('127.0.0.1', 'localhost', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8004)
    parser.add_argument('--mode', choices=('simulation',), required=True)
    parser.add_argument('--slope-enabled', choices=('true', 'false'), required=True)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(slope_enabled=args.slope_enabled == 'true'), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
