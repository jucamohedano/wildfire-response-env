"""Trusted opt-in containment episode with a separately pinned scoring domain.

The Python environment, checkpoints and verification methods are evaluator-only.
Use the containment agent tool allowlist as the actor interface.
"""
import copy
import hashlib
from pathlib import Path

from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_containment_v1.incident_day import (
    CORE, ContainmentIncidentDayEnv, _source_digest,
)
from wildfire_containment_v1.transcript import PUBLIC_CALLS, json_equal, transcribed
from wildfire_containment_v1.kernel import ContainmentKernel, _number

from .scoring import REWARD_VERSION, mask_sha256, recompute
from .verifier import _outcomes_match, _result, verify_outcome

INTERFACE_VERSION = 'wildfire-containment-integrated-scoring-v2'
CHECKPOINT_VERSION = 'wildfire-containment-integrated-checkpoint-v2'


def _implementation():
    directory = Path(__file__).parent
    files = ('integrated.py', 'scoring.py', 'verifier.py')
    return digest(dict(core=implementation_digest(), containment=_source_digest(),
                       scoring={name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                for name in files}))


def configuration_sha256(containment_config, pressure_scale):
    """Hash normalized containment physics settings from trusted configuration."""
    config = ContainmentKernel(**containment_config).config
    scale = _number(pressure_scale, 'pressure_scale')
    if scale <= 0 or config['wear_factor'] <= 0 or config['wind_factor'] <= 0:
        raise ValueError('Positive pressure, wind and wear factors are required')
    return digest(dict(version='wildfire-containment-scoring-configuration-v1',
                       containment_config=config, pressure_scale=scale))


def scoring_implementation_sha256(source_sha256, configuration_pin):
    """Bind the legacy verifier's implementation field to source and physics."""
    return digest(dict(version='wildfire-containment-scoring-identity-v1',
                       source_sha256=source_sha256, configuration_sha256=configuration_pin))


def _matches_configuration(env, expected):
    if (type(expected) is not str or len(expected) != 64
            or any(char not in '0123456789abcdef' for char in expected)):
        return False
    actual = configuration_sha256(env._config, env._scale)
    return (actual == expected and actual == env._configuration_pin
            and env.trajectory().get('configuration_sha256') == expected)


def verify_integrated_outcome(env, *, expected_identity, scoring_mask,
                              expected_configuration_sha256, infrastructure_timeout=False):
    """Verify a non-weather scorer with an independently trusted physics pin."""
    if type(infrastructure_timeout) is not bool:
        raise TypeError('infrastructure_timeout must be a boolean')
    if infrastructure_timeout:
        return verify_outcome(env, expected_identity=expected_identity, scoring_mask=scoring_mask,
                              infrastructure_timeout=True)
    try:
        if type(env) is not IntegratedContainmentEnv:
            return _result('verification_failed')
        if not _matches_configuration(env, expected_configuration_sha256):
            return _result('identity_mismatch')
        result = verify_outcome(env, expected_identity=expected_identity, scoring_mask=scoring_mask)
        if not _matches_configuration(env, expected_configuration_sha256):
            return _result('identity_mismatch')
        return result
    except Exception:
        return _result('verification_failed')


