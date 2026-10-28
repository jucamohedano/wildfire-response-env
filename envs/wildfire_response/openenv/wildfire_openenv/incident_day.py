"""Typed single-session adapter for public incident-day operations.

OpenEnv is optional for the standalone local viewer; when installed these
models and environment implement its native interfaces.
"""
import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from openenv.core.env_server.interfaces import Environment
    from openenv.core.env_server.types import Action, Observation, State
except ModuleNotFoundError as error:
    if error.name != 'openenv':
        raise

    class Environment:
        pass

    class Action(BaseModel):
        metadata: dict = Field(default_factory=dict)

    class Observation(BaseModel):
        done: bool = False
        reward: float | None = None
        metadata: dict = Field(default_factory=dict)

    class State(BaseModel):
        episode_id: str | None = None
        step_count: int = 0

from wildfire_core.incident_day import CASE_IDS, IncidentDayEnv


Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AllocationRequest(StrictModel):
    resource_id: Identifier
    sector_id: Identifier
    approach_id: Identifier


class ProtectionOrder(StrictModel):
    group_id: Identifier
    action: Literal['shelter', 'evacuate']
    destination_id: Identifier | None
    approach_id: Identifier | None = None

    @model_validator(mode='after')
    def validate_destination(self):
        if (self.action == 'shelter') != (self.destination_id is None):
            raise ValueError('Evacuation requires a destination; shelter must not select one')
        if (self.action == 'shelter') != (self.approach_id is None):
            raise ValueError('Evacuation requires an approach; shelter must not select one')
        return self


class StatusRequest(StrictModel):
    center_id: Identifier
    subject: Literal['resources', 'situation'] = 'situation'


class IncidentDayAction(Action):
    model_config = ConfigDict(extra='forbid', strict=True)
    metadata: dict = Field(default_factory=dict, max_length=0)
    advance_minutes: int | None = Field(default=None, ge=1, le=60)
    requests: list[AllocationRequest] = Field(default_factory=list, max_length=6)
    authorizations: list[Identifier] = Field(default_factory=list, max_length=6)
    releases: list[Identifier] = Field(default_factory=list, max_length=6)
    protection_orders: list[ProtectionOrder] = Field(default_factory=list, max_length=6)
    status_requests: list[StatusRequest] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def validate_wait(self):
        if 'advance_minutes' in self.model_fields_set:
            if self.advance_minutes is None or self.model_fields_set - {'advance_minutes', 'metadata'}:
                raise ValueError('advance_minutes must be the only action field')
        return self

    def core_action(self):
        action = self.model_dump(exclude={'metadata'}, exclude_unset=True)
        for order in action.get('protection_orders', []):
            order.setdefault('approach_id', None)
        return action


class IncidentDayObservation(Observation):
    data: dict
    terminated: bool = False
    truncated: bool = False


class IncidentDayState(State):
    model_config = ConfigDict(extra='forbid', strict=True)
    minute: int = Field(default=0, ge=0, le=1440)
    terminated: bool = False
    truncated: bool = False


_SITUATION_FIELDS = frozenset(('interface_version', 'task_id', 'minute', 'decision', 'clock',
    'resources', 'districts', 'destinations', 'sensor_memory', 'wind', 'reports', 'report_count',
    'status_requests', 'offers', 'pending_resource_requests', 'access_restrictions', 'protection_orders', 'centers',
    'sectors', 'input_approval', 'aircraft_operable', 'rules'))
_INFO_FIELDS = frozenset(('interface_version', 'minute', 'error', 'wake_reason'))


def _public_situation(value):
    return copy.deepcopy({key: item for key, item in value.items() if key in _SITUATION_FIELDS})


def _public_info(value):
    return copy.deepcopy({key: item for key, item in value.items() if key in _INFO_FIELDS})


class IncidentDayEnvironment(Environment):
    SUPPORTS_CONCURRENT_SESSIONS = False

    def __init__(self, inputs, network, *, expected_inputs_sha256, expected_network_sha256,
                 allow_unapproved_data=False):
        super().__init__()
        self.core = IncidentDayEnv(inputs, network, expected_inputs_sha256=expected_inputs_sha256,
            expected_network_sha256=expected_network_sha256, allow_unapproved_data=allow_unapproved_data)
        self._state = IncidentDayState()
        self._frames = []

    def reset(self, seed=None, episode_id=None, task_id=CASE_IDS[0], **kwargs):
        if kwargs:
            raise ValueError('Unknown reset options')
        if task_id not in CASE_IDS:
            raise ValueError('Unknown incident-day scenario')
        if seed is not None and (type(seed) is not int or not 0 <= seed <= 2**63 - 1):
            raise ValueError('seed must be a bounded nonnegative integer')
        if episode_id is not None:
            episode_id = _identifier(episode_id)
        observation, info = self.core.reset(task_id, seed)
        self._state = IncidentDayState(episode_id=episode_id or task_id)
        public = _public_situation(observation)
        self._frames = [public]
        return IncidentDayObservation(data=copy.deepcopy(public), reward=0.0, metadata=_public_info(info))

    def _require_reset(self):
        if self._state.episode_id is None:
            raise RuntimeError('reset required')

    def step(self, action, timeout_s=None, **kwargs):
        self._require_reset()
        if kwargs:
            raise ValueError('Unknown step options')
        if not isinstance(action, IncidentDayAction):
            raise TypeError('IncidentDayAction required')
        observation, reward, terminated, truncated, info = self.core.step(action.core_action())
        public = _public_situation(observation)
        self._state = IncidentDayState(episode_id=self._state.episode_id,
            step_count=self._state.step_count + 1, minute=public['minute'],
            terminated=terminated, truncated=truncated)
        self._frames.append(public)
        return IncidentDayObservation(data=copy.deepcopy(public), reward=reward,
            done=terminated or truncated, terminated=terminated, truncated=truncated, metadata=_public_info(info))

    @property
    def state(self):
        return self._state.model_copy(deep=True)

    def check_action(self, action):
        self._require_reset()
        if not isinstance(action, IncidentDayAction):
            raise TypeError('IncidentDayAction required')
        return copy.deepcopy(self.core.check_action(action.core_action()))

    def route_options(self, resource_id, sector_id='incident'):
        self._require_reset()
        return copy.deepcopy(self.core.route_options(_identifier(resource_id), _identifier(sector_id)))

    def evacuation_options(self, group_id, destination_id):
        self._require_reset()
        return copy.deepcopy(self.core.evacuation_options(_identifier(group_id), _identifier(destination_id)))

    def request_status(self, center_id, subject='situation'):
        request = StatusRequest(center_id=center_id, subject=subject)
        return self.step(IncidentDayAction(status_requests=[request]))

    def get_reports(self, ids=None, after_cursor=0, limit=20):
        self._require_reset()
        query = ReportQuery(ids=ids, after_cursor=after_cursor, limit=limit)
        return copy.deepcopy(self.core.get_reports(query.ids, after_cursor=query.after_cursor, limit=query.limit))

    def get_situation(self):
        self._require_reset()
        return _public_situation(self.core.get_situation())

    def trajectory(self):
        self._require_reset()
        return copy.deepcopy(self._frames)

    def list_splits(self):
        return ['development']

    def list_tasks(self, split):
        if split != 'development':
            raise ValueError('Unknown split')
        return [dict(id=identifier, split=split) for identifier in CASE_IDS]


def _identifier(value):
    from pydantic import TypeAdapter
    return TypeAdapter(Identifier).validate_python(value)


class ReportQuery(StrictModel):
    ids: list[Identifier] | None = Field(default=None, max_length=20)
    after_cursor: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=20)
