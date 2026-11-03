import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from eval import validate_population_refill_probe as probe
from wildfire_core.catalog_lifecycle import registry


def domain():
    catalogs = [c for c in registry()['catalogs'] if c['status'] == 'active_development']
    return probe.matrix.expected_cells({'runtimes': {('es-ma-2021' if 'realmap' in c['id'] else 'fixture'):
        dict(inputs_sha256=c['inputs_sha256'], network_sha256=c['network_sha256'], scenario_ids=c['case_ids'])
        for c in catalogs}})


def original():
    expected = domain()
    cells = [dict(c, status='complete') for c in expected]
    selected = [c for c in cells if c['runtime_id'] == 'fixture' and c['policy'] == 'passive'][:2]
    for cell in selected:
        cell.update(status='blocked', minute=1440, verification={k: True for k in probe.VERIFY_KEYS},
            findings=[], pins=dict(inputs_sha256='i', network_sha256='n', scenario_sha256='s'),
            checkpoint_boundaries={'refilling': {'encountered': True, 'status': 'unsupported'}})
    return dict(version=probe.matrix.VERSION, expected_cells=expected, cells=cells, pins={'example': 'pin'})


def valid_result(cell):
    keys = ('versioned_state_except_decision_and_actions_equal', 'audit_equal', 'operations_equal',
        'fire_equal', 'communications_equal', 'population_evidence_equal', 'public_situation_except_decision_equal')
    return dict(runtime_id='fixture', case_id=cell['case_id'], policy=cell['policy'], status='passed',
        original_cell_status='blocked', original_refill_status='unsupported', baseline_kind=probe.BASELINE_KIND, case_pins=cell['pins'],
        reconstructed_wait_start_minute=0, refill_minute=2, reconstructed_wait_end_minute=4,
        reconstructed_pre_wait_full_checkpoint_equal=True, refill_restore_full_checkpoint_equal=True,
        continuation=[{'public_equal': True, 'full_checkpoint_equal': True} for _ in range(2)],
        reconstructed_wait_end_comparison=dict({k: True for k in keys}, decision_counts_equal=False, action_histories_equal=False,
                      reconstructed_baseline_decisions=1, segmented_decisions=4),
        targeted_final_minute=1440, targeted_verification={k: True for k in probe.VERIFY_KEYS})


def test_selects_eligible_cases_and_rejects_running_or_wrong_domain():
    report = original(); expected = domain()
    targets, count = probe.select_targets(report, expected)
    assert len(targets) == count == 2
    subset, count = probe.select_targets(report, expected, [targets[0]['case_id']])
    assert len(subset) == 1 and count == 2
    with pytest.raises(ValueError): probe.select_targets(report, expected, ['unknown'])
    with pytest.raises(ValueError): probe.select_targets(report, expected, [targets[0]['case_id']] * 2)
    for mutation in (lambda r: r['cells'][0].update(status='incomplete'),
                     lambda r: r['cells'].pop(), lambda r: r['expected_cells'].pop(),
                     lambda r: r['cells'][0].update(case_id='duplicate-or-unknown')):
        value = copy.deepcopy(report); mutation(value)
        with pytest.raises(ValueError): probe.select_targets(value, expected)


def test_report_sha_detects_changes_and_rejects_symlinks(tmp_path):
    path = tmp_path / 'matrix.json'
    raw = json.dumps(original()).encode(); path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    assert probe.read_original(path, sha)['version'] == probe.matrix.VERSION
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='pin differs'): probe.read_original(path, sha)
    link = tmp_path / 'link.json'; link.symlink_to(path)
    with pytest.raises(ValueError, match='regular file'): probe.read_original(link, sha)


def test_evidence_gate_rejects_unverified_or_incomplete_results():
    cell = probe.select_targets(original(), domain())[0][0]
    assert probe.passed_probe(valid_result(cell), cell)
    for mutation in (lambda r: r['continuation'].pop(),
                     lambda r: r['continuation'][1].update(full_checkpoint_equal=False),
                     lambda r: r['reconstructed_wait_end_comparison'].update(audit_equal=False),
                     lambda r: r['reconstructed_wait_end_comparison'].update(action_histories_equal=True),
                     lambda r: r['targeted_verification'].pop('passed'),
                     lambda r: r.update(original_cell_status='complete')):
        result = valid_result(cell); mutation(result)
        assert not probe.passed_probe(result, cell)


