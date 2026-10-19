"""A single regional episode, with bounded non-advancing action checks."""
from openenv.core.env_server.interfaces import Environment

from ..environment import RegionalResponseEnv
from ..scenarios import generate_tasks
from .models import RegionalObservation, RegionalState


class RegionalEnvironment(Environment):
    SUPPORTS_CONCURRENT_SESSIONS = False

    def __init__(self):
        super().__init__()
        self.core = RegionalResponseEnv()

    def reset(self, seed=None, episode_id=None, scenario_id=None, split='train', index=0, **kwargs):
        if kwargs:
            raise ValueError('Unknown reset options')
        identifier = scenario_id if scenario_id is not None else self.get_task(split,index)['id']
        observation, info = self.core.reset(identifier,seed)
        if episode_id is not None:
            self.core.sim.incident_id = str(episode_id)
        return RegionalObservation(data=observation, reward=0.0, metadata=info)

    def step(self, action, timeout_s=None, **kwargs):
        if kwargs:
            raise ValueError('Unknown step options')
        raw = action.model_dump(exclude={'metadata','operation'}, exclude_unset=True)
        if action.operation == 'check':
            check = self.core.check_action(raw)
            return RegionalObservation(data=self.core.observe(), reward=0.0,
                                       metadata=dict(self.core._info(), action_check=check))
        observation, value, terminated, truncated, info = self.core.step(raw)
        return RegionalObservation(data=observation, reward=value, done=terminated or truncated,
                                   terminated=terminated, truncated=truncated, metadata=info)

    @property
    def state(self):
        if self.core.sim is None:
            return RegionalState()
        return RegionalState(episode_id=self.core.sim.incident_id, step_count=self.core.decisions,
                             minute=self.core.sim.tick, terminated=self.core.terminated, truncated=self.core.truncated)

    def list_splits(self):
        return ['train','validation','test']

    def list_tasks(self, split):
        if split not in self.list_splits():
            raise ValueError('Unknown split')
        return [dict(id=task['id'],split=split) for task in generate_tasks() if task['split']==split]

    def num_tasks(self, split):
        return len(self.list_tasks(split))

    def get_task(self, split, index):
        tasks=self.list_tasks(split)
        if type(index) is not int or not 0 <= index < len(tasks):
            raise ValueError('Unknown task index')
        return tasks[index]

    def get_task_range(self, split, start=None, stop=None):
        return self.list_tasks(split)[start:stop]
