"""Synchronous or asynchronous client for the hosted environment."""
from openenv.core.env_client import EnvClient
from openenv.core.client_types import StepResult

from .models import WildfireAction, WildfireObservation, WildfireState


class WildfireClient(EnvClient[WildfireAction, WildfireObservation, WildfireState]):
    def _step_payload(self, action):
        return action.model_dump(exclude={'metadata'}, exclude_unset=True)

    def _parse_result(self, payload):
        observation = WildfireObservation(**payload.get('observation', {}))
        return StepResult(observation=observation, reward=payload.get('reward', observation.reward),
                          done=payload.get('done', observation.done), metadata=observation.metadata)

    def _parse_state(self, payload):
        return WildfireState(**payload)
