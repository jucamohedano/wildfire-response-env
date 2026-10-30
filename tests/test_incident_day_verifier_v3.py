import copy
import json
import unittest

from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS, REWARD_VERSION, VERSION, verify_outcome
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


class StubEnv:
    def __init__(self, *, terminated=True, truncated=False, minute=1440,
                 censored=False, abandoned=False, eligible=True, score=0.7):
        self.terminated, self.truncated = terminated, truncated
        self.minute, self.censored, self.abandoned, self.reward = minute, censored, abandoned, score
        self.calls = 0
        self.verification = dict(passed=True, exact_replay=True, independent_reward_agreement=True,
                                 outcomes=dict(reward_version=REWARD_VERSION, graded_score=score,
                                               completed_minutes=minute, boundary_censored=censored,
                                               invalid_abandonment=abandoned, score_eligible=eligible,
                                               private_audit='SECRET'))

    def trajectory(self):
        return {'interface_version':'wildfire-incident-day-v2', 'task_id':'fixture', 'seed':1,
                'inputs_sha256':'a'*64, 'network_sha256':'b'*64, 'scenario_sha256':'c'*64,
                'implementation_sha256':'d'*64}

    def verify_episode(self):
        self.calls += 1
        return self.verification


def verify(env, **kwargs):
    expected = {key: env.trajectory()[key] for key in IDENTITY_FIELDS}
    return verify_outcome(env, expected_identity=expected, **kwargs)


class VerifierV3Tests(unittest.TestCase):
    def test_categories_and_score_eligibility(self):
        cases = [
            (StubEnv(), 'full_day', 0.7),
            (StubEnv(terminated=False, truncated=True, minute=3, abandoned=True, score=0), 'invalid_abandonment', 0.0),
            (StubEnv(terminated=False, truncated=True, minute=3, censored=True, eligible=False, score=0), 'censored', None),
            (StubEnv(terminated=False, truncated=True, minute=3, censored=True, abandoned=True, score=0), 'censored', None),
            (StubEnv(terminated=False, truncated=True, minute=3, eligible=False, score=0), 'truncated', None),
        ]
        for env, status, score in cases:
            with self.subTest(status=status, censored=env.censored, abandoned=env.abandoned):
                result = verify(env)
                self.assertEqual(result, dict(verifier_version=VERSION, status=status, verified=True,
                                              score_eligible=score is not None, score=score))
                self.assertEqual(env.calls, 1)
                self.assertNotIn('SECRET', json.dumps(result))

    def test_unfinished_and_timeout_do_not_claim_verification(self):
        env = StubEnv(terminated=False, minute=5, eligible=False, score=0)
        self.assertEqual(verify(env)['status'], 'incomplete')
        self.assertEqual(verify(env, infrastructure_timeout=True)['status'], 'timeout')
        self.assertEqual(env.calls, 0)
        self.assertIsNone(verify(env)['score'])
        with self.assertRaises(TypeError):
            verify(env, infrastructure_timeout=1)
        # An in-flight action that terminates after the driver deadline is not scored.
        late = StubEnv()
        self.assertEqual(verify(late, infrastructure_timeout=True)['status'], 'timeout')
        self.assertIsNone(verify(late, infrastructure_timeout=True)['score'])
        self.assertEqual(late.calls, 0)

    def test_inconsistent_and_untrusted_verification_fails_closed(self):
        mutations = [
            lambda e: e.verification.update(passed=False),
            lambda e: e.verification.update(exact_replay=False),
            lambda e: e.verification.update(independent_reward_agreement=False),
            lambda e: e.verification.update(outcomes=None),
            lambda e: e.verification['outcomes'].update(score_eligible=False),
            lambda e: e.verification['outcomes'].update(score_eligible=1),
            lambda e: e.verification['outcomes'].update(reward_version='other'),
            lambda e: e.verification['outcomes'].update(graded_score=float('nan')),
            lambda e: e.verification['outcomes'].update(graded_score=2),
            lambda e: e.verification['outcomes'].update(graded_score=0.8),
            lambda e: e.verification['outcomes'].update(completed_minutes=1439),
            lambda e: e.verification['outcomes'].update(boundary_censored=True),
            lambda e: setattr(e, 'truncated', True),
            lambda e: setattr(e, 'reward', True),
        ]
        for mutate in mutations:
            env = StubEnv()
            mutate(env)
            with self.subTest(mutation=mutate):
                result = verify(env)
                self.assertEqual(result['status'], 'verification_failed')
                self.assertIsNone(result['score'])
                self.assertFalse(result['verified'])

    def test_exceptions_do_not_leak(self):
        env = StubEnv()
        def fail():
            raise ValueError('SECRET evaluator data')
        env.verify_episode = fail
        result = verify(env)
        self.assertEqual(result['status'], 'verification_failed')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_trusted_identity_must_match_before_replay_or_scoring(self):
        for key in IDENTITY_FIELDS:
            env = StubEnv()
            expected = env.trajectory()
            expected[key] = 'different'
            result = verify_outcome(env, expected_identity=expected)
            assert result['status'] == 'identity_mismatch'
            assert result['score'] is None and env.calls == 0
        env = StubEnv()
        assert verify_outcome(env, expected_identity={})['status'] == 'identity_mismatch'
        assert env.calls == 0

    def test_identity_change_during_replay_is_not_scored(self):
        env = StubEnv()
        expected = env.trajectory()
        verification = env.verification
        def change():
            env.trajectory = lambda: {**expected, 'scenario_sha256':'changed'}
            return verification
        env.verify_episode = change
        result = verify_outcome(env, expected_identity=expected)
        assert result['status'] == 'identity_mismatch' and result['score'] is None

    def test_real_abandonment_replay_and_reward_tampering(self):
        inputs, network = fixture_inputs()
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
        env.reset()
        for _ in range(3):
            env.step({'bad': 1})
        before = copy.deepcopy(env.checkpoint())
        result = verify(env)
        self.assertEqual(result['status'], 'invalid_abandonment')
        self.assertEqual(result['score'], 0.0)
        self.assertEqual(before, env.checkpoint())
        env.reward = 0.1
        self.assertEqual(verify(env)['status'], 'verification_failed')

    def test_real_completed_day(self):
        inputs, network = fixture_inputs()
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
        env.reset()
        while not env.terminated and not env.truncated:
            env.advance_time(60)
        result = verify(env)
        self.assertEqual(result['status'], 'full_day')
        self.assertEqual(result['score'], env.reward)
        self.assertTrue(result['verified'])


if __name__ == '__main__':
    unittest.main()
