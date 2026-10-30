import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_containment_score_v1/src'))
from wildfire_containment_score_v1 import (IDENTITY_FIELDS, REWARD_VERSION, mask_sha256,
                                           recompute, verify_outcome)


def audit(minutes=1440):
    records = []
    for minute in range(minutes + 1):
        record = dict(minute=minute, new_footprint=[2, 5] if minute == 1 else [],
            population={'a': dict(count=10, exposed=2 if minute == 0 else 5,
                                  status='safe', protection_pending=False),
                        'b': dict(count=10, exposed=0, status='safe', protection_pending=False)})
        if minute == 0:
            record.update(footprint=[0, 1], burnable=[0, 1, 2, 3, 4, 5], cell_area_hectares=25.)
        records.append(record)
    return records


def score(records, mask=(0, 1, 2, 3), **flags):
    return recompute(records, scoring_mask=mask, terminal=flags.get('terminal', True),
                     abandoned=flags.get('abandoned', False), censored=flags.get('censored', False))


def test_fixed_mask_inherited_damage_and_80_20():
    result = score(audit())
    assert result['reward_version'] == REWARD_VERSION
    assert result['inherited_burned_hectares'] == 50
    assert result['additional_burned_hectares'] == 25
    assert result['inherited_exposed_population'] == 2
    assert result['new_exposed_population'] == 3
    assert result['graded_score'] == pytest.approx(.8 * (15 / 18) + .2 * .5)
    assert not result['safe_success']
    assert mask_sha256([3, 1, 0, 2]) == mask_sha256([0, 1, 2, 3])
    assert mask_sha256([1, 2]) != mask_sha256([1, 3])


