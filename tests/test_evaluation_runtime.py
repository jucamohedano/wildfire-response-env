"""Bounded fixture transitions and gate tests, without full-day evaluation."""
import copy
import gzip
import json
from types import SimpleNamespace
import pytest
from eval import evaluate_packaged_runtime as runner
from eval.validate_source_runtime_matrix import REQUIRED_COVERAGE
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


@pytest.fixture
def tiny(monkeypatch):
    inputs, network = fixture_inputs()
    ids = ['incident-day-dev-000'] + [f'incident-day-evaluation-v1-{i:03}' for i in range(1, 24)]
    catalog = dict(id=runner.CATALOG_ID, status='active_evaluation', case_ids=ids,
                   inputs_sha256=inputs['sha256'], network_sha256=network['sha256'])
    entry = dict(inputs_sha256=inputs['sha256'], network_sha256=network['sha256'],
                 scenario_ids=['development'], evaluation_scenario_ids=ids[:], allow_unapproved_data=False)
    manifest = {'runtimes': {'fixture': entry}}
    monkeypatch.setattr(runner, 'registry', lambda: {'catalogs': [catalog], 'sha256': 'fixture-lifecycle'})
    monkeypatch.setattr(runner, 'verify_package', lambda path: copy.deepcopy(manifest))
    def make(*args, **kwargs):
        return (IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                expected_network_sha256=network['sha256']), copy.deepcopy(entry))
    monkeypatch.setattr(runner, 'make_environment', make)
    reference, _ = make()
    reference.reset(ids[0])
    rows = {('fixture', identifier): dict(runtime_id='fixture', task_id=identifier,
        episode_seed=reference._scenario['seed'], scenario_sha256=reference._scenario['sha256'])
        for identifier in ids}
    monkeypatch.setattr(runner, 'evaluation_rows', lambda *args: copy.deepcopy(rows))
    return manifest


def checkpoint(output):
    return json.loads(gzip.decompress((output / 'private/checkpoint.json.gz').read_bytes()))


def test_exact_domain_and_missing_opt_in(tiny):
    cells = runner.expected_cells(tiny, 'fixture')
    assert len(cells) == len(set(map(runner._identity, cells))) == 48
    assert {cell['policy'] for cell in cells} == {'passive', 'dispatcher'}
    for bad in ([], tiny['runtimes']['fixture']['evaluation_scenario_ids'][:-1]):
        tiny['runtimes']['fixture']['evaluation_scenario_ids'] = bad
        with pytest.raises(ValueError, match='exact 24'): runner.expected_cells(tiny, 'fixture')


def test_short_resume_matches_full_state_policy_observer_and_calls(tiny, tmp_path, capsys):
    resumed_dir, direct_dir = tmp_path / 'resumed', tmp_path / 'direct'
    first = runner.run('fixture', resumed_dir, runtime_id='fixture', max_commands=2)
    assert first['status'] == 'incomplete'
    elapsed = checkpoint(resumed_dir)['live']['cell_elapsed_seconds']
    runner.run('fixture', resumed_dir, runtime_id='fixture', max_commands=2, resume=True)
    runner.run('fixture', direct_dir, runtime_id='fixture', max_commands=4)
    resumed, direct = checkpoint(resumed_dir), checkpoint(direct_dir)
    for key in ('environment', 'policy', 'movement_observer', 'calls', 'checked', 'resume_checks', 'query_independence'):
        assert resumed['live'][key] == direct['live'][key], key
    assert resumed['live']['cell_elapsed_seconds'] >= elapsed
    assert resumed['progress_report']['cells'][0]['elapsed_seconds'] > 0
    assert resumed['live']['environment']['state']['minute'] < 60
    assert resumed['live']['movement_observer']['coverage']['ticks_observed'] > 0
    assert (resumed_dir / 'private/checkpoint.json.gz').stat().st_mode & 0o777 == 0o600
    assert any(json.loads(line)['total'] == 48 for line in capsys.readouterr().out.splitlines())