class IntegratedContainmentEnv(ContainmentIncidentDayEnv):
    """Containment with a fixed, caller-supplied, handover-burnable mask.

    A trusted driver supplies the mask and separately pins the returned identity;
    it must not derive the mask or expected identity from the episode being graded.
    """
    def __init__(self, *args, scoring_mask, **kwargs):
        self._mask_hash = mask_sha256(scoring_mask)
        self._mask = frozenset(scoring_mask)
        self._integrated_source = _implementation()
        super().__init__(*args, **kwargs)
        self._configuration_pin = configuration_sha256(self._config, self._scale)

    def _check_integrated_source(self):
        if self._integrated_source != _implementation() or implementation_digest() != CORE:
            raise RuntimeError('Integrated implementation changed')

    def _check_public_runtime(self):
        super()._check_public_runtime()
        self._check_integrated_source()

    def reset(self, scenario_id=None, seed=None):
        self._check_integrated_source()
        try:
            result = super().reset(scenario_id, seed)
            original = self._counter
            # Inherited footprint is not necessarily burnable: the audit is the
            # authoritative list of initial fuel cells for this validation.
            burnable = set(self._audit[0]['burnable'])
            if not self._mask <= burnable:
                raise ValueError('Scoring mask includes a non-burnable cell')
            self._counter = OutcomeCounter(original.inherited, self._mask, self.ops.groups,
                                           original.cell_area)
            return result
        except BaseException:
            self._transcript_valid = False
            raise

    def outcomes(self):
        """Return evaluator-only diagnostics without recording an actor call."""
        self._check_public_runtime()
        if not (self.terminated or self.truncated):
            raise RuntimeError('Outcomes are available only after the episode ends')
        result = self._counter.result(self.ops.groups, self.minute, self.terminated,
                                      self.abandoned, self.censored, self.ops.orders)
        result['reward_version'] = REWARD_VERSION
        return result

    def _info(self, error, wake):
        info = super()._info(error, wake)
        info['interface_version'] = INTERFACE_VERSION
        info.pop('outcomes', None)  # Evaluator-only; retrieve through outcomes().
        return info

    @transcribed
    def truncate(self):
        """Trusted host termination for non-timeout interruptions; no simulated tick."""
        if self._scenario is None or self.terminated or self.truncated:
            self._reject_public_validation(RuntimeError('A live episode is required'))
        self.truncated = True

    def public_transcript(self):
        return super().trajectory()

    def trajectory(self):
        """Evaluator-private identity for the extended verifier."""
        self._check_integrated_source()
        self.public_transcript()  # require a valid complete call history
        current_config = configuration_sha256(self._config, self._scale)
        if current_config != self._configuration_pin:
            raise RuntimeError('Containment configuration changed')
        return dict(interface_version=INTERFACE_VERSION, task_id=self._scenario['id'],
                    seed=self._scenario['seed'], inputs_sha256=self.inputs['sha256'],
                    network_sha256=self.network.sha256, scenario_sha256=self._scenario['sha256'],
                    implementation_sha256=scoring_implementation_sha256(self._integrated_source, current_config),
                    source_implementation_sha256=self._integrated_source,
                    configuration_sha256=current_config, scoring_mask_sha256=self._mask_hash)

    def checkpoint(self):
        self._check_integrated_source()
        value = dict(version=CHECKPOINT_VERSION, implementation_sha256=self._integrated_source,
                     scoring_mask=sorted(self._mask), scoring_mask_sha256=self._mask_hash,
                     containment=super().checkpoint())
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        self._check_integrated_source()
        value = copy.deepcopy(checkpoint)
        if (not isinstance(value, dict) or value.get('version') != CHECKPOINT_VERSION
                or value.get('implementation_sha256') != self._integrated_source
                or value.get('scoring_mask') != sorted(self._mask)
                or value.get('scoring_mask_sha256') != self._mask_hash
                or value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'})):
            raise ValueError('Integrated checkpoint identity or integrity differs')
        try:
            result = super().restore(value['containment'])
            if (not self._mask <= set(self._audit[0]['burnable'])
                    or self._counter.eligible != self._mask - self._counter.inherited
                    or self._counter.inherited != set(self._audit[0]['footprint'])
                    or not self._counter.additional <= self._counter.eligible):
                raise ValueError('Integrated checkpoint counter or mask differs')
            return result
        except BaseException:
            self._transcript_valid = False
            raise

    def verify_extended_episode(self):
        """Fresh exact replay and private audit for independent verifier accounting."""
        self._check_integrated_source()
        if type(self) is not IntegratedContainmentEnv or not (self.terminated or self.truncated):
            raise RuntimeError('Verification requires a finished exact integrated adapter')
        transcript = self.public_transcript()
        replay = IntegratedContainmentEnv(self.inputs, self.network.document,
            expected_inputs_sha256=self.inputs['sha256'], expected_network_sha256=self.network.sha256,
            allow_unapproved_data=self.inputs['approval_status'] != 'approved_synthetic_fixture',
            containment_config=self._config, pressure_scale=self._scale, scoring_mask=self._mask)
        reset = transcript['reset']
        replay.reset(*reset['arguments']['args'], **reset['arguments']['kwargs'])
        if not json_equal(replay._transcript_reset, reset) or replay.trajectory() != self.trajectory():
            raise ValueError('Reset or identity differs')
        for entry in transcript['calls']:
            if entry.get('operation') not in PUBLIC_CALLS | {'truncate'}:
                raise ValueError('Unsupported public transcript operation')
            count = len(replay._transcript)
            try:
                args = entry['arguments']
                getattr(replay, entry['operation'])(*args['args'], **args['kwargs'])
            except Exception:
                pass
            if len(replay._transcript) != count + 1 or not json_equal(replay._transcript[-1], entry):
                raise ValueError('Public call or result differs')
        if (not json_equal(replay.public_transcript(), transcript)
                or digest(replay.checkpoint()) != digest(self.checkpoint())):
            raise ValueError('Private checkpoint or transcript differs')
        # Read production state without recording a public transcript entry.
        outcomes = replay._counter.result(replay.ops.groups, replay.minute, replay.terminated,
                                          replay.abandoned, replay.censored, replay.ops.orders)
        outcomes['reward_version'] = REWARD_VERSION
        return dict(passed=True, exact_replay=True, audit=copy.deepcopy(replay._audit),
                    outcomes=outcomes)

    def verify_episode(self):
        """Runner-facing consistency result; external trusted pins still required."""
        failed = dict(passed=False, exact_replay=False, independent_reward_agreement=False,
                      outcomes={})
        try:
            before = self.checkpoint()
            verification = self.verify_extended_episode()
            independently = recompute(verification['audit'], scoring_mask=self._mask,
                terminal=self.terminated, abandoned=self.abandoned, censored=self.censored)
            agreement = (_outcomes_match(verification['outcomes'], independently)
                         and independently['completed_minutes'] == self.minute
                         and abs(self.reward - independently['graded_score']) <= 1e-12)
            if not agreement or not json_equal(before, self.checkpoint()):
                return failed
            return dict(passed=True, exact_replay=True, independent_reward_agreement=True,
                        outcomes=copy.deepcopy(verification['outcomes']))
        except Exception:
            return failed
