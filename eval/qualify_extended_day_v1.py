"""Qualification runner for a *trusted* future extended-day environment.

This module does not build the map or bypass input guards. The factory must
independently verify all data, scenario, engine and reward pins before reset.
Results are development evidence, not a released or calibrated benchmark.
"""
import math
import resource
import time

VERSION = 'wildfire-extended-day-qualification-v1'
EXPECTED_REWARD = 'wildfire-extended-incident-day-outcomes-v1'
HORIZON = 1440


class _PublicFacade:
    """Narrow interface for trusted in-process scripted policies, not a sandbox."""
    def __init__(self, environment):
        self._environment = environment

    def __getattr__(self, name):
        if name not in ('get_situation', 'get_reports', 'route_options',
                        'evacuation_options', 'check_action', 'propose_hold'):
            raise AttributeError(name)
        return getattr(self._environment, name)


def _ids(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 12
            or any(type(identifier) is not str or not identifier for identifier in value)
            or len(set(value)) != 12):
        raise ValueError('Exactly twelve unique scenario IDs are required')
    return list(value)


def _chronology(observation):
    if type(observation) is not dict or type(observation.get('minute')) is not int:
        raise ValueError('Invalid public observation')
    minute = observation['minute']
    for report in observation.get('reports', []):
        observed, received = report.get('observed_at'), report.get('received_at')
        if (type(observed) is not int or type(received) is not int
                or not 0 <= observed <= received <= minute):
            raise ValueError('Public report chronology is invalid')
    return minute


def qualify(case_ids, *, make_environment, make_policy, verify_outcome, wall_seconds=1800, now=time.monotonic):
    """Run all cases with one CPU worker, without exposing private audit data.

    ``make_environment(id)`` is a trusted, independently pinned factory whose
    reset returns ``(observation, info)``. The policy is given only its public
    environment and observation. A completed case is scored only when the
    environment confirms exact replay and independent outcome agreement and
    the separately supplied trusted verifier checks externally pinned identity,
    mask, configuration and eligibility. Both must agree on the score. A budget stop,
    thrown exception or non-terminal result is never scored.
    """
    ids = _ids(case_ids)
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    start = now()
    report = {'version': VERSION, 'status': 'incomplete', 'cases': [],
              'reward_version': EXPECTED_REWARD, 'scope': 'synthetic development qualification',
              'worker_count': 1}
    for case_id in ids:
        if now() - start >= wall_seconds:
            break
        case_start = now()
        row = {'id': case_id, 'status': 'incomplete', 'minute': None,
               'score': None, 'exact_replay': False, 'independent_reward': False}
        try:
            env = make_environment(case_id)
            observation, _ = env.reset(case_id)
            minute = _chronology(observation)
            if minute != 0:
                raise ValueError('Episode must start at handover')
            policy = make_policy()
            terminated = truncated = False
            while not (terminated or truncated):
                if now() - start >= wall_seconds:
                    row['reason'] = 'budget'; break
                previous = minute
                action = policy.action(_PublicFacade(env), observation)
                observation, _, terminated, truncated, _ = env.step(action)
                if type(terminated) is not bool or type(truncated) is not bool or terminated and truncated:
                    raise ValueError('Invalid terminal flags')
                minute = _chronology(observation)
                if minute <= previous or minute > HORIZON:
                    raise ValueError('Committed decision did not advance a valid clock')
                row['minute'] = minute
            if (terminated or truncated) and now() - start >= wall_seconds:
                row['reason'] = 'budget'
            elif terminated or truncated:
                verification = env.verify_episode()
                gate = verify_outcome(env)
                row['exact_replay'] = verification.get('exact_replay') is True
                row['independent_reward'] = verification.get('independent_reward_agreement') is True
                outcome = verification.get('outcomes') or {}
                score = outcome.get('graded_score')
                valid = (terminated and not truncated and minute == HORIZON and
                         verification.get('passed') is True and row['exact_replay'] and
                         row['independent_reward'] and outcome.get('reward_version') == EXPECTED_REWARD and
                         outcome.get('score_eligible') is True and outcome.get('boundary_censored') is False and
                         outcome.get('invalid_abandonment') is False and outcome.get('completed_minutes') == HORIZON and
                         type(outcome.get('completed_minutes')) is int and type(score) in (int, float) and
                         math.isfinite(score) and 0 <= score <= 1 and
                         type(gate) is dict and gate.get('status') == 'full_day' and
                         gate.get('verified') is True and gate.get('score_eligible') is True and
                         type(gate.get('score')) in (int, float) and math.isfinite(gate['score']) and
                         math.isclose(gate['score'], score, rel_tol=1e-12, abs_tol=1e-12))
                if now() - start >= wall_seconds:
                    row['reason'] = 'budget'
                elif valid:
                    row.update(status='passed', score=score)
                else:
                    row.update(status='not_scored', reason='censored_truncated_or_unverified')
        except Exception:
            # Exceptions may contain evaluator-private inputs; do not publish them.
            row.update(status='not_scored', reason='setup_or_validation_error')
        row['elapsed_seconds'] = max(0, now() - case_start)
        report['cases'].append(row)
    report['elapsed_seconds'] = max(0, now() - start)
    report['peak_rss_kib_linux'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if len(report['cases']) == 12 and all(row['status'] == 'passed' for row in report['cases']):
        report['status'] = 'passed'
    elif len(report['cases']) < 12:
        report['reason'] = 'budget_or_interrupt'
    return report
