"""Mock-only revalidation tests: no simulator, scripted policy, or full-day run."""
import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import revalidate_evaluation as rv
from eval.validate_source_runtime_matrix import REQUIRED_COVERAGE


class FakeEnv:
    def __init__(self):
        self.minute = self.decision = 0
        self.terminated = self.truncated = self.abandoned = self.censored = False
        self.inputs = {'sha256': 'input'}
        self.network = SimpleNamespace(sha256='network')
        self.bus = SimpleNamespace(reports=[])
        self.ops = SimpleNamespace(orders={})
        self._actions, self._query_log, self._audit = [], [], []

    def reset(self, task_id, seed):
        self._scenario = {'id': task_id, 'seed': seed, 'evaluation': {'split': 'evaluation'}}
        self._scenario['sha256'] = rv.checksum(self._scenario)
        self._initial = self.get_situation()
        self._audit = [{'minute': 0}]
        return self._initial, {}

    def get_situation(self):
        return {'minute': self.minute, 'task_id': 'incident-day-evaluation-v1'}

    def get_reports(self, **kwargs):
        return []

    def route_options(self, resource_id, sector_id='incident'):
        result = {'options': [{'id': 'route'}]}
        self._query_log.append(dict(tool='route_options', arguments={'resource_id': resource_id, 'sector_id': sector_id},
            before_decision=self.decision, minute=self.minute, result_sha256=rv.checksum(result)))
        return result

    def advance_time(self, max_minutes=60):
        self.minute, self.decision, self.terminated = 1440, 1, True
        self._audit = [{'minute': i} for i in range(1441)]
        observation = self.get_situation()
        self._actions.append(dict(action={'advance_minutes': max_minutes}, valid=True,
            start_minute=0, minute=1440, observation_sha256=rv.checksum(observation), reward=.7,
            terminated=True, truncated=False))
        return observation, .7, True, False, {'wake_reason': 'day_complete'}

    # IncidentDayTools builds the whole dispatch table even for read calls.
    def check_action(self, **kwargs): return {'valid': True}
    def evacuation_options(self, **kwargs): return {'options': []}
    def request_status(self, **kwargs): raise AssertionError('unexpected status call')
    def step(self, action): raise AssertionError('unexpected step')

    def outcomes(self):
        return dict(graded_score=.7, score_eligible=True, completed_minutes=1440,
                    reward_version='reward', invalid_abandonment=False, boundary_censored=False)

    def trajectory(self):
        return dict(task_id=self._scenario['id'], seed=self._scenario['seed'],
            implementation_sha256='engine', inputs_sha256='input', network_sha256='network',
            scenario_sha256=self._scenario['sha256'], initial_observation=self._initial,
            queries=self._query_log, transitions=self._actions, rejected_calls=[])

    def verify_episode(self):
        raise AssertionError('A second simulator replay must never execute')


class FakeObserver:
    def __init__(self, env): pass
    def snapshot(self):
        return {'findings': {}, 'coverage': {'ticks_observed': 1440,
                'capacity_ticks_checked': 1440, 'timed_positions_checked': 1}}


