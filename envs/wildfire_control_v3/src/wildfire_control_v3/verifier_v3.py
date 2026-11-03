"""Final, transport-independent gate for IncidentDayEnv outcomes.

Call only on a trusted environment instance after the driver stops. A timeout
is a driver observation, not an environment action or a simulated penalty.
The result deliberately contains no replay, audit, exception text or inputs.
"""

import math
from collections.abc import Mapping
from . import _capture_source

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)

VERSION = 'wildfire-incident-day-verifier-v3'
REWARD_VERSION = 'wildfire-incident-day-outcomes-v2'
IDENTITY_FIELDS = frozenset(('interface_version', 'task_id', 'seed', 'inputs_sha256',
                             'network_sha256', 'scenario_sha256', 'implementation_sha256'))


def _result(status, *, verified=False, score=None):
    return dict(verifier_version=VERSION, status=status, verified=verified,
                score_eligible=score is not None, score=score)


def _score(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= 1)


def verify_outcome(env, *, expected_identity, infrastructure_timeout=False):
    """Verify a stopped day; return a bounded, JSON-compatible gate result.

    Status is full_day, invalid_abandonment, censored, truncated, timeout,
    incomplete, or verification_failed. Only the first two carry a score.
    expected_identity must be supplied by the trusted evaluation configuration,
    not inferred from the run being graded. A driver timeout is always unscored, even if the environment happens to
    terminate during an in-flight action; the caller must report it accurately.
    An unfinished environment cannot be replay-verified by the existing API.
    """
    if type(infrastructure_timeout) is not bool:
        raise TypeError('infrastructure_timeout must be a boolean')
    if infrastructure_timeout:
        return _result('timeout')
    try:
        terminated, truncated = env.terminated, env.truncated
        if type(terminated) is not bool or type(truncated) is not bool or (terminated and truncated):
            return _result('verification_failed')
        if not terminated and not truncated:
            return _result('incomplete')

        if not isinstance(expected_identity, Mapping) or set(expected_identity) != IDENTITY_FIELDS:
            return _result('identity_mismatch')
        trajectory = env.trajectory()
        if not isinstance(trajectory, Mapping) or any(
                type(trajectory.get(key)) is not type(expected_identity[key])
                or trajectory.get(key) != expected_identity[key] for key in IDENTITY_FIELDS):
            return _result('identity_mismatch')

        verification = env.verify_episode()
        if (not isinstance(verification, Mapping)
                or verification.get('passed') is not True
                or verification.get('exact_replay') is not True
                or verification.get('independent_reward_agreement') is not True):
            return _result('verification_failed')
        after = env.trajectory()
        if not isinstance(after, Mapping) or any(
                type(after.get(key)) is not type(expected_identity[key])
                or after.get(key) != expected_identity[key] for key in IDENTITY_FIELDS):
            return _result('identity_mismatch')
        outcome = verification.get('outcomes')
        if not isinstance(outcome, Mapping):
            return _result('verification_failed')
        eligible = outcome.get('score_eligible')
        censored = outcome.get('boundary_censored')
        abandoned = outcome.get('invalid_abandonment')
        minute = outcome.get('completed_minutes')
        score = outcome.get('graded_score')
        if (any(type(flag) is not bool for flag in (eligible, censored, abandoned))
                or type(minute) is not int or not 0 <= minute <= 1440
                or outcome.get('reward_version') != REWARD_VERSION
                or not _score(score) or not _score(env.reward)
                or not math.isclose(score, env.reward, rel_tol=1e-12, abs_tol=1e-12)
                or type(env.minute) is not int or env.minute != minute
                or type(env.censored) is not bool or env.censored != censored
                or type(env.abandoned) is not bool or env.abandoned != abandoned):
            return _result('verification_failed')

        # The v2 counter can mark simultaneous abandonment and censoring eligible;
        # censoring always takes precedence at this external scoring boundary.
        if censored:
            if not truncated or score != 0 or eligible != abandoned:
                return _result('verification_failed')
            return _result('censored', verified=True)
        if abandoned:
            if not truncated or not eligible or score != 0:
                return _result('verification_failed')
            return _result('invalid_abandonment', verified=True, score=0.0)
        if terminated:
            if minute != 1440 or not eligible:
                return _result('verification_failed')
            return _result('full_day', verified=True, score=float(score))
        if eligible or score != 0:
            return _result('verification_failed')
        return _result('truncated', verified=True)
    except Exception:
        # Errors can contain private evaluator data. Never forward their text.
        return _result('verification_failed')
