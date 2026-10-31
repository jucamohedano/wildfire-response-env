"""Fail-closed scoring gate for a future trusted extended-day adapter."""
import math
from collections.abc import Mapping
from .scoring import mask_sha256, recompute

VERIFIER_VERSION = 'wildfire-extended-incident-day-verifier-v1'
IDENTITY_FIELDS = frozenset(('interface_version', 'task_id', 'seed', 'inputs_sha256',
    'network_sha256', 'scenario_sha256', 'implementation_sha256', 'scoring_mask_sha256'))


def _result(status, verified=False, score=None):
    return dict(verifier_version=VERIFIER_VERSION, status=status, verified=verified,
                score_eligible=score is not None, score=score)


def _matches(actual, expected):
    if not isinstance(actual, Mapping):
        return False
    return all(type(actual.get(key)) is type(value) and actual.get(key) == value
               for key, value in expected.items())


def _outcomes_match(actual, expected):
    if not isinstance(actual, Mapping):
        return False
    for key, value in expected.items():
        other = actual.get(key)
        if type(value) is float:
            if (type(other) not in (int, float) or not math.isfinite(other)
                    or not math.isclose(other, value, rel_tol=1e-12, abs_tol=1e-12)):
                return False
        elif type(other) is not type(value) or other != value:
            return False
    return True


def verify_outcome(env, *, expected_identity, scoring_mask, infrastructure_timeout=False):
    """Verify through a trusted adapter; return only a bounded public gate result.

    The adapter's verify_extended_episode() must perform exact replay and return
    {'passed': True, 'exact_replay': True, 'audit': ..., 'outcomes': ...}.
    Its trajectory and state must remain stable during verification.
    """
    if type(infrastructure_timeout) is not bool:
        raise TypeError('infrastructure_timeout must be a boolean')
    if infrastructure_timeout:
        return _result('timeout')
    try:
        state = (env.terminated, env.truncated, env.minute, env.abandoned, env.censored, env.reward)
        terminated, truncated, minute, abandoned, censored, reward = state
        if (any(type(flag) is not bool for flag in (terminated, truncated, abandoned, censored))
                or terminated and truncated or type(minute) is not int or not 0 <= minute <= 1440
                or type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1):
            return _result('verification_failed')
        if not terminated and not truncated:
            return _result('incomplete')
        if not isinstance(expected_identity, Mapping) or set(expected_identity) != IDENTITY_FIELDS:
            return _result('identity_mismatch')
        expected = dict(expected_identity)
        mask = frozenset(scoring_mask)
        if mask_sha256(scoring_mask) != expected['scoring_mask_sha256']:
            return _result('identity_mismatch')
        if not _matches(env.trajectory(), expected):
            return _result('identity_mismatch')
        verification = env.verify_extended_episode()
        if (not isinstance(verification, Mapping) or verification.get('passed') is not True
                or verification.get('exact_replay') is not True):
            return _result('verification_failed')
        if not _matches(env.trajectory(), expected):
            return _result('identity_mismatch')
        after = (env.terminated, env.truncated, env.minute, env.abandoned, env.censored, env.reward)
        if any(type(a) is not type(b) or a != b for a, b in zip(state, after)):
            return _result('verification_failed')
        independent = recompute(verification['audit'], scoring_mask=mask, terminal=terminated,
                                abandoned=abandoned, censored=censored)
        if (independent['completed_minutes'] != minute
                or not _outcomes_match(verification.get('outcomes'), independent)
                or not math.isclose(reward, independent['graded_score'], rel_tol=1e-12, abs_tol=1e-12)):
            return _result('verification_failed')
        if censored:
            return _result('censored', verified=True) if truncated else _result('verification_failed')
        if abandoned:
            return _result('invalid_abandonment', verified=True, score=0.) if truncated else _result('verification_failed')
        if terminated:
            if minute != 1440 or not independent['score_eligible']:
                return _result('verification_failed')
            return _result('full_day', verified=True, score=float(independent['graded_score']))
        return _result('truncated', verified=True)
    except Exception:
        # Audit, exception text and adapter internals are evaluator-private.
        return _result('verification_failed')
