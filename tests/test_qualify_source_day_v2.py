import json

import pytest

from eval.qualify_source_day_v2 import BlockedCase, qualify

IDS = [f'case-{i}' for i in range(12)]
FULL_SCOPE = 'full_source_environment'
FULL_REWARD = 'wildfire-full-source-outcomes-v1'


class Host:
    def __init__(self, mode='normal'):
        self.minute = 0
        self.mode = mode
        self.status = 'active'

    def situation(self):
        return {'minute': self.minute, 'status': self.status}

    def advance(self, minutes):
        self.minute += minutes
        if self.mode == 'censored' and self.minute == 60:
            self.status = 'censored'
        elif self.minute == 1440:
            self.status = 'end_unscored'


class Policy:
    def act(self, host, obs):
        with pytest.raises(AttributeError):
            host.checkpoint
        host.advance(60)


def gate(case_id, host):
    return {'status': 'full_day', 'scope': FULL_SCOPE, 'reward_version': FULL_REWARD,
            'verified': True, 'score_eligible': True, 'score': .75}


def run(path, **overrides):
    kwargs = dict(checkpoint=path, run_id='public-v1', expected_scope=FULL_SCOPE,
                  expected_reward_version=FULL_REWARD, make_environment=lambda _: Host(),
                  make_policy=lambda _: Policy(), verify_outcome=gate)
    kwargs.update(overrides)
    return qualify(IDS, **kwargs)


def test_verified_scores_and_resume_preserves_results(tmp_path):
    path = tmp_path / 'checkpoint.json'
    result = run(path)
    assert all(r['status'] == 'scored' and r['score'] == .75 for r in result['cases'])
    assert json.loads(path.read_text()) == result
    with pytest.raises(FileExistsError):
        run(path)
    again = run(path, resume=True, make_environment=lambda _: pytest.fail('reran completed case'))
    assert again == result
    with pytest.raises(ValueError):
        run(path, resume=True, run_id='different')


def test_distinct_outcomes_and_sanitized_errors(tmp_path):
    def factory(case):
        if case == IDS[0]:
            raise BlockedCase('private source configuration')
        if case == IDS[1]:
            raise RuntimeError('private audit data')
        return Host('censored' if case == IDS[2] else 'normal')

    def verify(case, host):
        if case == IDS[3]:
            return {'status': 'full_day', 'verified': False, 'score_eligible': True, 'score': .8, 'secret': 'hidden'}
        return gate(case, host)

    report = run(tmp_path / 'state', make_environment=factory, verify_outcome=verify)
    assert [(r['status'], r['reason']) for r in report['cases'][:4]] == [
        ('blocked', 'integration_blocked'), ('blocked', 'integration_error'),
        ('censored', 'boundary'), ('blocked', 'verification_failed')]
    assert all(r['score'] is None for r in report['cases'][:4])
    assert all(s not in json.dumps(report) for s in ('private', 'hidden', 'secret', 'audit'))


def test_interrupt_saves_progress_then_restarts_only_incomplete(tmp_path):
    path = tmp_path / 'state'
    class Interrupt:
        def act(self, host, obs):
            raise KeyboardInterrupt('private')
    report = run(path, make_policy=lambda case: Interrupt() if case == IDS[1] else Policy())
    assert report['cases'][0]['status'] == 'scored'
    assert report['cases'][1]['reason'] == 'interrupted'
    assert report['cases'][2]['reason'] == 'not_started'
    assert json.loads(path.read_text()) == report
    seen = []
    def factory(case):
        seen.append(case)
        return Host()
    result = run(path, resume=True, make_environment=factory)
    assert seen == IDS[1:]
    assert all(row['status'] == 'scored' for row in result['cases'])


def test_decision_and_wall_budgets_are_incomplete(tmp_path):
    class Stall:
        def act(self, host, obs):
            pass
    report = run(tmp_path / 'stall', make_policy=lambda _: Stall(), max_decisions=2)
    assert all(row['reason'] == 'decision_limit' and row['score'] is None for row in report['cases'])
    ticks = iter(range(1000))
    report = run(tmp_path / 'budget', wall_seconds=4, now=lambda: next(ticks))
    assert report['cases'][0]['reason'] == 'budget'
    assert report['cases'][1]['reason'] == 'not_started'