def test_parity_with_v2_when_mask_is_all_burnable():
    path = ROOT / 'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/outcomes.py'
    spec = importlib.util.spec_from_file_location('old_outcomes', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = audit()
    expected = module.recompute(records, terminal=True, abandoned=False, censored=False)
    actual = score(records, (0, 1, 2, 3, 4, 5))
    expected['reward_version'] = REWARD_VERSION
    assert actual == expected


def test_empty_denominators_and_pending_orders():
    records = audit()
    for record in records:
        record['population'] = {'a': dict(count=2, exposed=2, status='safe', protection_pending=False)}
    records[-1]['population']['a']['protection_pending'] = True
    result = score(records, mask=(0, 1))
    assert result['graded_score'] == 1
    assert result['unresolved_evacuations'] == 1
    assert not result['safe_success']


@pytest.mark.parametrize('mutation', [
    lambda a: a[1].update(minute=2),
    lambda a: a[1]['population']['a'].update(count=11),
    lambda a: a[1]['population']['a'].update(exposed=1),
    lambda a: a[1]['population']['a'].update(exposed=True),
    lambda a: a[1]['population'].pop('a'),
    lambda a: a[1].update(new_footprint=[0]),
    lambda a: a[1].update(new_footprint=[2, 2]),
    lambda a: a[0].update(cell_area_hectares=float('nan')),
    lambda a: a[0].update(new_footprint=[1]),
])
def test_bad_audit_rejected(mutation):
    records = audit(2)
    mutation(records)
    with pytest.raises((ValueError, KeyError)):
        score(records, terminal=False)


def test_invalid_masks_rejected():
    for mask in ([2, 2], [True], [-1], [99]):
        with pytest.raises(ValueError):
            score(audit(1), mask=mask, terminal=False)


class Adapter:
    def __init__(self, *, minute=1440, terminated=True, truncated=False, abandoned=False, censored=False):
        self.terminated, self.truncated, self.minute = terminated, truncated, minute
        self.abandoned, self.censored = abandoned, censored
        self.mask = [0, 1, 2, 3]
        self.records = audit(minute)
        self.outcomes = score(self.records, self.mask, terminal=terminated, abandoned=abandoned, censored=censored)
        self.reward = self.outcomes['graded_score']
        self.identity = dict(interface_version='extended-v1', task_id='x', seed=1,
            inputs_sha256='a', network_sha256='b', scenario_sha256='c', implementation_sha256='d',
            scoring_mask_sha256=mask_sha256(self.mask))
        self.calls = 0

    def trajectory(self):
        return dict(self.identity)

    def verify_extended_episode(self):
        self.calls += 1
        return dict(passed=True, exact_replay=True, audit=self.records, outcomes=self.outcomes,
                    private='SECRET')


def verify(env, **kwargs):
    return verify_outcome(env, expected_identity=dict(env.identity), scoring_mask=env.mask, **kwargs)


@pytest.mark.parametrize('args,status,value', [
    ({}, 'full_day', True),
    (dict(minute=3, terminated=False, truncated=True, abandoned=True), 'invalid_abandonment', 0.),
    (dict(minute=3, terminated=False, truncated=True, censored=True), 'censored', None),
    (dict(minute=3, terminated=False, truncated=True, censored=True, abandoned=True), 'censored', None),
    (dict(minute=3, terminated=False, truncated=True), 'truncated', None),
    (dict(minute=3, terminated=False), 'incomplete', None),
])
def test_gate_statuses(args, status, value):
    env = Adapter(**args)
    result = verify(env)
    assert result['status'] == status
    assert result['score'] == (env.reward if value is True else value)
    assert result['score_eligible'] == (result['score'] is not None)
    assert 'SECRET' not in json.dumps(result)
    assert result['verified'] == (status != 'incomplete')


def test_timeout_precedes_completion_and_does_not_replay():
    env = Adapter()
    assert verify(env, infrastructure_timeout=True)['status'] == 'timeout'
    assert env.calls == 0
    with pytest.raises(TypeError):
        verify(env, infrastructure_timeout=1)


@pytest.mark.parametrize('change', [
    lambda e: e.outcomes.update(graded_score=0.99),
    lambda e: e.outcomes.update(reward_version='wildfire-incident-day-outcomes-v2'),
    lambda e: e.outcomes.update(score_eligible=1),
    lambda e: e.outcomes.update(new_exposed_population=0),
    lambda e: setattr(e, 'reward', True),
    lambda e: setattr(e, 'reward', float('nan')),
    lambda e: e.records[5].update(minute=6),
])
def test_verification_fails_closed_on_tampering(change):
    env = Adapter()
    change(env)
    result = verify(env)
    assert result['status'] == 'verification_failed'
    assert result['score'] is None


def test_pinned_identity_mask_and_replay_checks():
    env = Adapter()
    expected = dict(env.identity)
    assert set(expected) == IDENTITY_FIELDS
    expected['inputs_sha256'] = 'other'
    assert verify_outcome(env, expected_identity=expected, scoring_mask=env.mask)['status'] == 'identity_mismatch'
    assert env.calls == 0
    assert verify_outcome(env, expected_identity=env.identity, scoring_mask=[0, 1])['status'] == 'identity_mismatch'
    assert env.calls == 0
    def bad_replay():
        env.identity['scenario_sha256'] = 'changed'
        return dict(passed=True, exact_replay=True, audit=env.records, outcomes=env.outcomes)
    env.verify_extended_episode = bad_replay
    assert verify(env)['status'] == 'identity_mismatch'
    env = Adapter()
    env.verify_extended_episode = lambda: dict(passed=True, exact_replay=False)
    assert verify(env)['status'] == 'verification_failed'


def test_private_errors_do_not_escape():
    env = Adapter()
    def fail():
        raise ValueError('SECRET')
    env.verify_extended_episode = fail
    result = verify(env)
    assert result['status'] == 'verification_failed'
    assert 'SECRET' not in json.dumps(result)


def test_deterministic_recomputation_does_not_mutate_inputs():
    records = audit()
    mask = [3, 2, 1, 0]
    before = copy.deepcopy(records), list(mask)
    first = score(records, mask)
    second = score(records, list(reversed(mask)))
    assert first == second
    assert (records, mask) == before


def test_gate_rejects_short_terminal_and_mutation_during_replay():
    env = Adapter(minute=3)
    assert verify(env)['status'] == 'verification_failed'
    env = Adapter()
    def mutate():
        env.minute = 100
        return dict(passed=True, exact_replay=True, audit=env.records, outcomes=env.outcomes)
    env.verify_extended_episode = mutate
    assert verify(env)['status'] == 'verification_failed'