@pytest.fixture
def sample(monkeypatch):
    identity = dict(runtime_id='source', case_id='case', policy='dispatcher')
    env = FakeEnv()
    initial, _ = env.reset('case', 12)
    calls = []
    public = rv.PublicFacade(env, calls)
    public.get_situation()
    public.get_reports(limit=20)
    public.route_options('unit')
    public.execute('advance_time', {'max_minutes': 60})
    outcome = env.outcomes()
    checks = [{'boundary': 'initial', 'passed': True}]
    verification = dict(passed=True, exact_replay=True, independent_reward_agreement=True, outcomes=outcome)
    cell = dict(identity, status='failed', minute=1440, verification={k: True for k in
        ('passed', 'exact_replay', 'independent_reward_agreement')}, outcome=outcome,
        pins={'inputs_sha256': 'input', 'network_sha256': 'network', 'scenario_sha256': env._scenario['sha256']},
        resume_checks=checks, query_independence={'passed': True}, elapsed_seconds=44,
        split='evaluation', training_eligible=False)
    saved = dict(calls=copy.deepcopy(calls), trajectory=copy.deepcopy(env.trajectory()),
        audit=copy.deepcopy(env._audit), reports=[], orders=[], outcome=outcome,
        episode_seed=12, evaluation_metadata={'split': 'evaluation'}, verification=verification,
        resume_checks=checks, findings=[{'category': 'contradiction', 'code': 'old_observer_error'}])
    row = dict(identity, task_id='case', episode_seed=12, scenario_sha256=env._scenario['sha256'])
    freeze = {'implementation_sha256': 'engine'}
    monkeypatch.setattr(rv, 'make_environment', lambda *a, **k: (FakeEnv(), {}))
    monkeypatch.setattr(rv, 'MovementObserver', FakeObserver)
    monkeypatch.setattr(rv, 'recompute', lambda *a, **k: copy.deepcopy(outcome))
    coverage = {k: {'status': 'complete', 'observed': 1} for k in REQUIRED_COVERAGE}
    monkeypatch.setattr(rv.runner, 'audit_runtime', lambda *a: {'findings': [], 'coverage': coverage, 'durations': []})
    monkeypatch.setattr(rv.runner, 'make_policy', lambda *a: (_ for _ in ()).throw(AssertionError('policy execution')))
    return SimpleNamespace(cell=cell, saved=saved, row=row, freeze=freeze, coverage=coverage)


def replay(sample):
    return rv.replay_cell(sample.cell, sample.saved, sample.row, sample.freeze, 'unused', lambda *a: None)


def test_exact_replay_reuses_strict_finish_without_second_replay_or_policy(sample):
    result = replay(sample)
    assert result['result']['status'] == 'complete'
    assert result['result']['score'] == .7
    proof = result['result']['recorded_call_revalidation']
    assert proof['passed'] and not proof['policy_executed']
    assert (proof['calls'], proof['transitions'], proof['queries']) == (4, 1, 1)
    assert result['result']['inherited_policy_checks']['reexecuted'] is False
    assert result['result']['source_elapsed_seconds'] == 44
    assert 'elapsed_seconds' not in result['result']
    assert result['original_findings'][0]['code'] == 'old_observer_error'


@pytest.mark.parametrize('fault', ['call', 'initial', 'action', 'query', 'audit', 'reports', 'orders',
                                  'outcome', 'independent', 'seed', 'configuration', 'unverified'])
def test_frozen_evidence_mismatch_fails(sample, fault):
    d = sample.saved
    if fault == 'call': d['calls'][0]['after_minute'] = 1
    elif fault == 'initial': d['trajectory']['initial_observation']['minute'] = 2
    elif fault == 'action': d['trajectory']['transitions'][0]['reward'] = .8
    elif fault == 'query': d['trajectory']['queries'][0]['result_sha256'] = 'wrong'
    elif fault == 'audit': d['audit'][3]['minute'] = 9
    elif fault == 'reports': d['reports'].append({'id': 'invented'})
    elif fault == 'orders': d['orders'].append({'id': 'invented'})
    elif fault == 'outcome': d['outcome'] = dict(d['outcome'], graded_score=.8)
    elif fault == 'independent': d['verification']['outcomes'] = dict(d['outcome'], graded_score=.8)
    elif fault == 'seed': d['episode_seed'] = 13
    elif fault == 'configuration': d['evaluation_metadata'] = {'split': 'train'}
    elif fault == 'unverified': d['verification']['passed'] = False
    with pytest.raises(rv.Mismatch): replay(sample)


def test_late_egress_and_other_unsupported_findings_stay_blocked_unscored(sample, monkeypatch):
    finding = {'category': 'unsupported', 'code': 'recovery_after_shift_end_egress_unavailable'}
    monkeypatch.setattr(rv.runner, 'audit_runtime', lambda *a: dict(findings=[finding], coverage=sample.coverage, durations=[]))
    result = replay(sample)
    assert result['result']['status'] == 'blocked'
    assert result['result']['score'] is None
    assert result['result']['outcome']['graded_score'] == .7