def test_publication_strips_private_details(tmp_path):
    cell = probe.select_targets(original(), domain())[0][0]
    result = valid_result(cell)
    result['calls'] = ['private calls']; result['audit'] = 'private audit'
    result['case_pins']['raw'] = 'private pin'
    result['reconstructed_wait_end_comparison']['audit'] = 'private finding'
    result['continuation'][0]['report'] = 'private report'
    result['targeted_verification']['raw'] = 'private verification'
    report = dict(version=probe.VERSION, status='incomplete', cases=[result], audit='private top level')
    probe.publish(tmp_path, report)
    data = (tmp_path / 'report.json').read_text()
    assert 'private' not in data
    assert json.loads(data)['cases'][0]['original_cell_status'] == 'blocked'


def test_real_versioned_state_comparison_ignores_only_history():
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    inputs, network = fixture_inputs()
    env = PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'])
    env.reset()
    other, _ = probe.matrix.clone(env)
    env.advance_time(4)
    for _ in range(env.minute): other.advance_time(1)
    compared = probe.end_comparison(env, other)
    assert compared['versioned_state_except_decision_and_actions_equal']
    assert compared['public_situation_except_decision_equal']
    assert not compared['decision_counts_equal'] and not compared['action_histories_equal']
    other._population_evidence['injected'] = []
    assert not probe.end_comparison(env, other)['versioned_state_except_decision_and_actions_equal']


class FakeView:
    def __init__(self, env, name):
        self.env, self.name = env, name

    @property
    def resources(self):
        return {'unit': {'state': 'refilling' if 2 <= self.env.minute <= 3 else 'available'}}

    def checkpoint(self):
        return {'name': self.name, 'minute': self.env.minute}


class FakeEnv:
    def __init__(self):
        self.inputs = {'sha256': 'i'}
        self.network = SimpleNamespace(sha256='n')
        self.ops, self.fire, self.bus = (FakeView(self, name) for name in ('ops', 'fire', 'bus'))
        self.reset('case')

    def reset(self, case):
        self._scenario = {'id': case, 'sha256': 's'}
        self.minute = self.decision = 0
        self.terminated = self.truncated = False
        self._actions = []
        self._audit = []
        self._population_evidence = {'known': True}
        return self.get_situation(), {}

    def get_situation(self):
        return {'decision': self.decision, 'minute': self.minute}

    def checkpoint(self):
        state = dict(minute=self.minute, decision=self.decision, _actions=copy.deepcopy(self._actions),
                     audit=copy.deepcopy(self._audit), scenario=copy.deepcopy(self._scenario),
                     terminated=self.terminated, truncated=self.truncated)
        return dict(version='v3', sha256=probe.matrix.checksum(state),
                    core=dict(sha256=probe.matrix.checksum(state), state=state),
                    population_evidence=copy.deepcopy(self._population_evidence))

    def restore(self, snapshot):
        state = copy.deepcopy(snapshot['core']['state'])
        self.minute, self.decision = state['minute'], state['decision']
        self._actions, self._audit, self._scenario = state['_actions'], state['audit'], state['scenario']
        self.terminated, self.truncated = state['terminated'], state['truncated']
        self._population_evidence = copy.deepcopy(snapshot['population_evidence'])
        return self.get_situation()

    def advance(self, minutes):
        start = self.minute
        self.decision += 1
        end = min(self.minute + minutes, 1440)
        if start == 0 and minutes > 1: end = 4  # an actionable wake in the original wait
        for minute in range(start + 1, end + 1):
            self.minute = minute
            self._audit.append({'minute': minute, 'resources': copy.deepcopy(self.ops.resources)})
        self.terminated = self.minute == 1440
        self._actions.append({'start': start, 'end': end, 'wait': minutes})
        return {'observation': self.get_situation(), 'terminated': self.terminated, 'truncated': False}

    def verify_episode(self):
        return {k: True for k in probe.VERIFY_KEYS}


