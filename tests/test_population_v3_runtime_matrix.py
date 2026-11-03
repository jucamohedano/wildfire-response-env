import copy
import json

import pytest

from eval import validate_population_v3_runtime as runner
from eval.validate_incident_day_runtime import REQUIRED_OPERATIONS
from wildfire_core.catalog_lifecycle import registry


def domain():
    catalogs = [c for c in registry()['catalogs'] if c['status'] == 'active_development']
    return runner.expected_cells({'runtimes': {str(i): dict(inputs_sha256=c['inputs_sha256'],
        network_sha256=c['network_sha256'], scenario_ids=c['case_ids']) for i, c in enumerate(catalogs)}})


def completed():
    expected = domain()
    return dict(expected_cells=expected, pins={'test': 'pin'}, runtime_findings=[],
        static_cases=[dict(runtime_id=runtime, case_id=case, findings=[])
                      for runtime, case in sorted({(c['runtime_id'], c['case_id']) for c in expected})], cells=[dict(c, status='complete', minute=1440,
        verification=dict(passed=True, exact_replay=True, independent_reward_agreement=True),
        resume_checks=[{'boundary': 'hourly', 'passed': True, 'clone_transitions': 2, 'live_transitions': 2}], query_independence={'passed': True},
        checkpoint_boundaries={k: {'encountered': k == 'hourly', 'status': 'exercised' if k == 'hourly' else 'not_exercised'} for k in runner.BOUNDARIES},
        operation_coverage={k: {'observed': 1} for k in REQUIRED_OPERATIONS}) for c in expected])


def test_matrix_gate_rejects_missing_unverified_and_incomplete():
    assert runner.matrix_status(completed()) == 'complete'
    for change in (lambda r: r['cells'].pop(), lambda r: r['cells'][0].update(status='incomplete'),
                   lambda r: r['cells'][0]['verification'].pop('passed'),
                   lambda r: r['cells'][0].update(resume_checks=[]),
                   lambda r: r['static_cases'].pop(), lambda r: r.pop('pins')):
        report = completed(); change(report)
        assert runner.matrix_status(report) != 'complete'
    report = completed(); report['expected_cells'][0] = copy.deepcopy(report['expected_cells'][1])
    assert runner.matrix_status(report) != 'complete'
    report = completed(); report['static_cases'][0] = copy.deepcopy(report['static_cases'][1])
    assert runner.matrix_status(report) != 'complete'
    report = completed(); report['static_cases'][0]['case_id'] = 'unknown'
    assert runner.matrix_status(report) != 'complete'


def test_public_output_excludes_private_fields(tmp_path):
    report = completed()
    report.update(version=runner.VERSION, status='incomplete', audit='secret audit', reports='secret report')
    report['cells'][0]['audit'] = 'secret cell audit'
    report['cells'][0]['calls'] = ['secret call']
    runner.publish(tmp_path, report)
    data = (tmp_path / 'report.json').read_text()
    assert 'secret' not in data
    assert json.loads(data)['status'] == 'incomplete'


def test_budget_error_and_source_change_never_pass(tmp_path, monkeypatch):
    expected = domain()
    manifest = {'runtimes': {}}
    monkeypatch.setattr(runner, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(runner, 'pins', lambda _: (manifest, {'pin': 1}))
    monkeypatch.setattr(runner, 'run_cell', lambda *args: (_ for _ in ()).throw(RuntimeError('private details')))
    monkeypatch.setattr(runner, 'make_v3', lambda *args: (None, None))
    output = tmp_path / 'error'
    with pytest.raises(RuntimeError): runner.run('unused', output)
    data = json.loads((output / 'report.json').read_text())
    assert data['status'] == 'incomplete' and data['reason'] == 'validation_error'
    assert 'private details' not in json.dumps(data)
    counter = iter([{'pin': 1}, {'pin': 2}])
    monkeypatch.setattr(runner, 'pins', lambda _: (manifest, next(counter)))
    output = tmp_path / 'changed'
    with pytest.raises(ValueError, match='pins changed'): runner.run('unused', output)
    assert json.loads((output / 'report.json').read_text())['status'] == 'incomplete'
    output = tmp_path / 'budget'
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(ticks, 2))
    assert runner.run('unused', output, wall_seconds=1)['status'] == 'incomplete'
    assert json.loads((output / 'report.json').read_text())['reason'] == 'wall_budget'


