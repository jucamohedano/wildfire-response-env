"""Single-session OpenEnv transport for explicitly synthetic prepared-data exercises."""
import argparse
import json
import os
from typing import Annotated, Literal

from openenv.core.client_types import StepResult
from openenv.core.env_client import EnvClient
from openenv.core.env_server import create_app as create_openenv_app
from openenv.core.env_server.interfaces import Environment
from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, ConfigDict, Field, model_validator

from wildfire_core.prepared import CASE_IDS, PreparedDataEnv


Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=255)]


class Allocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_id: Identifier
    sector_id: Identifier
    mission: Literal['recon', 'suppress']


class ReadbackCorrection(Allocation):
    readback_id: Identifier


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: Identifier
    purpose: Literal['status', 'inform', 'evacuate']


class Assessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_ids: list[Identifier] = Field(min_length=1, max_length=20)
    assessment: Literal['fire_observed', 'no_fire_observed', 'defer']


class ProtectionOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: Identifier
    action: Literal['shelter', 'evacuate', 'release']
    channel: Literal['broadcast', 'in_person']
    resource_id: Identifier | None
    supersedes: Identifier | None


class PreparedAction(Action):
    model_config = ConfigDict(extra='forbid')
    metadata: dict = Field(default_factory=dict, max_length=0)
    operation: Literal['advance', 'check', 'situation', 'trajectory'] = 'advance'
    requests: list[Allocation] = Field(default_factory=list, max_length=12)
    authorizations: list[Identifier] = Field(default_factory=list, max_length=12)
    releases: list[Identifier] = Field(default_factory=list, max_length=12)
    messages: list[Message] = Field(default_factory=list, max_length=12)
    readback_confirmations: list[Identifier] = Field(default_factory=list, max_length=12)
    readback_corrections: list[ReadbackCorrection] = Field(default_factory=list, max_length=12)
    triage: list[Assessment] = Field(default_factory=list, max_length=12)
    clarifications: list[Identifier] = Field(default_factory=list, max_length=4)
    protection_orders: list[ProtectionOrder] = Field(default_factory=list, max_length=4)

    def core_action(self):
        return self.model_dump(exclude={'metadata', 'operation'}, exclude_unset=True)

    @model_validator(mode='after')
    def bounded_payload(self):
        raw = self.core_action()
        if len(json.dumps(raw, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        if self.operation in ('situation', 'trajectory') and raw:
            raise ValueError('Read operations do not accept orders')
        return self


class PreparedObservation(Observation):
    data: dict
    terminated: bool = False
    truncated: bool = False
    trajectory: dict | None = None


class PreparedState(State):
    model_config = ConfigDict(extra='forbid')
    minute: int = 0
    terminated: bool = False
    truncated: bool = False


class PreparedEnvironment(Environment):
    SUPPORTS_CONCURRENT_SESSIONS = False

    def __init__(self, inputs=None, *, expected_sha256=None, allow_synthetic_conditions,
                 max_decisions=60, ticks_per_action=2):
        super().__init__()
        inputs = inputs if inputs is not None else os.environ.get('WILDFIRE_PREPARED_INPUTS')
        expected_sha256 = (expected_sha256 if expected_sha256 is not None
                           else os.environ.get('WILDFIRE_PREPARED_SHA256'))
        if inputs is None or not expected_sha256:
            raise ValueError('Set WILDFIRE_PREPARED_INPUTS and WILDFIRE_PREPARED_SHA256')
        self.core = PreparedDataEnv(inputs, expected_sha256=expected_sha256,
                                    allow_synthetic_conditions=allow_synthetic_conditions,
                                    max_decisions=max_decisions, ticks_per_action=ticks_per_action)
        self._episode_id = None

    def reset(self, seed=None, episode_id=None, task_id=CASE_IDS[0], **kwargs):
        if kwargs:
            raise ValueError('Unknown reset options')
        observation, info = self.core.reset(task_id, seed)
        self._episode_id = episode_id if episode_id is not None else task_id
        return PreparedObservation(data=observation, reward=0.0, metadata=info)

    def step(self, action, timeout_s=None, **kwargs):
        if kwargs:
            raise ValueError('Unknown step options')
        raw = action.core_action()
        if action.operation == 'advance':
            observation, reward, terminated, truncated, info = self.core.step(raw)
            return PreparedObservation(data=observation, reward=reward,
                                       done=terminated or truncated, terminated=terminated,
                                       truncated=truncated, metadata=info)
        metadata = {}
        trajectory = None
        if action.operation == 'check':
            metadata['action_check'] = self.core.check_orders(raw)
        elif action.operation == 'trajectory':
            trajectory = self.core.trajectory()
        state = self.state
        return PreparedObservation(data=self.core.get_situation(), reward=0.0,
                                   done=state.terminated or state.truncated,
                                   terminated=state.terminated, truncated=state.truncated,
                                   metadata=metadata, trajectory=trajectory)

    @property
    def state(self):
        core = self.core._core
        if core.sim is None:
            return PreparedState()
        return PreparedState(episode_id=self._episode_id, step_count=core.decisions,
                             minute=core.sim.tick, terminated=core.terminated,
                             truncated=core.truncated)

    def list_splits(self):
        return ['development']

    def list_tasks(self, split):
        if split != 'development':
            raise ValueError('Prepared cases are development exercises only')
        return [dict(id=identifier, split=split) for identifier in CASE_IDS]

    def num_tasks(self, split):
        return len(self.list_tasks(split))

    def get_task(self, split, index):
        tasks = self.list_tasks(split)
        if type(index) is not int or not 0 <= index < len(tasks):
            raise ValueError('Unknown development task index')
        return tasks[index]

    def get_task_range(self, split, start=None, stop=None):
        return self.list_tasks(split)[start:stop]


class PreparedClient(EnvClient[PreparedAction, PreparedObservation, PreparedState]):
    def _step_payload(self, action):
        return action.model_dump(exclude={'metadata'}, exclude_unset=True)

    def _parse_result(self, payload):
        observation = PreparedObservation(**payload.get('observation', {}))
        return StepResult(observation=observation, reward=payload.get('reward', observation.reward),
                          done=payload.get('done', observation.done), metadata=observation.metadata)

    def _parse_state(self, payload):
        return PreparedState(**payload)


def create_app():
    """Explicitly enable synthetic exercise conditions; require caller-pinned local inputs."""
    return create_openenv_app(lambda: PreparedEnvironment(allow_synthetic_conditions=True),
                             PreparedAction, PreparedObservation,
                             env_name='wildfire_prepared_development', max_concurrent_envs=1,
                             state_cls=PreparedState, mode='simulation')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