def test_timeout_initial_checkpoint_and_expected_code_freeze(tiny, tmp_path):
    output = tmp_path / 'run'
    with pytest.raises(ValueError, match='Expected implementation'):
        runner.run('fixture', output, runtime_id='fixture', expected_implementation_sha256='wrong')
    assert not output.exists()
    runner.run('fixture', output, runtime_id='fixture', wall_seconds=1e-12)
    assert checkpoint(output)['live'] is None
    runner.run('fixture', output, runtime_id='fixture', max_commands=1, resume=True)
    assert checkpoint(output)['live']['environment']['state']['decision'] == 1


def test_changed_resume_pin_rejected(tiny, tmp_path, monkeypatch):
    output = tmp_path / 'changed'
    runner.run('fixture', output, runtime_id='fixture', max_commands=1)
    monkeypatch.setattr(runner, 'validator_digest', lambda: 'changed')
    with pytest.raises(ValueError, match='resume pins'):
        runner.run('fixture', output, runtime_id='fixture', max_commands=1, resume=True)


def test_blocked_cell_is_retained_and_resume_advances(tiny, tmp_path):
    output = tmp_path / 'blocked'
    runner.run('fixture', output, runtime_id='fixture', max_commands=1)
    data = checkpoint(output)
    report = data['progress_report']
    report['cells'][0].update(status='blocked', findings=[dict(category='unsupported', code='egress')])
    runner._save(output, report, 1, None)
    result = runner.run('fixture', output, runtime_id='fixture', max_commands=1, resume=True)
    assert result['status'] == 'blocked'
    assert result['cells'][0]['findings'] == [dict(category='unsupported', code='egress')]
    assert checkpoint(output)['next_index'] == 1
    assert checkpoint(output)['live']['identity']['policy'] == 'dispatcher'


def test_rehashed_wrong_live_policy_cannot_resume(tiny, tmp_path):
    output = tmp_path / 'identity'
    runner.run('fixture', output, runtime_id='fixture', max_commands=1)
    data = checkpoint(output)
    data['live']['policy']['policy'] = 'dispatcher'
    data['sha256'] = runner.checksum({key: value for key, value in data.items() if key != 'sha256'})
    (output / 'private/checkpoint.json.gz').write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(ValueError, match='case or policy'):
        runner.run('fixture', output, runtime_id='fixture', max_commands=1, resume=True)


@pytest.mark.parametrize('category,code,status', [
    ('unsupported', 'retained', 'blocked'),
    ('contradiction', 'retained', 'failed'),
    ('unsupported', 'fire_front_screen', 'complete'),
])
def test_gate_keeps_unsupported_and_score_components(monkeypatch, tiny, category, code, status):
    outcome = dict(graded_score=.7, protected_population_fraction=.8, completed_minutes=1440, score_eligible=True)
    env = SimpleNamespace(terminated=True, truncated=False, minute=1440, decision=3,
        verify_episode=lambda: dict(passed=True, exact_replay=True, independent_reward_agreement=True),
        outcomes=lambda: outcome, inputs={'sha256': 'input'}, network=SimpleNamespace(sha256='network'),
        _scenario={'sha256': 'scenario', 'evaluation': {'generation_seed': 20261102, 'configuration_group': 'family-0'}},
        _audit=[], trajectory=lambda: {}, bus=SimpleNamespace(reports=[]), ops=SimpleNamespace(orders={}))
    finding = dict(category=category, code=code, detail='test')
    coverage = {key: dict(status='complete', observed=1) for key in REQUIRED_COVERAGE}
    monkeypatch.setattr(runner, 'audit_runtime', lambda *args: dict(findings=[finding], coverage=coverage, durations=[]))
    observer = SimpleNamespace(snapshot=lambda: {'findings': {}, 'coverage': {'capacity_ticks_checked': 1440,
                                                                          'timed_positions_checked': 1}})
    result, private = runner.finish_cell(dict(runtime_id='fixture', case_id='test', policy='passive'),
                                        env, observer, [], [{'passed': True}], {'passed': True})
    assert result['status'] == status
    assert result['score'] == (.7 if status == 'complete' else None)
    assert result['outcome'] == outcome and private['findings'] == [finding]
    assert result['findings'][0]['category'] == category

    assert private['evaluation_metadata'] == env._scenario['evaluation']
    assert 'generation_seed' not in result and 'configuration_group' not in result
    expected = runner.expected_cells(tiny, 'fixture')
    # Independently enforce the same gate when refreshing persisted reports.
    report = dict(expected_cells=expected, cells=[dict(result, **identity) for identity in expected])
    for cell in report['cells']:
        cell['status'] = 'complete'
    runner.refresh_report(report)
    assert report['status'] == status
    serialized = json.dumps(report)
    assert 'generation_seed' not in serialized and 'configuration_group' not in serialized