class FakePublic:
    def __init__(self, env, calls): self.env, self.calls = env, calls
    def get_situation(self): return self.env.get_situation()
    def get_reports(self, **kwargs): return []


class FakePolicy:
    def __init__(self, name): self.name, self.actions = name, 0
    def checkpoint(self): return {'policy': self.name, 'actions': self.actions}
    def restore(self, checkpoint):
        assert checkpoint['policy'] == self.name
        self.actions = checkpoint['actions']
    def action(self, public, observation):
        self.actions += 1
        return {'advance_minutes': 60}


def install_fake_engine(monkeypatch):
    monkeypatch.setattr(probe.matrix, 'make_v3', lambda *args: (FakeEnv(), {}))
    monkeypatch.setattr(probe.matrix, 'make_policy', lambda name: FakePolicy(name))
    monkeypatch.setattr(probe.matrix, 'PublicFacade', FakePublic)
    monkeypatch.setattr(probe.matrix, 'commit', lambda public, action: public.env.advance(action['advance_minutes']))
    monkeypatch.setattr(probe.matrix, 'clone', lambda env: (copy.deepcopy(env), env.get_situation()))
    def restore(_, snapshot):
        env = FakeEnv(); env.restore(snapshot); return env
    monkeypatch.setattr(probe, '_restore_before_wait', restore)


def test_full_cell_control_flow_with_fake_engine(monkeypatch):
    install_fake_engine(monkeypatch)
    cell = probe.select_targets(original(), domain())[0][0]
    result = probe.probe_cell('unused', cell, lambda: None)
    assert probe.passed_probe(result, cell)
    assert (result['reconstructed_wait_start_minute'], result['refill_minute'], result['reconstructed_wait_end_minute']) == (0, 2, 4)
    assert result['targeted_final_minute'] == 1440
    conservative = dict(cell, policy='conservative')
    assert probe.passed_probe(probe.probe_cell('unused', conservative, lambda: None), conservative)
    monkeypatch.setattr(FakeEnv, 'verify_episode', lambda self: {'passed': False})
    with pytest.raises(ValueError, match='did not verify'):
        probe.probe_cell('unused', cell, lambda: None)


def test_subset_is_labelled_and_original_change_cannot_publish_success(tmp_path, monkeypatch):
    report = original(); expected = domain(); cells = probe.select_targets(report, expected)[0]
    monkeypatch.setattr(probe, 'source_pin', lambda: 'probe-pin')
    monkeypatch.setattr(probe.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(probe.matrix, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(probe, 'read_original', lambda *args: copy.deepcopy(report))
    monkeypatch.setattr(probe, 'probe_cell', lambda data, cell, check: valid_result(cell))
    output = tmp_path / 'subset'
    result = probe.run(tmp_path / 'data', tmp_path / 'matrix' / 'report.json', 'a' * 64,
                       output, cases=[cells[0]['case_id']])
    assert result['status'] == 'subset_complete' and result['selected_cells'] == 1
    assert json.loads((output / 'report.json').read_text())['cases'][0]['original_cell_status'] == 'blocked'
    count = [0]
    def changed(*args):
        count[0] += 1
        if count[0] > 2: raise ValueError('private change detail')
        return copy.deepcopy(report)
    monkeypatch.setattr(probe, 'read_original', changed)
    output = tmp_path / 'changed'
    with pytest.raises(ValueError):
        probe.run(tmp_path / 'data', tmp_path / 'matrix' / 'report.json', 'a' * 64, output)
    public = (output / 'report.json').read_text()
    assert json.loads(public)['status'] == 'incomplete' and 'private change detail' not in public


def test_cli_error_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--data-dir', 'x', '--original-report', 'x',
                                           '--original-report-sha256', 'x', '--output', 'x'])
    monkeypatch.setattr(probe, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('sensitive')))
    assert probe.main() == 2
    output = capsys.readouterr()
    assert 'sensitive' not in output.out and not output.err


