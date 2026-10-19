"""Typed wire contracts; engine validation checks mission semantics."""
from typing import Annotated, Literal

from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class ReportDisposition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str
    status: Literal['investigate', 'defer', 'dismiss']
    supporting_report_ids: list[str] = Field(default_factory=list)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: str
    purpose: Literal['inform', 'investigate', 'evacuate']
    channel: Literal['radio', 'broadcast', 'call']
    report_ids: list[str] = Field(default_factory=list)


Point = Annotated[list[StrictInt], Field(min_length=2, max_length=2)]


class ScoutOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    drone_id: str
    command: Literal['patrol', 'hold', 'continue', 'evacuate_town', 'evacuate_farm']
    reason: str
    waypoints: list[Point] = Field(default_factory=list, max_length=6)
    district_id: str = ''


class ExtinguisherOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    drone_id: str
    command: Literal['scout', 'contain', 'hold', 'evacuate_town', 'evacuate_farm']
    target_x: StrictInt
    target_y: StrictInt
    reason: str
    district_id: str = ''
    mission: str = ''


class TruckOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    truck_id: str
    command: Literal['attack_sector', 'continue', 'hold']
    reason: str
    target_x: StrictInt | None = None
    target_y: StrictInt | None = None


class Fleet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scout_orders: list[ScoutOrder] = Field(max_length=3)
    extinguisher_orders: list[ExtinguisherOrder] = Field(max_length=3)
    truck_orders: list[TruckOrder] = Field(max_length=3)
    mission: str = ''
    reason: str = ''


class WildfireAction(Action):
    fleet: Fleet
    report_dispositions: list[ReportDisposition] = Field(default_factory=list, max_length=100)
    messages: list[Message] = Field(default_factory=list, max_length=12)
    explanation: str = Field(default='', max_length=2000)


class WildfireObservation(Observation):
    data: dict
    terminated: bool = False
    truncated: bool = False


class WildfireState(State):
    tick: int = 0
    terminated: bool = False
    truncated: bool = False