def test_real_v3_clone_and_queries_preserve_live_episode():
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3, VERSION
    from eval.incident_day_policies import make_policy
    inputs, network = fixture_inputs()
    env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    env.reset()
    before = env.checkpoint()
    assert env.get_situation()['interface_version'] == VERSION
    assert runner.query_independence(env)['passed']
    assert len(runner.continuation(env, make_policy('dispatcher'))) == 2
    assert env.checkpoint() == before


def test_selected_subset_and_signal_are_incomplete(tmp_path, monkeypatch):
    from types import SimpleNamespace
    expected = domain()
    manifest = {'runtimes': {'test': {}}}
    monkeypatch.setattr(runner, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(runner, 'pins', lambda _: (manifest, {'pin': 1}))
    env = SimpleNamespace(inputs={}, network=SimpleNamespace(document={}))
    entry = {'scenario_ids': [], 'allow_unapproved_data': True}
    monkeypatch.setattr(runner, 'make_v3', lambda *args: (env, entry))
    monkeypatch.setattr(runner, 'static_audit', lambda *args: [dict(case=str(i), findings=[]) for i in range(12)])
    monkeypatch.setattr(runner, 'run_cell', lambda env, identity, check: dict(identity, status='complete'))
    selected = runner.run('unused', tmp_path / 'subset', max_cells=1)
    assert selected['status'] == 'incomplete' and selected['reason'] == 'selected_subset'
    assert sum(c['status'] == 'complete' for c in selected['cells']) == 1
    monkeypatch.setattr(runner, 'run_cell', lambda *args: (_ for _ in ()).throw(runner.Interrupted('signal')))
    stopped = runner.run('unused', tmp_path / 'signal')
    assert stopped['status'] == 'incomplete' and stopped['reason'] == 'signal'
    assert stopped['cells'][0]['status'] == 'incomplete'


@pytest.mark.parametrize('stop_kind', ['wall_budget', 'signal'])
def test_final_staging_timeout_never_publishes_success(tmp_path, monkeypatch, stop_kind):
    from types import SimpleNamespace
    expected = domain()
    pairs = {(c['runtime_id'], c['case_id']) for c in expected}
    runtimes = {r: {'scenario_ids': [c for runtime, c in sorted(pairs) if runtime == r],
                    'allow_unapproved_data': True} for r, _ in pairs}
    manifest = {'runtimes': runtimes}
    monkeypatch.setattr(runner, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(runner, 'pins', lambda _: (manifest, {'pin': 1}))
    env = SimpleNamespace(inputs={}, network=SimpleNamespace(document={}))
    monkeypatch.setattr(runner, 'make_v3', lambda _, runtime: (env, runtimes[runtime]))
    monkeypatch.setattr(runner, 'static_audit', lambda inputs, network, bounds, unused, ids, allowed:
                        [dict(case=case, findings=[]) for case in ids])
    good = {runner.cell_identity(c): c for c in completed()['cells']}
    monkeypatch.setattr(runner, 'run_cell', lambda env, identity, check: copy.deepcopy(good[runner.cell_identity(identity)]))
    now = [0.0]
    monkeypatch.setattr(runner.time, 'monotonic', lambda: now[0])
    original_dump = runner.json.dump
    def slow_dump(obj, *args, **kwargs):
        original_dump(obj, *args, **kwargs)
        if obj['status'] == 'complete':
            if stop_kind == 'signal':
                runner.signal.getsignal(runner.signal.SIGTERM)(runner.signal.SIGTERM, None)
            else:
                now[0] = 2.0
    monkeypatch.setattr(runner.json, 'dump', slow_dump)
    real_replace = runner.os.replace
    published = []
    def record_replace(src, dest):
        with open(src) as stream:
            published.append(json.load(stream)['status'])
        real_replace(src, dest)
    monkeypatch.setattr(runner.os, 'replace', record_replace)
    output = tmp_path / 'staged'
    result = runner.run('unused', output, wall_seconds=1)
    assert result['status'] == 'incomplete' and result['reason'] == stop_kind
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'incomplete' and report['elapsed_seconds'] == now[0]
    assert report['workers'] == 1 and 'complete' not in published
    assert sorted(p.name for p in output.iterdir()) == ['report.json']


def test_cli_sanitizes_exceptions(monkeypatch, capsys):
    monkeypatch.setattr(runner.sys, 'argv', ['runner', '--data-dir', 'unused', '--output', 'unused'])
    def fail(*args, **kwargs):
        raise RuntimeError('sensitive audit and raw report')
    monkeypatch.setattr(runner, 'run', fail)
    assert runner.main() == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'status': 'incomplete', 'reason': 'validation_error'}
    assert not captured.err and 'sensitive' not in captured.out


def test_validator_source_change_rejected_before_dataset_access(monkeypatch):
    monkeypatch.setattr(runner, '_VALIDATOR_SOURCE_AT_CAPTURE_SHA256', 'different')
    with pytest.raises(ValueError, match='source changed'):
        runner.pins('unused')


def test_hard_timeout_interrupts_stalled_operation_and_restores_timer(tmp_path, monkeypatch):
    import signal
    import time
    original_handler = signal.getsignal(signal.SIGALRM)
    original_timer = signal.getitimer(signal.ITIMER_REAL)
    def previous_handler(signum, frame):
        pass
    signal.signal(signal.SIGALRM, previous_handler)
    signal.setitimer(signal.ITIMER_REAL, 10.0, 5.0)
    monkeypatch.setattr(runner, 'pins', lambda _: time.sleep(2))
    try:
        started = time.monotonic()
        result = runner.run('unused', tmp_path / 'stall', wall_seconds=0.15)
        assert time.monotonic() - started < 1.5
        assert result['status'] == 'incomplete' and result['reason'] == 'wall_budget'
        assert json.loads((tmp_path / 'stall' / 'report.json').read_text())['status'] == 'incomplete'
        assert signal.getsignal(signal.SIGALRM) is previous_handler
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 8 < remaining < 10 and interval == 5
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original_handler)
        if original_timer[0]: signal.setitimer(signal.ITIMER_REAL, *original_timer)