def test_real_refill_window_resegmentation_and_checkpoint_without_full_episode():
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    inputs, network = fixture_inputs()
    def new_env():
        return PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                  expected_network_sha256=network['sha256'])
    original_env = new_env()
    original_env.reset('incident-day-coverage-v1-002')
    for _ in range(20):
        snapshot = original_env.checkpoint()
        start = len(original_env._audit)
        original_env.advance_time(60)
        seen = [row['minute'] for row in original_env._audit[start:] if
                any(unit['state'] == 'refilling' for unit in row['resources'].values())]
        if seen: break
    assert seen
    segmented = new_env(); segmented.restore(copy.deepcopy(snapshot))
    assert segmented.checkpoint() == snapshot
    while not probe._refilling(segmented) and segmented.minute < original_env.minute:
        segmented.advance_time(1)
    assert probe._refilling(segmented) and segmented.minute in seen
    restored, _ = probe.matrix.clone(segmented)
    assert restored.checkpoint() == segmented.checkpoint()
    for _ in range(2):
        assert segmented.advance_time(1) == restored.advance_time(1)
        assert segmented.checkpoint() == restored.checkpoint()
    while segmented.minute < original_env.minute: segmented.advance_time(1)
    compared = probe.end_comparison(original_env, segmented)
    assert compared['versioned_state_except_decision_and_actions_equal']
    assert compared['audit_equal'] and compared['operations_equal'] and compared['fire_equal']
    assert compared['communications_equal'] and compared['public_situation_except_decision_equal']
    assert not compared['decision_counts_equal'] and not compared['action_histories_equal']


def test_case_selection_includes_each_blocked_policy():
    report = original()
    first = probe.select_targets(report, domain())[0][0]
    conservative = next(c for c in report['cells'] if c['case_id'] == first['case_id']
                        and c['runtime_id'] == 'fixture' and c['policy'] == 'conservative')
    conservative.update(status='blocked', minute=1440, verification={k: True for k in probe.VERIFY_KEYS},
        findings=[], pins=copy.deepcopy(first['pins']),
        checkpoint_boundaries={'refilling': {'encountered': True, 'status': 'unsupported'}})
    selected, eligible = probe.select_targets(report, domain(), [first['case_id']])
    assert eligible == 3 and {c['policy'] for c in selected} == {'passive', 'conservative'}


def test_real_conservative_refill_window_preserves_policy_pre_wait_state():
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_control_v3.population_engine_v3 import PopulationEngineV3
    inputs, network = fixture_inputs()
    def new_env():
        return PopulationEngineV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                  expected_network_sha256=network['sha256'])
    original_env = new_env()
    observation, _ = original_env.reset('incident-day-egress-v1-000')
    policy = probe.matrix.make_policy('conservative')
    public = probe.matrix.PublicFacade(original_env, [])
    public.get_situation(); public.get_reports(limit=20)
    for _ in range(100):
        start = len(original_env._audit)
        action = policy.action(public, observation)
        snapshot, policy_state = original_env.checkpoint(), policy.checkpoint()
        result = probe.matrix.commit(public, action)
        observation = result['observation']
        seen = [row['minute'] for row in original_env._audit[start:] if
                any(unit['state'] == 'refilling' for unit in row['resources'].values())]
        if seen: break
    assert seen and set(action) == {'advance_minutes'} and not probe._refilling(original_env)
    segmented = new_env(); segmented.restore(copy.deepcopy(snapshot))
    resumed_policy = probe.matrix.make_policy('conservative'); resumed_policy.restore(policy_state)
    assert resumed_policy.checkpoint() == policy_state
    while not probe._refilling(segmented) and segmented.minute < original_env.minute:
        segmented.advance_time(1)
    assert probe._refilling(segmented)
    restored, _ = probe.matrix.clone(segmented)
    assert restored.checkpoint() == segmented.checkpoint()
    for _ in range(2):
        assert segmented.advance_time(1) == restored.advance_time(1)
        assert segmented.checkpoint() == restored.checkpoint()
    while segmented.minute < original_env.minute: segmented.advance_time(1)
    compared = probe.end_comparison(original_env, segmented)
    assert compared['versioned_state_except_decision_and_actions_equal']
    assert compared['public_situation_except_decision_equal']
    assert not compared['decision_counts_equal'] and not compared['action_histories_equal']


