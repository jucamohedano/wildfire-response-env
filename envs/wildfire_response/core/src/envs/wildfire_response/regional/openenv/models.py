"""Typed regional action and safe observation contracts."""
from typing import Literal

from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_id: StrictStr
    sector_id: StrictStr
    mission: Literal['recon', 'suppress']


class RegionalMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: StrictStr
    purpose: Literal['status', 'inform', 'evacuate']


class RegionalAction(Action):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['advance', 'check'] = 'advance'
    requests: list[ResourceRequest] = Field(default_factory=list, max_length=12)
    authorizations: list[StrictStr] = Field(default_factory=list, max_length=12)
    releases: list[StrictStr] = Field(default_factory=list, max_length=12)
    messages: list[RegionalMessage] = Field(default_factory=list, max_length=12)
    explanation: str = Field(default='', max_length=1000)


class RegionalObservation(Observation):
    data: dict
    terminated: bool = False
    truncated: bool = False


class RegionalState(State):
    minute: int = 0
    terminated: bool = False
    truncated: bool = False