def test_missing_movement_coverage_cannot_pass(sample, monkeypatch):
    monkeypatch.setattr(FakeObserver, 'snapshot', lambda self: {'findings': {}, 'coverage': {'capacity_ticks_checked': 1}})
    assert replay(sample)['result']['status'] == 'blocked'


def test_call_budget_stops_before_replay_finishes(sample):
    count = 0
    def stop(count_call=False):
        nonlocal count
        if count_call:
            count += 1
            if count > 1: raise rv.BudgetStop()
    with pytest.raises(rv.BudgetStop):
        rv.replay_cell(sample.cell, sample.saved, sample.row, sample.freeze, 'unused', stop)


@pytest.fixture
def snapshot(sample, tmp_path, monkeypatch):
    source = tmp_path / 'baseline'; source.mkdir(); (source / 'private').mkdir()
    data = tmp_path / 'package'; data.mkdir()
    freeze = dict(sample.freeze, version='evaluation-freeze-v1', training=False,
                  lifecycle_sha256='life', package_sha256='package', validator_sha256='old-validator')
    expected = [dict(runtime_id='source', case_id='case', policy='dispatcher')]
    freeze['cells'] = expected
    freeze['tasks'] = [sample.row]
    rows = {('source', 'case'): sample.row}
    package = {'test': True}
    report = dict(version=rv.runner.VERSION, split='evaluation', training_eligible=False,
                  pins=rv.source_pins(freeze), expected_cells=expected, policies=list(rv.runner.POLICIES),
                  cells=[sample.cell])
    (source / 'report.json').write_text(json.dumps(report))
    frozen = tmp_path / 'freeze.json'; frozen.write_text(json.dumps(freeze))
    name = rv.filename(sample.cell)
    artifact = source / 'private' / name
    artifact.write_bytes(gzip.compress(json.dumps(sample.saved).encode()))
    code = {'corrected_validator_sha256': 'new-validator'}
    monkeypatch.setattr(rv, 'code_identity', lambda *a: code)
    monkeypatch.setattr(rv, 'package_identity', lambda *a: (package, rows))
    monkeypatch.setattr(rv.runner, 'expected_cells', lambda *a: copy.deepcopy(expected))
    # Small domain for persistence tests; production uses the unchanged 48-cell gate.
    def refresh(r):
        r['status'] = 'complete' if all(c['status'] == 'complete' for c in r['cells']) else 'incomplete'
        r['summary'] = {'expected': len(r['cells'])}
        r['baseline_coverage_contract'] = {'unobserved_required': []}
    monkeypatch.setattr(rv.runner, 'refresh_report', refresh)
    return SimpleNamespace(source=source, frozen=frozen, data=data, artifact=artifact,
                           output=tmp_path / 'revalidated', report=report)


def run(snapshot, **kwargs):
    return rv.run(snapshot.source / 'report.json', snapshot.frozen, snapshot.data, snapshot.output,
                  reviewed_observer_sha256='reviewed', **kwargs)


def test_snapshot_hashes_and_private_output_do_not_modify_source(snapshot):
    original = {p: p.read_bytes() for p in (snapshot.artifact, snapshot.frozen, snapshot.source / 'report.json')}
    result = run(snapshot)
    assert result['status'] == 'complete'
    manifest = json.loads((snapshot.output / 'source_manifest.json').read_text())
    assert len(manifest['source_hashes']) == 3
    assert (snapshot.output / 'source_manifest.json').stat().st_mode & 0o777 == 0o600
    assert {p: p.read_bytes() for p in original} == original
    with pytest.raises(FileExistsError): run(snapshot)
    with pytest.raises(ValueError, match='separate'):
        rv.run(snapshot.source / 'report.json', snapshot.frozen, snapshot.data, snapshot.source / 'nested',
               reviewed_observer_sha256='reviewed')


def test_per_cell_resume_does_not_reexecute_completed_cell(snapshot, monkeypatch):
    run(snapshot)
    monkeypatch.setattr(rv, 'replay_cell', lambda *a: (_ for _ in ()).throw(AssertionError('replayed complete cell')))
    # Baseline is allowed to advance: resume uses its original snapshot.
    (snapshot.source / 'report.json').write_text('{"new": "live report"}')
    assert run(snapshot, resume=True)['status'] == 'complete'