def test_incomplete_matrix_never_runs_a_probe(tmp_path, monkeypatch):
    report = original(); report['cells'][0]['status'] = 'incomplete'
    expected = domain()
    monkeypatch.setattr(probe, 'source_pin', lambda: 'probe-pin')
    monkeypatch.setattr(probe.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(probe.matrix, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(probe, 'read_original', lambda *args: copy.deepcopy(report))
    def must_not_run(*args): raise AssertionError('probe should not start')
    monkeypatch.setattr(probe, 'probe_cell', must_not_run)
    output = tmp_path / 'unfinished'
    with pytest.raises(ValueError, match='not finished'):
        probe.run(tmp_path / 'data', tmp_path / 'matrix' / 'report.json', 'a' * 64, output)
    assert json.loads((output / 'report.json').read_text())['status'] == 'incomplete'


def test_output_cannot_be_placed_in_original_matrix_or_dataset(tmp_path):
    report = tmp_path / 'matrix' / 'report.json'
    with pytest.raises(ValueError, match='separate'):
        probe.run(tmp_path / 'data', report, 'a' * 64, report.parent / 'new')
    with pytest.raises(ValueError, match='separate'):
        probe.run(tmp_path / 'data', report, 'a' * 64, tmp_path / 'data' / 'new')


def test_complete_stub_run_pins_but_never_writes_original(tmp_path, monkeypatch):
    report = original(); expected = domain()
    path = tmp_path / 'matrix' / 'report.json'; path.parent.mkdir()
    raw = json.dumps(report).encode(); path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(probe, 'source_pin', lambda: 'probe-pin')
    monkeypatch.setattr(probe.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(probe.matrix, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(probe, 'probe_cell', lambda data, cell, check: valid_result(cell))
    output = tmp_path / 'separate'
    result = probe.run(tmp_path / 'data', path, sha, output)
    assert result['status'] == 'complete' and result['selected_cells'] == 2
    assert path.read_bytes() == raw
    published = json.loads((output / 'report.json').read_text())
    assert published['pins']['original_report_sha256'] == sha
    assert published['baseline_kind'] == 'freshly_reconstructed_unsplit'
    limitation = ' '.join(published['limitations'])
    assert 'no pre-wait checkpoint or action digest' in limitation
    assert 'does not establish original trajectory identity or score equality' in limitation
    assert all(c['original_cell_status'] == 'blocked' and
               c['baseline_kind'] == 'freshly_reconstructed_unsplit' and
               c['reconstructed_pre_wait_full_checkpoint_equal'] is True
               for c in published['cases'])


def test_unverified_stub_result_fails_closed(tmp_path, monkeypatch):
    report = original(); expected = domain()
    monkeypatch.setattr(probe, 'source_pin', lambda: 'probe-pin')
    monkeypatch.setattr(probe.matrix, 'pins', lambda _: ({}, report['pins']))
    monkeypatch.setattr(probe.matrix, 'expected_cells', lambda _: expected)
    monkeypatch.setattr(probe, 'read_original', lambda *args: copy.deepcopy(report))
    def incomplete(data, cell, check):
        value = valid_result(cell); value['targeted_verification']['passed'] = False; return value
    monkeypatch.setattr(probe, 'probe_cell', incomplete)
    output = tmp_path / 'bad-result'
    with pytest.raises(ValueError, match='evidence gate'):
        probe.run(tmp_path / 'data', tmp_path / 'matrix' / 'report.json', 'a' * 64, output)
    assert json.loads((output / 'report.json').read_text())['status'] == 'incomplete'
