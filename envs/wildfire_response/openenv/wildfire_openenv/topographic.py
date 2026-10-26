"""Single-session localhost OpenEnv transport for pinned topographic exercises."""
import argparse
import os

from openenv.core.env_server.interfaces import Environment

from wildfire_core.topographic import CASE_IDS, TopographicEnv
from .prepared import (PreparedAction, PreparedClient, PreparedEnvironment,
                       PreparedObservation, PreparedState, create_openenv_app)


def configured_slope(slope_enabled):
    if slope_enabled is None:
        value = os.environ.get('WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED')
        if value not in ('true', 'false'):
            raise ValueError('Set WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED to true or false')
        return value == 'true'
    if type(slope_enabled) is not bool:
        raise ValueError('slope_enabled must be an explicit boolean')
    return slope_enabled


class TopographicEnvironment(PreparedEnvironment):
    def __init__(self, prepared_inputs=None, topographic_inputs=None, *,
                 expected_prepared_sha256=None, expected_topographic_sha256=None,
                 slope_enabled=None, allow_synthetic_conditions,
                 max_decisions=60, ticks_per_action=2):
        Environment.__init__(self)
        prepared_inputs = (prepared_inputs if prepared_inputs is not None
                           else os.environ.get('WILDFIRE_PREPARED_INPUTS'))
        topographic_inputs = (topographic_inputs if topographic_inputs is not None
                              else os.environ.get('WILDFIRE_TOPOGRAPHIC_INPUTS'))
        expected_prepared_sha256 = (expected_prepared_sha256 if expected_prepared_sha256 is not None
                                    else os.environ.get('WILDFIRE_PREPARED_SHA256'))
        expected_topographic_sha256 = (expected_topographic_sha256 if expected_topographic_sha256 is not None
                                       else os.environ.get('WILDFIRE_TOPOGRAPHIC_SHA256'))
        if prepared_inputs is None or not expected_prepared_sha256:
            raise ValueError('Set WILDFIRE_PREPARED_INPUTS and WILDFIRE_PREPARED_SHA256')
        if topographic_inputs is None or not expected_topographic_sha256:
            raise ValueError('Set WILDFIRE_TOPOGRAPHIC_INPUTS and WILDFIRE_TOPOGRAPHIC_SHA256')
        self.core = TopographicEnv(prepared_inputs, topographic_inputs,
            expected_prepared_sha256=expected_prepared_sha256,
            expected_topographic_sha256=expected_topographic_sha256,
            slope_enabled=configured_slope(slope_enabled),
            allow_synthetic_conditions=allow_synthetic_conditions,
            max_decisions=max_decisions, ticks_per_action=ticks_per_action)
        self._episode_id = None

    def reset(self, seed=None, episode_id=None, task_id=CASE_IDS[0], **kwargs):
        return super().reset(seed=seed, episode_id=episode_id, task_id=task_id, **kwargs)

    def list_tasks(self, split):
        if split != 'development':
            raise ValueError('Topographic cases are development exercises only')
        return [dict(id=identifier, split=split) for identifier in CASE_IDS]


def create_app(*, slope_enabled=None):
    slope_enabled = configured_slope(slope_enabled)
    return create_openenv_app(
        lambda: TopographicEnvironment(slope_enabled=slope_enabled, allow_synthetic_conditions=True),
        PreparedAction, PreparedObservation, env_name='wildfire_topographic_development',
        max_concurrent_envs=1, state_cls=PreparedState, mode='simulation')


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--host', choices=('127.0.0.1', 'localhost', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8003)
    parser.add_argument('--mode', choices=('simulation',), required=True)
    parser.add_argument('--slope-enabled', choices=('true', 'false'), required=True)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(slope_enabled=args.slope_enabled == 'true'),
                host=args.host, port=args.port)


if __name__ == '__main__':
    main()