def test_loader_mismatch_and_preimport_fail_closed(monkeypatch):
    import wildfire_control_v3
    monkeypatch.setattr(runner, '_PREIMPORTED_VALIDATOR_MODULES', ('eval.hub_runtime',))
    with pytest.raises(ValueError, match='preimported'):
        runner.check_source_loader_consistency()
    monkeypatch.setattr(runner, '_PREIMPORTED_VALIDATOR_MODULES', ())
    monkeypatch.setattr(runner, '_PREIMPORTED_CORE_MODULES', ())
    monkeypatch.setattr(wildfire_control_v3, '_capture_source', lambda *args: ('stale', False))
    with pytest.raises(ValueError, match='loader code differ'):
        runner.check_source_loader_consistency()


def test_transient_phase_reported_and_blocks_matrix():
    audit = [{'resources': {'crew': {'state': 'travel'}}},
             {'resources': {'crew': {'state': 'refilling'}}}]
    checks = [{'boundary': 'travel', 'passed': True}]
    evidence = runner.boundary_coverage(audit, {'travel'}, checks)
    assert evidence['travel']['status'] == 'exercised'
    assert evidence['refilling'] == {'encountered': True, 'status': 'unsupported'}
    assert evidence['resting']['status'] == 'not_exercised'
    report = completed()
    report['cells'][0]['checkpoint_boundaries'] = evidence
    assert runner.matrix_status(report) != 'complete'


