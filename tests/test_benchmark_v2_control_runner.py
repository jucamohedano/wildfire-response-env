"""Remote-CI-only short fixtures and synthetic campaign safety checks."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import run_benchmark_v2_controls as campaign


def test_memory_preflight_refuses_low_ram_or_swap_before_runtime_import(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, 'load_runtime', lambda: pytest.fail('Heavy import before preflight'))
    monkeypatch.setattr(campaign, 'limits', lambda: pytest.fail('Limits should follow reserve preflight'))
    for available, swap in ((4 * campaign.GIB - 1, campaign.GIB), (8 * campaign.GIB, 256 * 1024**2 - 1)):
        monkeypatch.setattr(campaign, 'memory', lambda: {'MemAvailable': available, 'SwapFree': swap})
        with pytest.raises(MemoryError, match='4 GiB'):
            campaign.run(tmp_path, tmp_path / 'protocol.json', tmp_path / 'out',
                expected_protocol_sha256='p', expected_package_sha256='d', reference_task='selected')
    assert not (tmp_path / 'out').exists()


def test_memory_boundary_is_the_existing_guard_threshold(monkeypatch):
    monkeypatch.setattr(campaign, 'memory', lambda: {'MemAvailable': 4 * campaign.GIB, 'SwapFree': 256 * 1024**2})
    campaign.require_memory()


def test_module_main_spec_uses_loader_name_and_help_has_no_heavy_import(monkeypatch, capsys):
    spec = importlib.util.find_spec('eval.run_benchmark_v2_controls')
    namespace = {'__name__': '__main__', '__file__': spec.origin, '__loader__': spec.loader, '__spec__': spec}
    monkeypatch.setattr(campaign.sys, 'argv', ['run_benchmark_v2_controls', '--help'])
    with pytest.raises(SystemExit) as result:
        exec(spec.loader.get_code(spec.name), namespace)
    assert result.value.code == 0
    assert namespace['_LOADED_CODE_MATCH'] is True
    assert namespace['own_source_identity']() == campaign.own_source_identity()
    assert '--reference-task' in capsys.readouterr().out


def test_loader_mismatch_fails_policy_attestation(monkeypatch):
    runtime = SimpleNamespace(policies=campaign, baselines=campaign)
    monkeypatch.setattr(campaign, 'capture_source', lambda *args: ('0' * 64, False))
    with pytest.raises(ValueError, match='loaded code/source differs'):
        campaign.capture_policy_sources(runtime)


def test_captured_policy_disk_drift_and_runner_drift_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / 'policy.py'; path.write_text('original')
    runtime = SimpleNamespace(policies=SimpleNamespace(__file__=str(path)),
                              baselines=SimpleNamespace(__file__=str(path)),
                              loaded_sources={'policies': '0' * 64, 'baselines': '0' * 64})
    with pytest.raises(ValueError, match='changed after loading'):
        campaign.check_policy_sources(runtime)
    monkeypatch.setattr(campaign, '_LOADED_CODE_MATCH', False)
    with pytest.raises(ValueError, match='runner loaded code/source differs'):
        campaign.own_source_identity()


@pytest.fixture
def fixture_campaign(tmp_path, monkeypatch):
    # Only the tests replace OS reserve/limit effects. Production has no bypass.
    monkeypatch.setattr(campaign, 'require_memory', lambda: None)
    monkeypatch.setattr(campaign, 'limits', lambda: None)
    runtime = campaign.load_runtime()
    base = runtime.base
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_ops_v3 import IncidentDayEnvV3
    inputs, network = fixture_inputs()
    selected = 'incident-day-dev-000'
    tasks = [selected] + [f'synthetic-evaluation-{i:02}' for i in range(1, 24)]
    entry = dict(inputs_sha256=inputs['sha256'], network_sha256=network['sha256'],
                 scenario_ids=[], evaluation_scenario_ids=tasks, allow_unapproved_data=False)
    manifest = {'runtimes': {'es-ma-2021': entry}}
    protocol = dict(version='wildfire-benchmark-protocol-v2', reference_candidates=['dispatcher', 'stress'],
        weak_active_registration=dict(name='conservative', factory='eval.incident_day_policies:make_policy',
            configuration={'name': 'conservative'}, source_sha256=runtime.loaded_sources['policies'],
            dependency_sha256={'baselines.py': runtime.loaded_sources['baselines']},
            implementation_sha256=base.implementation_digest(), observation_sha256=runtime.observation_identity(),
            interface_version='wildfire-incident-day-ops-v3'))
    protocol_path = tmp_path / 'protocol' / 'protocol.json'; protocol_path.parent.mkdir()
    protocol_path.write_text(json.dumps(protocol))
    data = tmp_path / 'package'; data.mkdir()
    def make(*args, **kwargs):
        assert kwargs['observation_version'] == 'v3'
        return IncidentDayEnvV3(inputs, network, expected_inputs_sha256=inputs['sha256'],
                               expected_network_sha256=network['sha256']), copy.deepcopy(entry)
    sample, _ = make(observation_version='v3'); sample.reset(selected)
    rows = {('es-ma-2021', task): dict(runtime_id='es-ma-2021', task_id=task,
        episode_seed=sample._scenario['seed'], scenario_sha256=sample._scenario['sha256']) for task in tasks}
    monkeypatch.setattr(campaign, 'load_runtime', lambda: runtime)
    monkeypatch.setattr(base, 'verify_package', lambda *args: copy.deepcopy(manifest))
    monkeypatch.setattr(base, 'expected_cells', lambda *args: [dict(runtime_id='es-ma-2021', case_id=t, policy=p)
        for t in tasks for p in ('passive', 'dispatcher')])
    monkeypatch.setattr(base, 'evaluation_rows', lambda *args: copy.deepcopy(rows))
    monkeypatch.setattr(base, 'make_environment', make)
    return SimpleNamespace(base=base, runtime=runtime, data=data, protocol_path=protocol_path,
        protocol=protocol, manifest=manifest, selected=selected, tasks=tasks,
        kwargs=dict(expected_protocol_sha256=campaign.checksum(protocol),
                    expected_package_sha256=campaign.checksum(manifest), reference_task=selected))


def test_domain_private_reference_first_and_registered_receipts(fixture_campaign):
    f = fixture_campaign
    ctx = campaign.prepare(f.data, f.protocol_path, f.kwargs['expected_protocol_sha256'],
                           f.kwargs['expected_package_sha256'], f.selected)
    assert len(ctx.expected) == len(set(map(f.base._identity, ctx.expected))) == 25
    assert ctx.expected[0] == dict(runtime_id='es-ma-2021', case_id=f.selected, policy='stress')
    assert {c['case_id'] for c in ctx.expected[1:]} == set(f.tasks)
    assert all(c['policy'] == 'conservative' for c in ctx.expected[1:])
    conservative, stress = ctx.receipts['conservative'], ctx.receipts['stress']
    assert conservative == f.protocol['weak_active_registration']
    assert stress == dict(conservative, name='stress', configuration={'name': 'stress'})
    assert ctx.pins['campaign_domain_sha256'] == campaign.checksum(ctx.expected)


def test_short_v3_resume_matches_direct_calls_policy_observer_and_state(fixture_campaign, tmp_path):
    f = fixture_campaign
    resumed, direct = tmp_path / 'resumed', tmp_path / 'direct'
    first = campaign.run(f.data, f.protocol_path, resumed, max_commands=2, **f.kwargs)
    assert first['status'] == 'incomplete' and first['cells'][0]['policy'] == 'stress'
    # Exercise the resumed branch, which must construct its own PublicFacade.
    campaign.run(f.data, f.protocol_path, resumed, max_commands=2, resume=True, **f.kwargs)
    campaign.run(f.data, f.protocol_path, direct, max_commands=4, **f.kwargs)
    a = campaign.load_gzip(resumed / 'private/checkpoint.json.gz')
    b = campaign.load_gzip(direct / 'private/checkpoint.json.gz')
    for key in ('environment', 'policy', 'movement_observer', 'calls', 'checked', 'resume_checks', 'query_independence'):
        assert a['live'][key] == b['live'][key], key
    assert a['live']['movement_observer']['coverage']['ticks_observed'] > 0
    assert a['live']['query_independence']['passed']
    assert a['live']['resume_checks'] and all(c['passed'] for c in a['live']['resume_checks'])
    assert (resumed / 'private/checkpoint.json.gz').stat().st_mode & 0o777 == 0o600


def test_changed_reference_domain_or_protocol_cannot_resume(fixture_campaign, tmp_path):
    f = fixture_campaign; output = tmp_path / 'run'
    campaign.run(f.data, f.protocol_path, output, max_commands=1, **f.kwargs)
    changed = dict(f.kwargs, reference_task=f.tasks[1])
    with pytest.raises(ValueError, match='resume identity'):
        campaign.run(f.data, f.protocol_path, output, max_commands=1, resume=True, **changed)
    protocol = copy.deepcopy(f.protocol); protocol['reference_candidates'] = ['dispatcher']
    f.protocol_path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match='protocol identity'):
        campaign.run(f.data, f.protocol_path, output, max_commands=1, resume=True, **f.kwargs)


def test_corrupt_or_cross_policy_live_checkpoint_is_rejected(fixture_campaign, tmp_path):
    f = fixture_campaign; output = tmp_path / 'run'
    campaign.run(f.data, f.protocol_path, output, max_commands=1, **f.kwargs)
    path = output / 'private/checkpoint.json.gz'
    raw = campaign.load_gzip(path)
    raw['live']['policy']['policy'] = 'conservative'
    raw['sha256'] = campaign.checksum({k: v for k, v in raw.items() if k != 'sha256'})
    f.base.write_gzip(path, raw)
    with pytest.raises(ValueError, match='case/policy'):
        campaign.run(f.data, f.protocol_path, output, max_commands=1, resume=True, **f.kwargs)


def test_campaign_does_not_overwrite_existing_output(fixture_campaign, tmp_path):
    f = fixture_campaign; output = tmp_path / 'run'
    campaign.run(f.data, f.protocol_path, output, max_commands=1, **f.kwargs)
    before = (output / 'freeze.json').read_bytes()
    with pytest.raises(FileExistsError):
        campaign.run(f.data, f.protocol_path, output, max_commands=1, **f.kwargs)
    assert (output / 'freeze.json').read_bytes() == before
