import pytest

from eval.qualify_extended_day_v1 import EXPECTED_REWARD, qualify


IDS = [f'extended-case-{i:03}' for i in range(12)]


class WaitPolicy:
    def action(self, env, observation):
        with pytest.raises(AttributeError):
            env.checkpoint
        return {'advance_minutes': 60}


class FakeEpisode:
    def __init__(self, *, censored=False, reward_version=EXPECTED_REWARD):
        self.minute, self.censored, self.reward_version = 0, censored, reward_version

    def reset(self, scenario_id):
        return {'minute': 0, 'reports': []}, {}

    def step(self, action):
        self.minute += action['advance_minutes']
        truncated = self.censored and self.minute == 120
        return {'minute': self.minute, 'reports': []}, 0., self.minute == 1440, truncated, {}

    def verify_episode(self):
        return {'passed': True, 'exact_replay': True, 'independent_reward_agreement': True,
                'outcomes': {'reward_version': self.reward_version,
                             'graded_score': .75, 'score_eligible': True}}


def test_twelve_completed_episodes_are_verified_and_scored():
    report = qualify(IDS, make_environment=lambda _: FakeEpisode(), make_policy=WaitPolicy)
    assert report['status'] == 'passed'
    assert len(report['cases']) == 12
    assert all(row['minute'] == 1440 and row['score'] == .75 for row in report['cases'])


def test_censored_and_old_reward_runs_remain_unscored():
    def factory(identifier):
        if identifier == IDS[0]:
            return FakeEpisode(censored=True)
        return FakeEpisode(reward_version='old') if identifier == IDS[1] else FakeEpisode()
    report = qualify(IDS, make_environment=factory, make_policy=WaitPolicy)
    assert report['status'] == 'incomplete'
    assert report['cases'][0]['status'] == report['cases'][1]['status'] == 'not_scored'
    assert report['cases'][0]['score'] is report['cases'][1]['score'] is None
    assert report['cases'][2]['status'] == 'passed'


def test_invalid_case_ids_and_budget_are_rejected():
    for ids in (IDS[:-1], [IDS[0]] * 12):
        with pytest.raises(ValueError):
            qualify(ids, make_environment=lambda _: FakeEpisode(), make_policy=WaitPolicy)
    with pytest.raises(ValueError):
        qualify(IDS, make_environment=lambda _: FakeEpisode(), make_policy=WaitPolicy, wall_seconds=0)


def test_future_report_or_engine_error_does_not_leak_and_is_unscored():
    class BadEpisode(FakeEpisode):
        def reset(self, scenario_id):
            return {'minute': 0, 'reports': [{'observed_at': 0, 'received_at': 1}]}, {}
    def factory(identifier):
        if identifier == IDS[0]:
            raise ValueError('private scenario annotation')
        return BadEpisode() if identifier == IDS[1] else FakeEpisode()
    report = qualify(IDS, make_environment=factory, make_policy=WaitPolicy)
    assert report['cases'][0]['reason'] == report['cases'][1]['reason'] == 'setup_or_validation_error'
    assert 'private scenario annotation' not in str(report)
    assert report['cases'][0]['score'] is report['cases'][1]['score'] is None


def test_budget_stop_never_produces_success():
    tick = iter(range(1000))
    report = qualify(IDS, make_environment=lambda _: FakeEpisode(), make_policy=WaitPolicy,
                     wall_seconds=5, now=lambda: next(tick))
    assert report['status'] == 'incomplete'
    assert report['cases'][0]['score'] is None
    assert report['reason'] == 'budget_or_interrupt'