def test_full_checkpoint_mismatch_after_restore_is_rejected(monkeypatch):
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    from eval.incident_day_policies import make_policy
    inputs, network = fixture_inputs()
    env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    env.reset()
    real_clone = runner.clone
    def damaged_clone(original):
        other, observation = real_clone(original)
        checkpoint = other.checkpoint
        def damaged_checkpoint():
            data = checkpoint()
            data['population_evidence'] = {'corrupt': True}
            return data
        other.checkpoint = damaged_checkpoint
        return other, observation
    monkeypatch.setattr(runner, 'clone', damaged_clone)
    with pytest.raises(ValueError, match='checkpoint changed on restore'):
        runner.continuation(env, make_policy('passive'))


def test_stale_timestamp_pyc_is_detected(tmp_path):
    import importlib.machinery
    import os
    import py_compile
    import wildfire_control_v3
    path = tmp_path / 'stale_probe.py'
    path.write_text('value = 1\n')
    original_time = path.stat().st_mtime_ns
    py_compile.compile(str(path), doraise=True)
    path.write_text('value = 2\n')
    os.utime(path, ns=(original_time, original_time))
    loader = importlib.machinery.SourceFileLoader('stale_probe', str(path))
    _, matches = wildfire_control_v3._capture_source('stale_probe', str(path), loader)
    assert not matches


def test_full_checkpoint_mismatch_after_a_continued_step_is_rejected(monkeypatch):
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    from eval.incident_day_policies import make_policy
    inputs, network = fixture_inputs()
    env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    env.reset()
    real_clone = runner.clone
    count = [0]
    def damaged_clone(original):
        other, observation = real_clone(original)
        count[0] += 1
        if count[0] == 2:
            checkpoint = other.checkpoint
            def damaged_checkpoint():
                data = checkpoint()
                if other.decision > 0:
                    data['population_evidence'] = {'corrupt': True}
                return data
            other.checkpoint = damaged_checkpoint
        return other, observation
    monkeypatch.setattr(runner, 'clone', damaged_clone)
    with pytest.raises(ValueError, match='checkpoint continuation differs'):
        runner.continuation(env, make_policy('passive'))


def test_two_step_live_continuation_is_required_and_mismatch_fails():
    first = ('first action', 'result', 'checkpoint', 'policy')
    second = ('second action', 'result', 'checkpoint', 'policy')
    record = {'boundary': 'hourly', 'passed': False, 'clone_transitions': 2, 'live_transitions': 0}
    pending = [{'record': record, 'predicted': [first, second]}]
    pending = runner.advance_continuations(pending, first)
    assert record['live_transitions'] == 1 and record['passed'] is False and len(pending) == 1
    with pytest.raises(ValueError, match='Live checkpoint continuation differs'):
        runner.advance_continuations(pending, ('different live state',))
    assert record['passed'] is False
    pending = runner.advance_continuations(pending, second)
    assert not pending and record['passed'] is True and record['live_transitions'] == 2


def test_incomplete_live_continuation_cannot_pass_matrix():
    report = completed()
    check = report['cells'][0]['resume_checks'][0]
    check['live_transitions'] = 1
    assert runner.matrix_status(report) != 'complete'
    check.pop('live_transitions')
    check['transitions'] = 2
    assert runner.matrix_status(report) != 'complete'


def test_source_attestation_names_import_gap():
    assert 'do not attest to code executed during import' in runner.__doc__
    assert 'not past module execution' in runner.check_source_loader_consistency.__doc__


def test_real_v3_two_live_steps_match_restored_continuation():
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    from eval.incident_day_policies import make_policy
    inputs, network = fixture_inputs()
    env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    observation, _ = env.reset()
    policy = make_policy('dispatcher')
    predicted = runner.continuation(env, policy)
    assert len(predicted) == 2
    record = {'boundary': 'hourly', 'passed': False, 'clone_transitions': len(predicted), 'live_transitions': 0}
    pending = [{'record': record, 'predicted': predicted}]
    public = runner.PublicFacade(env, [])
    for _ in predicted:
        action = policy.action(public, observation)
        result = runner.commit(public, action)
        observation = result['observation']
        actual = (action, runner.checksum(result), runner.checksum(env.checkpoint()),
                  runner.checksum(policy.checkpoint()))
        pending = runner.advance_continuations(pending, actual)
    assert not pending and record['passed'] is True and record['live_transitions'] == 2
