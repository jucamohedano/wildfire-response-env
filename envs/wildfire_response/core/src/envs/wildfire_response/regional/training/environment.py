"""Stateful TRL tools with independently verified full-horizon outcome rewards."""
import hashlib
import json
from pathlib import Path
import uuid

from ...eval.run import dump, source_digest
from ..benchmark import verify_lock
from ..data import PACK
from ..director.contract import public_context
from ..environment import RegionalResponseEnv, VERSION, REWARD_VERSION
from ..run import replay
from ..scenarios import get_scenario
from ..verifier import verify


class RegionalTrainingEnv:
    def __init__(self, audit_directory=None):
        self._audit_directory = Path(audit_directory) if audit_directory is not None else None
        self._core = None
        self._finished = False
        self.reward = 0.0
        self.model_submissions, self.model_checks = 0, 0

    def reset(self, scenario_id='regional-train-001', seed=None, **kwargs):
        task = get_scenario(scenario_id)
        if task.get('split') != 'train' or (seed is not None and seed != task['seed']):
            raise ValueError('Use a frozen training task and its assigned seed only')
        self._benchmark = verify_lock()
        self._core = RegionalResponseEnv()
        self._observation, info = self._core.reset(scenario_id, seed)
        self.reward, self._finished = 0.0, False
        self.model_submissions, self.model_checks = 0, 0
        self._last_error = None
        self._trajectory = dict(version=VERSION, reward_version=REWARD_VERSION, scenario_id=scenario_id,
            seed=self._core.seed, max_decisions=60, ticks_per_action=2, policy='trl_director',
            benchmark=self._benchmark, source_sha256=source_digest(),
            pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest(),
            initial_observation=self._observation, initial_info=info, transitions=[])
        return self._public()

    def _public(self):
        return json.dumps(dict(observation=public_context(self._observation),
            done=self._core.terminated or self._core.truncated, last_action_error=self._last_error,
            decisions_remaining=60-self._core.decisions,
            remaining_time_rule='If generation stops, standing missions persist and time advances with no new orders to the frozen horizon'),
            separators=(',', ':'), allow_nan=False)

    def _live(self):
        if self._core is None or self._finished or self._core.terminated or self._core.truncated:
            raise ValueError('Reset a live training episode before calling tools')

    def check_action(self, requests: list[dict[str, str]], authorizations: list[str],
                     releases: list[str], messages: list[dict[str, str]], explanation: str) -> str:
        """Check a director draft without advancing time or predicting outcomes.

        Args:
            requests: Resource requests; each has resource_id, sector_id and mission (recon or suppress).
            authorizations: Live offer IDs to authorize after receiving a base offer.
            releases: Assigned resource IDs to release and return to base.
            messages: Each has recipient and purpose; populations accept inform or evacuate, bases accept status.
            explanation: Short unscored explanation; use an empty string if unnecessary.

        Returns:
            Current contract validity and the remaining non-advancing check budget.
        """
        self._live()
        self.model_checks += 1
        return json.dumps(self._core.check_action(dict(requests=requests, authorizations=authorizations,
            releases=releases, messages=messages, explanation=explanation)))

    def submit_action(self, requests: list[dict[str, str]], authorizations: list[str],
                      releases: list[str], messages: list[dict[str, str]], explanation: str) -> str:
        """Issue director orders and advance two simulated minutes, including invalid orders.

        Args:
            requests: Resource requests; each has resource_id, sector_id and mission (recon or suppress).
            authorizations: Live offer IDs to authorize; a request alone never dispatches a resource.
            releases: Assigned resource IDs to return to their owning bases.
            messages: Each has recipient and purpose; populations accept inform or evacuate, bases accept status.
            explanation: Short unscored explanation; use an empty string if unnecessary.

        Returns:
            Public observations, delivery acknowledgements and whether the episode ended.
        """
        self._live()
        self.model_submissions += 1
        self._advance(dict(requests=requests, authorizations=authorizations, releases=releases,
                           messages=messages, explanation=explanation), 'model')
        return self._public()

    def _advance(self, action, origin):
        observation, reward, terminated, truncated, info = self._core.step(action)
        self._observation, self._last_error = observation, info['error']
        self.reward += reward
        self._trajectory['transitions'].append(dict(action=action, observation=observation, reward=reward,
            terminated=terminated, truncated=truncated, info=info, origin=origin))

    def _finish(self):
        if self._finished:
            return self.reward
        if self._core is None:
            raise ValueError('Reset required before scoring')
        while not self._core.terminated and not self._core.truncated:
            self._advance({}, 'passive_finalization')
        self._trajectory['total_reward'] = self.reward
        evaluator = self._core.evaluator_state()
        self._verification = verify(self._trajectory, evaluator)
        replay(self._trajectory)
        self._finished = True
        if self._audit_directory is not None:
            output = self._audit_directory/str(uuid.uuid4())
            output.mkdir(parents=True)
            (output/'private').mkdir()
            dump(output/'trajectory.json.gz', self._trajectory)
            dump(output/'private'/'evaluator.json.gz', evaluator)
            dump(output/'verification.json', self._verification)
            dump(output/'summary.json', dict(total_reward=self.reward, scenario=self._trajectory['scenario_id'],
                model_submissions=self.model_submissions, model_checks=self.model_checks,
                passive_finalization_decisions=sum(item['origin']=='passive_finalization' for item in self._trajectory['transitions']),
                components=evaluator['components'], terminated=self._core.terminated, truncated=self._core.truncated))
        return self.reward


def outcome_reward(environments, **kwargs):
    """Score every rollout at natural termination or the unchanged frozen horizon."""
    return [environment._finish() for environment in environments]
