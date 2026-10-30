"""Trusted weather, containment and fixed-mask scoring integration.

The episode, pins, audit and checkpoints are evaluator-private. Actor access
must go through an allowlisted host interface, not the Python object.
"""
import copy
import hashlib
from pathlib import Path

from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_containment_v1.incident_day import CORE
from wildfire_containment_v1.transcript import PUBLIC_CALLS, json_equal, transcribed
from wildfire_containment_weather_v1.incident_day import (
    ContainmentWeatherIncidentDayEnv, _sources,
)

from .integrated import IntegratedContainmentEnv, _implementation, _matches_configuration
from .scoring import REWARD_VERSION
from .verifier import verify_outcome

INTERFACE_VERSION = 'wildfire-containment-weather-integrated-scoring-v2'
CHECKPOINT_VERSION = 'wildfire-containment-weather-integrated-checkpoint-v2'


def _weather_implementation():
    return digest(dict(integrated=_implementation(), weather=_sources(),
                       combined=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


class IntegratedWeatherContainmentEnv(IntegratedContainmentEnv, ContainmentWeatherIncidentDayEnv):
    """Fixed-mask scorer over the separately versioned weather host.

    The weather catalog and fixed mask are trusted constructor inputs. The
    independent external verifier must also check the weather catalog/source
    pins with verify_weather_outcome, in addition to the standard identity.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._integrated_source = _weather_implementation()

    def _check_integrated_source(self):
        if (self._integrated_source != _weather_implementation()
                or implementation_digest() != CORE):
            raise RuntimeError('Integrated weather implementation changed')

    def _check_public_runtime(self):
        # The weather host calls this at every simulated minute. Keep its
        # per-minute checks; hash the larger integrated/core source tree at
        # outer call boundaries and after a state-changing step instead.
        ContainmentWeatherIncidentDayEnv._check_public_runtime(self)
        if not self._transcript_depth:
            self._check_integrated_source()

    @transcribed
    def step(self, action):
        result = super().step(action)
        self._check_integrated_source()
        return result

    def _info(self, error, wake):
        result = super()._info(error, wake)
        result['interface_version'] = INTERFACE_VERSION
        return result

    def trajectory(self):
        identity = super().trajectory()
        identity.update(interface_version=INTERFACE_VERSION,
                        weather_catalog_sha256=self._catalog['sha256'],
                        weather_source_sha256=self._weather_source,
                        containment_source_sha256=self._source_pin,
                        core_source_sha256=CORE)
        return identity

    def checkpoint(self):
        value = dict(version=CHECKPOINT_VERSION, implementation_sha256=self._integrated_source,
                     catalog_sha256=self._catalog['sha256'], weather_source_sha256=self._weather_source,
                     integrated=super().checkpoint())
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        self._check_integrated_source()
        value = copy.deepcopy(checkpoint)
        if (type(value) is not dict or set(value) != {'version', 'implementation_sha256',
                'catalog_sha256', 'weather_source_sha256', 'integrated', 'sha256'}
                or value['version'] != CHECKPOINT_VERSION
                or value['implementation_sha256'] != self._integrated_source
                or value['catalog_sha256'] != self._catalog['sha256']
                or value['weather_source_sha256'] != self._weather_source
                or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
            raise ValueError('Integrated weather checkpoint identity or integrity differs')
        return super().restore(value['integrated'])

    def verify_extended_episode(self):
        """Replay this exact integrated class and compare its joint checkpoint."""
        self._check_integrated_source()
        if type(self) is not IntegratedWeatherContainmentEnv or not (self.terminated or self.truncated):
            raise RuntimeError('Verification requires a finished exact integrated weather adapter')
        transcript = self.public_transcript()
        replay = IntegratedWeatherContainmentEnv(self.inputs, self.network.document,
            expected_inputs_sha256=self.inputs['sha256'], expected_network_sha256=self.network.sha256,
            allow_unapproved_data=self.inputs['approval_status'] != 'approved_synthetic_fixture',
            containment_config=self._config, pressure_scale=self._scale, scoring_mask=self._mask,
            weather_catalog=self._catalog, expected_catalog_sha256=self._catalog['sha256'])
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
            raise ValueError('Private weather checkpoint or transcript differs')
        outcomes = replay._counter.result(replay.ops.groups, replay.minute, replay.terminated,
                                          replay.abandoned, replay.censored, replay.ops.orders)
        outcomes['reward_version'] = REWARD_VERSION
        return dict(passed=True, exact_replay=True, audit=copy.deepcopy(replay._audit),
                    outcomes=outcomes)


def verify_weather_outcome(env, *, expected_identity, scoring_mask,
                           expected_configuration_sha256, expected_catalog_sha256, expected_weather_source_sha256,
                           expected_containment_source_sha256, expected_core_source_sha256,
                           infrastructure_timeout=False):
    """Apply the normal score gate and independently trusted weather/source pins."""
    if type(infrastructure_timeout) is not bool:
        raise TypeError('infrastructure_timeout must be a boolean')
    if infrastructure_timeout:
        return verify_outcome(env, expected_identity=expected_identity, scoring_mask=scoring_mask,
                              infrastructure_timeout=True)
    from .verifier import _result
    pins = dict(weather_catalog_sha256=expected_catalog_sha256,
                weather_source_sha256=expected_weather_source_sha256,
                containment_source_sha256=expected_containment_source_sha256,
                core_source_sha256=expected_core_source_sha256)
    try:
        if type(env) is not IntegratedWeatherContainmentEnv:
            return _result('verification_failed')
        def matches():
            identity = env.trajectory()
            return _matches_configuration(env, expected_configuration_sha256) and all(
                       type(value) is str and len(value) == 64 and
                       all(c in '0123456789abcdef' for c in value) and identity.get(key) == value
                       for key, value in pins.items())
        if not matches():
            return _result('identity_mismatch')
        result = verify_outcome(env, expected_identity=expected_identity, scoring_mask=scoring_mask)
        if not matches():
            return _result('identity_mismatch')
        return result
    except Exception:
        return _result('verification_failed')
