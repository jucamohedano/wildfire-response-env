"""Expanded tasks on the unchanged public benchmark engine and reward."""
import copy
import hashlib
import math
from pathlib import Path

from ..benchmark_v1.calibration import apply_profile
from ..benchmark_v1.environment import BenchmarkEnv, implementation_digest as engine_digest
from ..benchmark_v1.outcomes import recompute_outcomes, verify_outcomes
from ..ops.generator import digest
from .catalog import build_catalog, get_task


def implementation_digest():
    directory = Path(__file__).parent
    return digest(dict(engine=engine_digest(), expansion={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob('*.py'))}))


class ExpandedEnv(BenchmarkEnv):
    def _task_manifest(self):
        canonical = build_catalog(self._inputs, self._domain)
        if self._catalog is not None and self._catalog != canonical:
            raise ValueError('Expansion catalog differs from canonical compiler and inputs')
        if self._expected_catalog_sha256 is not None and self._expected_catalog_sha256 != canonical['sha256']:
            raise ValueError('Expansion catalog pin mismatch')
        self._catalog = canonical
        self._catalog_sha256 = canonical['sha256']
        return copy.deepcopy(canonical)

    def _resolve_task(self, task_id):
        task = get_task(self._catalog, self._active_inputs, task_id)
        task['scenario'] = apply_profile(task['scenario'], self._profile)
        task['scenario']['minimum_ticks'] = 120
        return task

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['exercise_mode'] = 'wildfire_synthetic_expansion_v1'
        return public

    def reset(self, task_id=None, seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory['expansion_implementation_sha256'] = implementation_digest()
        return observation, info

    def verify_episode(self):
        self._require_finished()
        recorded = self.trajectory()
        replay = ExpandedEnv(self._inputs, self._domain,
            expected_prepared_sha256=self._inputs['sha256'], expected_topographic_sha256=self._domain['sha256'],
            expected_catalog_sha256=self._catalog_sha256, slope_enabled=self._slope_enabled,
            profile=self._profile, max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)
        replay.reset(recorded['task_id'], recorded['seed'])
        if ({key: value for key, value in recorded.items() if key != 'transitions'}
                != {key: value for key, value in replay.trajectory().items() if key != 'transitions'}):
            raise ValueError('Expanded trajectory header differs from replay')
        for index, transition in enumerate(recorded['transitions']):
            replay.step(transition['action'])
            if digest(transition) != digest(replay._ops_trajectory['transitions'][-1]):
                raise ValueError(f'Expanded transition {index} differs from replay')
        replay._require_finished()
        audit = replay.private_audit()
        if digest(audit) != digest(self.private_audit()):
            raise ValueError('Expanded private audit differs from replay')
        outcomes = recompute_outcomes(audit['records'], terminal=True,
            abandoned=replay._core.invalid_streak >= 3, boundary_censored=replay._core.boundary_censored)
        verify_outcomes(self.outcomes(), outcomes)
        if not math.isclose(self.reward, outcomes['graded_score'], rel_tol=0, abs_tol=1e-12):
            raise ValueError('Expanded reward differs from independent outcomes')
        return dict(passed=True, core_replayed=True, outcomes=outcomes)
