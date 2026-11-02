import copy
import json
from types import SimpleNamespace

import pytest

from eval import write_source_matrix_repro as repro


@pytest.fixture
def context(tmp_path, monkeypatch):
    scenario = {'id': 'case-1', 'seed': 12}
    scenario['sha256'] = repro.digest(scenario)
    entry = {'inputs_sha256': 'input', 'network_sha256': 'network',
             'additional_scenario_ids': ['case-1']}
    package = {'runtimes': {repro.RUNTIME: entry}, 'files': []}
    data = tmp_path / 'package'
    data.mkdir()
    (data / 'manifest.json').write_text(json.dumps(package))
    expected = [dict(runtime_id=repro.RUNTIME, case_id='case-1', policy=policy)
                for policy in repro.POLICY_NAMES]
    cells = [dict(item, status='not_run') for item in expected]
    cells[0].update(status='complete', pins=dict(inputs_sha256='input', network_sha256='network',
                    scenario_sha256=scenario['sha256']), outcome={'reward_version': repro.REWARD_VERSION})
    report = dict(version=repro.MATRIX_VERSION, package_sha256=repro.checksum(package),
                  implementation_sha256='impl', lifecycle_sha256='lifecycle', validator_sha256='validator',
                  policies=list(repro.POLICY_NAMES), expected_cells=expected, cells=cells, status='incomplete')
    row = dict(runtime_id=repro.RUNTIME, task_id='case-1', split='development', scenario_sha256=scenario['sha256'])
    monkeypatch.setattr(repro, 'verify_package', lambda _: package)
    monkeypatch.setattr(repro, 'implementation_digest', lambda: 'impl')
    monkeypatch.setattr(repro, 'registry', lambda: {'sha256': 'lifecycle'})
    monkeypatch.setattr(repro, 'validator_digest', lambda: 'validator')
    monkeypatch.setattr(repro, 'expected_cells', lambda *args: copy.deepcopy(expected))
    monkeypatch.setattr(repro, 'task_rows', lambda *args: [row])
    monkeypatch.setattr(repro, 'make_environment', lambda *args, **kwargs: (SimpleNamespace(inputs={}, network={}), entry))
    monkeypatch.setattr(repro, 'build_scenario', lambda *args: copy.deepcopy(scenario))
    monkeypatch.setattr(repro, 'git_identity', lambda: {'revision': 'commit', 'working_tree_dirty': True})
    return SimpleNamespace(data=data, report=report, package=package, scenario=scenario, row=row)


def test_manifest_verifies_identity_and_marks_pending_cells(context):
    result = repro.build_manifest(context.report, b'snapshot', context.data)
    assert result['report_cells_with_verified_pins'] == 1
    assert result['report_cells_total'] == 4
    assert result['scenarios'] == [{'id': 'case-1', 'seed': 12,
                                     'scenario_sha256': context.scenario['sha256']}]
    assert result['reward_version'] == repro.REWARD_VERSION
    assert len(result['reward_sha256']) == 64
    assert result['git'] == {'revision': 'commit', 'working_tree_dirty': True}
    assert result['report_sha256'] == repro.sha256(b'snapshot')


@pytest.mark.parametrize('field', ['package_sha256', 'implementation_sha256', 'lifecycle_sha256', 'validator_sha256'])
def test_refuses_report_pin_mismatch(context, field):
    context.report[field] = 'wrong'
    with pytest.raises(ValueError, match=field):
        repro.build_manifest(context.report, b'{}', context.data)


@pytest.mark.parametrize('mutation', ['scenario', 'input', 'network', 'reward', 'missing_finished_pins',
                                      'expected', 'duplicate_cell', 'task', 'seed', 'missing_outcome'])
def test_refuses_inconsistent_scenarios_or_report(context, mutation):
    cell = context.report['cells'][0]
    if mutation in ('scenario', 'input', 'network'):
        cell['pins'][{'scenario': 'scenario_sha256', 'input': 'inputs_sha256', 'network': 'network_sha256'}[mutation]] = 'wrong'
    elif mutation == 'reward': cell['outcome']['reward_version'] = 'wrong'
    elif mutation == 'missing_finished_pins': del cell['pins']
    elif mutation == 'expected': context.report['expected_cells'][0]['case_id'] = 'wrong'
    elif mutation == 'duplicate_cell': context.report['cells'][1] = copy.deepcopy(cell)
    elif mutation == 'task': context.row['scenario_sha256'] = 'wrong'
    elif mutation == 'seed': context.scenario['seed'] = 13
    elif mutation == 'missing_outcome': del cell['outcome']
    with pytest.raises(ValueError):
        repro.build_manifest(context.report, b'{}', context.data)


def test_cli_private_exclusive_and_rejects_package_output(context, tmp_path):
    source = tmp_path / 'report.json'
    source.write_text(json.dumps(context.report))
    output = tmp_path / 'repro.json'
    args = ['--report', str(source), '--data-dir', str(context.data), '--output']
    repro.main(args + [str(output)])
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())['scenarios'][0]['seed'] == 12
    previous = output.read_bytes()
    with pytest.raises(FileExistsError): repro.main(args + [str(output)])
    assert output.read_bytes() == previous
    with pytest.raises(ValueError, match='outside'): repro.main(args + [str(context.data / 'output.json')])
    original = source.read_bytes()
    with pytest.raises(FileExistsError): repro.main(args + [str(source)])
    assert source.read_bytes() == original


def test_refuses_code_change_during_generation(context, monkeypatch):
    values = iter(['validator', 'changed'])
    monkeypatch.setattr(repro, 'validator_digest', lambda: next(values))
    with pytest.raises(ValueError, match='changed'):
        repro.build_manifest(context.report, b'{}', context.data)