@pytest.mark.parametrize('bad', [float('nan'), True, -1, 1.1, None])
def test_bad_scores_are_never_accepted(tmp_path, bad):
    report = run(tmp_path / 'state', verify_outcome=lambda *_: dict(status='full_day', scope=FULL_SCOPE, reward_version=FULL_REWARD, verified=True, score_eligible=True, score=bad))
    assert all(row['status'] == 'blocked' and row['score'] is None for row in report['cases'])


def test_corrupt_checkpoint_and_invalid_inputs_fail_closed(tmp_path):
    path = tmp_path / 'state'
    report = run(path)
    report['cases'][0]['score'] = 10
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        run(path, resume=True)
    with pytest.raises(ValueError):
        run(tmp_path / 'bad', max_decisions=True)
    with pytest.raises(ValueError):
        qualify(IDS[:-1], checkpoint=tmp_path / 'bad', run_id='x', expected_scope=FULL_SCOPE, expected_reward_version=FULL_REWARD, make_environment=None, make_policy=None, verify_outcome=None)


def test_censored_at_handover_does_not_require_a_policy(tmp_path):
    def factory(_):
        host = Host()
        host.status = 'censored'
        return host
    report = run(tmp_path / 'state', make_environment=factory,
                 make_policy=lambda _: pytest.fail('policy is not needed'))
    assert all(row['status'] == 'censored' for row in report['cases'])


def test_verification_cannot_finish_after_budget(tmp_path):
    clock = [0]
    def verify(case, host):
        clock[0] = 10
        return gate(case, host)
    report = run(tmp_path / 'state', verify_outcome=verify, wall_seconds=5, now=lambda: clock[0])
    assert report['cases'][0]['status'] == 'incomplete'
    assert report['cases'][0]['reason'] == 'budget'
    assert report['cases'][0]['score'] is None


class StatefulHost(Host):
    def __init__(self):
        super().__init__()
        self.history = []

    def advance(self, minutes):
        self.history.append(minutes)
        super().advance(minutes)


class AlternatingPolicy:
    def __init__(self, counter=0, interrupt=False):
        self.counter = counter
        self.interrupt = interrupt

    def act(self, host, obs):
        if self.interrupt and self.counter == 3:
            # An in-flight decision can mutate both sides; it must roll back to
            # the last durable pair, not combine either side with newer state.
            host.advance(30)
            self.counter += 1
            raise KeyboardInterrupt('private')
        host.advance(30 if self.counter % 2 == 0 else 90)
        self.counter += 1


class AuthenticatedState:
    """Test-only MAC; production integrations must use their own trusted keys."""
    def __init__(self, key=b'test-key'):
        self.key = key
        self.restored = []

    def capture(self, host, policy):
        return json.dumps(dict(minute=host.minute, status=host.status,
                               history=host.history, counter=policy.counter)).encode()

    def seal(self, body):
        import hashlib
        import hmac
        return hmac.digest(self.key, body, hashlib.sha256) + body

    def open(self, sealed):
        import hashlib
        import hmac
        if not hmac.compare_digest(sealed[:32], hmac.digest(self.key, sealed[32:], hashlib.sha256)):
            raise ValueError('private authentication error')
        return sealed[32:]

    def restore(self, case_id, state):
        data = json.loads(state)
        host = StatefulHost()
        host.minute, host.status, host.history = data['minute'], data['status'], data['history']
        self.restored.append((case_id, data['counter'], data['minute']))
        return host, AlternatingPolicy(data['counter'])


def test_mid_case_interruption_restores_exact_host_and_policy(tmp_path):
    path = tmp_path / 'state'
    adapter = AuthenticatedState()
    first = run(path, make_environment=lambda _: StatefulHost(),
                make_policy=lambda _: AlternatingPolicy(interrupt=True), state_adapter=adapter)
    assert first['cases'][0]['reason'] == 'interrupted'
    assert first['cases'][0]['minute'] == 150
    assert 'continuations' not in first
    assert 'private' not in json.dumps(first)
    def verify(case, host):
        assert host.history == [30, 90] * 12
        return gate(case, host)
    result = run(path, resume=True, state_adapter=adapter, verify_outcome=verify,
                 make_environment=lambda _: StatefulHost(),
                 make_policy=lambda _: AlternatingPolicy())
    assert adapter.restored == [(IDS[0], 3, 150)]
    assert all(row['status'] == 'scored' for row in result['cases'])
    assert 'continuations' not in json.loads(path.read_text())


