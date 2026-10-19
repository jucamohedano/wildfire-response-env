"""One isolated core episode behind OpenEnv's standard lifecycle."""
from openenv.core.env_server.interfaces import Environment

from ..core.environment import WildfireResponseEnv
from ..core.scenarios import generate_tasks
from .models import WildfireObservation, WildfireState


class WildfireEnvironment(Environment):
    SUPPORTS_CONCURRENT_SESSIONS = False

    def __init__(self):
        super().__init__()
        self.core = WildfireResponseEnv()

    def reset(self, seed=None, episode_id=None, scenario_id=None, split='train', index=0, **kwargs):
        if kwargs:
            raise ValueError('Unknown reset options')
        if scenario_id is None:
            scenario_id = self.get_task(split, index)['id']
        observation, info = self.core.reset(scenario_id, seed)
        if episode_id is not None:
            self.core.sim.incident_id = str(episode_id)
        return WildfireObservation(data=observation, reward=0.0, metadata=info)

    def step(self, action, timeout_s=None, **kwargs):
        raw = action.model_dump(exclude={'metadata'}, exclude_unset=True)
        observation, value, terminated, truncated, info = self.core.step(raw)
        return WildfireObservation(data=observation, reward=value, done=terminated or truncated,
                                   terminated=terminated, truncated=truncated, metadata=info)

    @property
    def state(self):
        if self.core.sim is None:
            return WildfireState()
        return WildfireState(episode_id=self.core.sim.incident_id, tick=self.core.sim.tick,
                             step_count=self.core.decisions, terminated=self.core.terminated,
                             truncated=self.core.truncated)

    def list_splits(self):
        return ['train', 'validation', 'test']

    def list_tasks(self, split):
        if split not in self.list_splits():
            raise ValueError('Unknown split')
        return [dict(id=task['id'], split=split) for task in generate_tasks() if task['split'] == split]

    def num_tasks(self, split):
        return len(self.list_tasks(split))

    def get_task(self, split, index):
        return self.list_tasks(split)[index]

    def get_task_range(self, split, start=None, stop=None):
        return self.list_tasks(split)[start:stop]
