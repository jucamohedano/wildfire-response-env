"""Standard OpenEnv client for the regional adapter."""
from openenv.core.env_client import EnvClient
from openenv.core.client_types import StepResult

from .models import RegionalAction, RegionalObservation, RegionalState


class RegionalClient(EnvClient[RegionalAction, RegionalObservation, RegionalState]):
    def _step_payload(self, action):
        return action.model_dump(exclude={'metadata'}, exclude_unset=True)

    def _parse_result(self, payload):
        observation = RegionalObservation(**payload.get('observation',{}))
        return StepResult(observation=observation, reward=payload.get('reward',observation.reward),
                          done=payload.get('done',observation.done), metadata=observation.metadata)

    def _parse_state(self, payload):
        return RegionalState(**payload)