@pytest.mark.parametrize('fault', ['seed', 'digest', 'content'])
def test_compiled_case_must_match_frozen_row_before_policy_execution(tiny, tmp_path, monkeypatch, fault):
    make = runner.make_environment
    executed = []
    def corrupt(*args, **kwargs):
        env, entry = make(*args, **kwargs)
        reset = env.reset
        def wrong_reset(*reset_args):
            observation, info = reset(*reset_args)
            if fault == 'seed': env._scenario['seed'] += 1
            elif fault == 'digest': env._scenario['sha256'] = '0' * 64
            else: env._scenario['profile']['ground_setup_minutes'] += 1
            return observation, info
        env.reset = wrong_reset
        return env, entry
    monkeypatch.setattr(runner, 'make_environment', corrupt)
    monkeypatch.setattr(runner, 'commit', lambda *args: executed.append('commit'))
    with pytest.raises(ValueError, match='frozen evaluation row'):
        runner.run('fixture', tmp_path / fault, runtime_id='fixture', max_commands=1)
    assert executed == []


@pytest.mark.parametrize('fault', ['seed', 'configuration'])
def test_resumed_case_is_bound_even_with_rehashed_checkpoint(tiny, tmp_path, monkeypatch, fault):
    output = tmp_path / fault
    runner.run('fixture', output, runtime_id='fixture', max_commands=1)
    data = checkpoint(output)
    frozen = data['live']['environment']
    scenario = frozen['state']['_scenario']
    if fault == 'seed': scenario['seed'] += 1
    else: scenario['profile']['ground_setup_minutes'] += 1
    scenario['sha256'] = runner.checksum({key: value for key, value in scenario.items() if key != 'sha256'})
    frozen['sha256'] = runner.checksum({key: value for key, value in frozen.items() if key != 'sha256'})
    data['sha256'] = runner.checksum({key: value for key, value in data.items() if key != 'sha256'})
    (output / 'private/checkpoint.json.gz').write_bytes(gzip.compress(json.dumps(data).encode()))
    executed = []
    monkeypatch.setattr(runner, 'commit', lambda *args: executed.append('commit'))
    with pytest.raises(ValueError, match='frozen evaluation row'):
        runner.run('fixture', output, runtime_id='fixture', max_commands=1, resume=True)
    assert executed == []


def test_missing_aggregate_baseline_operation_blocks_without_requiring_optional_actions(tiny):
    expected = runner.expected_cells(tiny, 'fixture')
    coverage = {key: dict(status='complete', observed=1) for key in REQUIRED_COVERAGE}
    for optional in runner.BASELINE_OPTIONAL_OPERATIONS:
        coverage[optional] = dict(status='not_exercised', observed=0)
    good = dict(status='complete', minute=1440, split='evaluation', training_eligible=False,
        verification=dict(passed=True, exact_replay=True, independent_reward_agreement=True),
        outcome=dict(score_eligible=True), query_independence=dict(passed=True), resume_checks=[{'passed': True}],
        findings=[], operation_coverage=coverage)
    report = dict(expected_cells=expected, cells=[dict(copy.deepcopy(good), **identity) for identity in expected])
    runner.refresh_report(report)
    assert report['status'] == 'complete'
    assert report['baseline_coverage_contract']['version'] == 'passive-dispatcher-operations-v1'
    assert set(report['baseline_coverage_contract']['optional_operations']) == {'check_action', 'releases', 'shelter'}
    for cell in report['cells']:
        cell['operation_coverage']['evacuate'] = dict(status='not_exercised', observed=0)
    runner.refresh_report(report)
    assert report['status'] == 'blocked'
    assert report['baseline_coverage_contract']['unobserved_required'] == ['evacuate']
    # Partial progress has not failed its aggregate contract merely because a
    # later baseline cell has not yet exercised evacuation.
    report['cells'][-1]['status'] = 'not_run'
    runner.refresh_report(report)
    assert report['status'] == 'incomplete'