def test_budget_resume_restarts_unfinished_cell(snapshot):
    first = run(snapshot, max_calls=1)
    assert first['status'] == 'incomplete'
    assert list((snapshot.output / 'cells').iterdir()) == []
    assert run(snapshot, resume=True)['status'] == 'complete'


def test_snapshot_tampering_is_rejected(snapshot):
    run(snapshot, max_calls=1)
    (snapshot.output / 'source/report.json').write_text('{}')
    with pytest.raises(rv.Mismatch, match='hash'): run(snapshot, resume=True)


@pytest.fixture
def code_fixture(tmp_path, monkeypatch):
    before, after = b'old observer\n', b'corrected observer\n'
    for path in rv.VALIDATOR_PATHS:
        target = tmp_path / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after if path == rv.OBSERVER else b'unchanged\n')
    current = {p: rv.sha((tmp_path / p).read_bytes()) for p in rv.VALIDATOR_PATHS}
    old = dict(current, **{rv.OBSERVER: rv.sha(before)})
    commit = 'a' * 40
    freeze = dict(code_commit=commit, validator_sha256=rv.checksum(old),
                  implementation_sha256='engine', lifecycle_sha256='life', reward_identity=rv.REWARD_VERSION)
    def git(*args):
        if args[0] == 'rev-parse': return commit.encode() + b'\n'
        if args[0] == 'diff': return (rv.OBSERVER + '\n').encode()
        if args[0] == 'ls-files': return (rv.SELF + '\n').encode()
        if args[0] == 'show': return before
        raise AssertionError(args)
    monkeypatch.setattr(rv, 'ROOT', tmp_path)
    monkeypatch.setattr(rv, 'git', git)
    monkeypatch.setattr(rv, 'implementation_digest', lambda: 'engine')
    monkeypatch.setattr(rv, 'registry', lambda: {'sha256': 'life'})
    monkeypatch.setattr(rv.runner, 'validator_digest', lambda: rv.checksum(current))
    return freeze, rv.sha(after), git


def test_reviewed_observer_is_the_only_authorized_production_delta(code_fixture):
    freeze, approved, _ = code_fixture
    code = rv.code_identity(freeze, approved)
    assert code['source_validator_sha256'] != code['corrected_validator_sha256']
    assert 'corrected observer' in code['observer_patch']
    with pytest.raises(ValueError, match='explicitly reviewed'):
        rv.code_identity(freeze, 'wrong')


def test_unreviewed_core_or_runner_change_rejected(code_fixture, monkeypatch):
    freeze, approved, git = code_fixture
    monkeypatch.setattr(rv, 'git', lambda *args: b'eval/evaluate_packaged_runtime.py\n' if args[0] == 'diff' else git(*args))
    with pytest.raises(ValueError, match='Unreviewed'): rv.code_identity(freeze, approved)


