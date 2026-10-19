"""Use the same director against an already connected OpenEnv client."""
from ..openenv.models import RegionalAction


class OpenEnvDirectorSession:
    def __init__(self, client, policy):
        self.client, self.policy = client, policy
        self.result = None
        self.total_reward = 0.0

    def reset(self, scenario_id='regional-train-001', seed=None):
        if not isinstance(scenario_id, str) or not scenario_id.startswith('regional-train-'):
            raise ValueError('Director development uses training tasks only')
        self.result = self.client.reset(scenario_id=scenario_id, seed=seed)
        self.policy.reset()
        self.total_reward = 0.0
        return self.result

    def step(self):
        if self.result is None or self.result.done:
            raise RuntimeError('Reset a live OpenEnv director session before stepping')

        def check(action):
            checked = self.client.step(RegionalAction(operation='check', **action))
            return checked.observation.metadata['action_check']

        action = self.policy(self.result.observation.data, feedback=self.result.observation.metadata,
                             check_action=check)
        self.result = self.client.step(RegionalAction(**action))
        self.total_reward += self.result.reward or 0.0
        return self.result