def test_continuations_survive_multiple_decision_limited_cases(tmp_path):
    path = tmp_path / 'state'
    adapter = AuthenticatedState()
    first = run(path, make_environment=lambda _: StatefulHost(),
                make_policy=lambda _: AlternatingPolicy(), state_adapter=adapter, max_decisions=3)
    assert all(row['reason'] == 'decision_limit' and row['minute'] == 150 for row in first['cases'])
    assert len(json.loads(path.read_text())['continuations']) == 12
    def verify(case, host):
        assert host.history == [30, 90] * 12
        return gate(case, host)
    result = run(path, resume=True, state_adapter=adapter, verify_outcome=verify,
                 make_environment=lambda _: pytest.fail('continuation was lost'),
                 make_policy=lambda _: pytest.fail('policy was restarted'))
    assert len(adapter.restored) == 12
    assert all(row['status'] == 'scored' for row in result['cases'])


def test_authentication_and_case_binding_fail_closed(tmp_path):
    path = tmp_path / 'state'
    adapter = AuthenticatedState()
    run(path, make_environment=lambda _: StatefulHost(), make_policy=lambda _: AlternatingPolicy(),
        state_adapter=adapter, max_decisions=1)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='adapter required'):
        run(path, resume=True)
    with pytest.raises(ValueError, match='authentication or restore'):
        run(path, resume=True, state_adapter=AuthenticatedState(b'wrong-key'))
    assert path.read_bytes() == original
    doc = json.loads(original)
    doc['continuations'][IDS[0]] = doc['continuations'][IDS[1]]
    path.write_text(json.dumps(doc))
    changed = path.read_bytes()
    with pytest.raises(ValueError, match='authentication or restore'):
        run(path, resume=True, state_adapter=adapter)
    assert path.read_bytes() == changed