def test_source_validator_mismatch_rejected(code_fixture):
    freeze, approved, _ = code_fixture
    freeze['validator_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='Source validator'): rv.code_identity(freeze, approved)


def test_package_and_frozen_rows_must_match(tmp_path, monkeypatch):
    package = {'runtimes': {}}
    raw = json.dumps(package).encode()
    (tmp_path / 'manifest.json').write_bytes(raw)
    row = {'runtime_id': 'source', 'task_id': 'case', 'episode_seed': 12}
    freeze = {'package_sha256': rv.checksum(package), 'manifest_sha256': rv.sha(raw), 'tasks': [row]}
    monkeypatch.setattr(rv, 'verify_package', lambda _: package)
    monkeypatch.setattr(rv.runner, 'evaluation_rows', lambda *args: {('source', 'case'): row})
    assert rv.package_identity(tmp_path, freeze)[0] == package
    freeze['tasks'] = [dict(row, episode_seed=13)]
    with pytest.raises(rv.Mismatch, match='rows'): rv.package_identity(tmp_path, freeze)
    freeze['package_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='Package'): rv.package_identity(tmp_path, freeze)


def test_completed_cell_tampering_is_rejected_on_resume(snapshot):
    run(snapshot)
    cell_path = next((snapshot.output / 'cells').iterdir())
    cell = json.loads(cell_path.read_bytes())
    cell['result']['score'] = 1
    cell_path.write_text(json.dumps(cell))
    with pytest.raises(rv.Mismatch, match='checksum'): run(snapshot, resume=True)


@pytest.fixture
def adverse():
    rid = 'crew'
    finding = dict(category='unsupported', code='recovery_after_shift_end_egress_unavailable', resource=rid, minute=4)
    rows = []
    for minute in range(1441):
        state = 'available' if minute == 0 else 'needs_assistance' if minute < 4 else 'returning' if minute == 4 else 'off_duty'
        rows.append({'minute': minute, 'resources': {rid: {'state': state,
                     'position': [100, 0] if minute < 4 else [90, 0] if minute == 4 else [0, 0]}}, 'applied_work': []})
    env = SimpleNamespace(_scenario={'resources': [{'id': rid, 'kind': 'crew', 'duty_end_at': 2,
                                                   'base_position': [0, 0]}]}, _audit=rows,
        bus=SimpleNamespace(reports=[{'kind': 'egress_unavailable', 'observed_at': 1,
                                     'payload': {'resource_id': rid, 'position': [100, 0]}}]))
    result = dict(status='blocked', score=None, outcome={'score_eligible': True, 'graded_score': .7},
        recorded_call_revalidation={'passed': True},
        verification={k: True for k in ('passed', 'exact_replay', 'independent_reward_agreement')},
        operation_coverage={k: {'status': 'complete', 'observed': 1} for k in REQUIRED_COVERAGE},
        movement_evidence={'ticks_observed': 1440, 'capacity_ticks_checked': 1440, 'timed_positions_checked': 1})
    return result, [finding], env


@pytest.mark.parametrize('policy,score', [('withhold', None), ('score', .7)])
def test_adverse_reporting_is_explicit_and_leaves_strict_verdict_unchanged(adverse, policy, score):
    result, findings, env = adverse
    rv.apply_reporting(result, findings, env, {'status': 'blocked', 'score': None}, policy)
    assert result['status'] == 'blocked' and result['score'] is None
    assert result['reported_score'] == score
    assert result['operational_status'] == 'adverse'
    assert result['adverse_recovery_evidence'][0]['arrived_off_duty_at'] == 5
    assert result['outcome']['graded_score'] == .7


@pytest.mark.parametrize('fault', ['other_unsupported', 'contradiction', 'missing_coverage', 'unverified',
    'missing_ticks', 'unverified_paths', 'no_report', 'moved_wait', 'unfinished', 'post_shift_work', 'active'])
def test_score_option_never_waives_unproven_or_arbitrary_findings(adverse, fault):
    result, findings, env = adverse
    if fault == 'other_unsupported': findings[0]['code'] = 'uninspectable_route'
    elif fault == 'contradiction': findings.append({'category': 'contradiction', 'code': 'bad'})
    elif fault == 'missing_coverage': result['operation_coverage'].pop(next(iter(REQUIRED_COVERAGE)))
    elif fault == 'unverified': result['verification']['exact_replay'] = False
    elif fault == 'missing_ticks': result['movement_evidence']['ticks_observed'] = 1439
    elif fault == 'unverified_paths': result['movement_evidence']['journey_unverifiable'] = 1
    elif fault == 'no_report': env.bus.reports = []
    elif fault == 'moved_wait': env._audit[2]['resources']['crew']['position'] = [99, 0]
    elif fault == 'unfinished': env._audit[-1]['resources']['crew']['state'] = 'needs_assistance'
    elif fault == 'post_shift_work': env._audit[3]['applied_work'] = [{'resource_id': 'crew'}]
    elif fault == 'active': env._audit[6]['resources']['crew']['state'] = 'working'
    rv.apply_reporting(result, findings, env, {'status': 'blocked'}, 'score')
    assert result['reported_score'] is None
    assert result['operational_status'] == 'unresolved'
    assert result['status'] == 'blocked'


def test_resume_cannot_silently_switch_reporting_policy(snapshot):
    run(snapshot, max_calls=1)
    with pytest.raises(ValueError, match='policy differs'):
        run(snapshot, resume=True, adverse_outcome_policy='score')


@pytest.fixture
def reporting_matrix(adverse):
    blocked, detailed, env = adverse
    blocked.update(minute=1440, split='evaluation', training_eligible=False,
        query_independence={'passed': True}, resume_checks=[{'passed': True}])
    blocked['outcome']['completed_minutes'] = 1440
    blocked['recorded_call_revalidation']['policy_executed'] = False
    blocked['findings'] = [{'category': 'unsupported', 'code': detailed[0]['code'], 'count': 1}]
    rv.apply_reporting(blocked, detailed, env, {'status': 'blocked', 'score': None}, 'score')
    expected = [dict(runtime_id='source', case_id=f'case-{i:02}', policy=policy)
                for i in range(24) for policy in rv.runner.POLICIES]
    complete = dict(status='complete', minute=1440, split='evaluation', training_eligible=False,
        verification={k: True for k in ('passed', 'exact_replay', 'independent_reward_agreement')},
        outcome={'score_eligible': True}, query_independence={'passed': True}, resume_checks=[{'passed': True}],
        findings=[], operation_coverage=copy.deepcopy(blocked['operation_coverage']))
    cells = [dict(copy.deepcopy(complete), **identity) for identity in expected]
    cells[0] = dict(blocked, **expected[0])
    return dict(expected_cells=expected, cells=cells, adverse_outcome_policy='score')


def test_reporting_adverse_completion_requires_strict_aggregate_blocked(reporting_matrix):
    rv.refresh_revalidation_report(reporting_matrix)
    assert reporting_matrix['status'] == 'blocked'
    assert reporting_matrix['reporting_status'] == 'complete_with_adverse_outcomes'


def test_malformed_complete_cell_cannot_override_strict_aggregate_failure(reporting_matrix):
    # Stored cell labels still say complete/blocked, but the strict validator
    # discovers the malformed complete cell's failed verification.
    reporting_matrix['cells'][1]['verification']['exact_replay'] = False
    rv.refresh_revalidation_report(reporting_matrix)
    assert reporting_matrix['cells'][1]['status'] == 'complete'
    assert reporting_matrix['status'] == 'failed'
    assert reporting_matrix['reporting_status'] == 'failed'


@pytest.mark.parametrize('fault', ['failed_cell', 'other_code', 'empty_proof', 'bad_arrival',
    'stale_policy', 'unverified', 'wrong_reward', 'missing_coverage', 'count_mismatch', 'withhold'])
def test_reporting_label_does_not_replace_exact_permitted_proof(reporting_matrix, fault):
    cell = reporting_matrix['cells'][0]
    if fault == 'failed_cell': cell['status'] = 'failed'
    elif fault == 'other_code': cell['findings'][0]['code'] = 'unsupported_route'
    elif fault == 'empty_proof': cell['adverse_recovery_evidence'] = []
    elif fault == 'bad_arrival': cell['adverse_recovery_evidence'][0]['arrived_off_duty_at'] = 1441
    elif fault == 'stale_policy': cell['adverse_outcome_policy'] = 'withhold'
    elif fault == 'unverified': cell['recorded_call_revalidation']['passed'] = False
    elif fault == 'wrong_reward': cell['reported_score'] = .9
    elif fault == 'missing_coverage': cell['operation_coverage'].pop(next(iter(REQUIRED_COVERAGE)))
    elif fault == 'count_mismatch': cell['findings'][0]['count'] = 2
    elif fault == 'withhold': reporting_matrix['adverse_outcome_policy'] = 'withhold'
    rv.refresh_revalidation_report(reporting_matrix)
    assert reporting_matrix['reporting_status'] == reporting_matrix['status']
    assert reporting_matrix['reporting_status'] in ('blocked', 'failed')