def test_checkpoint_permissions_and_symlinks_rejected(tmp_path):
    import os
    path = tmp_path / 'state'
    run(path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private user-owned regular file'):
        run(path, resume=True)
    path.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(ValueError, match='private user-owned regular file'):
        run(link, resume=True)
    public_dir = tmp_path / 'public'
    public_dir.mkdir(mode=0o755)
    os.chmod(public_dir, 0o755)
    with pytest.raises(ValueError, match='directory must be private'):
        run(public_dir / 'state')


def test_capture_failure_keeps_last_durable_pair_and_hides_error(tmp_path):
    path = tmp_path / 'state'
    class BrokenCapture(AuthenticatedState):
        def capture(self, host, policy):
            if host.minute >= 150:
                raise RuntimeError('private host data')
            return super().capture(host, policy)
    with pytest.raises(ValueError, match='capture failed') as exc:
        run(path, make_environment=lambda _: StatefulHost(),
            make_policy=lambda _: AlternatingPolicy(), state_adapter=BrokenCapture())
    assert 'private' not in str(exc.value).replace('private continuation', '')
    saved = json.loads(path.read_text())
    assert saved['cases'][0]['minute'] == 120
    adapter = AuthenticatedState()
    result = run(path, resume=True, state_adapter=adapter,
                 make_environment=lambda _: StatefulHost(), make_policy=lambda _: AlternatingPolicy())
    assert adapter.restored[0] == (IDS[0], 2, 120)
    assert all(row['status'] == 'scored' for row in result['cases'])


def test_terminal_checkpoint_resumes_verification_after_budget(tmp_path):
    path = tmp_path / 'state'
    adapter = AuthenticatedState()
    clock = [0]
    def slow_verify(case, host):
        clock[0] = 10
        return gate(case, host)
    first = run(path, make_environment=lambda _: StatefulHost(),
                make_policy=lambda _: AlternatingPolicy(), state_adapter=adapter,
                verify_outcome=slow_verify, now=lambda: clock[0], wall_seconds=5)
    assert first['cases'][0]['reason'] == 'budget'
    assert first['cases'][0]['minute'] == 1440
    def verify(case, host):
        assert host.history == [30, 90] * 12
        return gate(case, host)
    resumed = run(path, resume=True, state_adapter=adapter, verify_outcome=verify,
                  make_environment=lambda _: StatefulHost(), make_policy=lambda _: AlternatingPolicy())
    assert adapter.restored[0] == (IDS[0], 24, 1440)
    assert all(row['status'] == 'scored' for row in resumed['cases'])


@pytest.mark.parametrize('metadata', [
    {},
    {'scope': FULL_SCOPE},
    {'reward_version': FULL_REWARD},
    {'scope': 'restricted_no_ground_no_evac', 'reward_version': 'wildfire-restricted-no-ground-no-evac-outcomes-v1'},
    {'scope': 'restricted_no_ground_no_evac', 'reward_version': FULL_REWARD},
    {'scope': FULL_SCOPE, 'reward_version': 'wildfire-restricted-no-ground-no-evac-outcomes-v1'},
    {'scope': 'other_full_environment', 'reward_version': FULL_REWARD},
    {'scope': FULL_SCOPE, 'reward_version': 'other-reward-v1'},
    {'scope': None, 'reward_version': FULL_REWARD},
])
def test_normalized_or_missing_verifier_metadata_never_scores(tmp_path, metadata):
    def verify(*_):
        return dict(status='full_day', verified=True, score_eligible=True, score=.75, **metadata)
    report = run(tmp_path / 'state', verify_outcome=verify)
    assert all(row['status'] == 'blocked' and row['reason'] == 'verification_failed'
               and row['score'] is None for row in report['cases'])


@pytest.mark.parametrize('argument,value', [
    ('expected_scope', 'restricted_no_ground_no_evac'),
    ('expected_scope', 'RESTRICTED-no-ground'),
    ('expected_reward_version', 'wildfire-restricted-no-ground-no-evac-outcomes-v1'),
    ('expected_scope', ''),
    ('expected_reward_version', None),
])
def test_invalid_or_restricted_expected_identity_rejected_before_creation(tmp_path, argument, value):
    path = tmp_path / 'state'
    with pytest.raises(ValueError, match='full-environment identities'):
        run(path, **{argument: value})
    assert not path.exists()


def test_resume_pins_must_match_and_old_checkpoint_is_preserved(tmp_path):
    path = tmp_path / 'state'
    first = run(path)
    assert first['expected_scope'] == FULL_SCOPE
    assert first['expected_reward_version'] == FULL_REWARD
    before = path.read_bytes()
    for kwargs in ({'expected_scope': 'different_full_scope'},
                   {'expected_reward_version': 'different_full_reward'}):
        with pytest.raises(ValueError, match='checkpoint identity'):
            run(path, resume=True, **kwargs)
        assert path.read_bytes() == before
    old = dict(first)
    old.pop('expected_scope')
    old.pop('expected_reward_version')
    old['version'] = 'wildfire-source-day-orchestration-v2'
    path.write_text(json.dumps(old))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='checkpoint identity'):
        run(path, resume=True)
    assert path.read_bytes() == before


def test_continuation_authenticates_scope_and_reward_pins(tmp_path):
    path = tmp_path / 'state'
    adapter = AuthenticatedState()
    run(path, make_environment=lambda _: StatefulHost(), make_policy=lambda _: AlternatingPolicy(),
        state_adapter=adapter, max_decisions=1)
    doc = json.loads(path.read_text())
    doc['expected_scope'] = 'different_full_scope'
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='authentication or restore'):
        run(path, resume=True, expected_scope='different_full_scope', state_adapter=adapter)
    assert path.read_bytes() == before


def test_expected_pins_are_required_and_unrestricted_is_not_restricted(tmp_path):
    with pytest.raises(TypeError):
        qualify(IDS, checkpoint=tmp_path / 'missing', run_id='test',
                make_environment=lambda _: Host(), make_policy=lambda _: Policy(), verify_outcome=gate)
    report = run(tmp_path / 'state', expected_scope='unrestricted_full_environment',
                 verify_outcome=lambda *_: dict(status='full_day', scope='unrestricted_full_environment',
                                               reward_version=FULL_REWARD, verified=True,
                                               score_eligible=True, score=.75))
    assert all(row['status'] == 'scored' for row in report['cases'])
