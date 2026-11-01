import copy

import pytest

from eval import packaged_runtime_pilot as pilot


def record(minute, state, water, *, status='unwarned', work=False):
    return {'minute': minute, 'resources': {'u': {'state': state, 'water_litres': water, 'fuel_minutes': None}},
            'population': {'p': {'status': status}},
            'applied_work': [{'resource_id': 'u', 'state': 'working'}] if work else []}


def test_evidence_requires_actual_transitions_and_refill_completion():
    audit = [record(0, 'available', 5), record(1, 'mobilizing', 5),
             record(2, 'travel', 5), record(3, 'on_scene', 5),
             record(4, 'working', 3, status='sheltering', work=True),
             record(5, 'refilling', 3, status='sheltering'),
             record(6, 'available', 5, status='sheltering')]
    evidence = pilot.collect_evidence(audit)
    assert all(len(items) == 1 for items in evidence.values())
    assert evidence['arrival'][0]['resource_id'] == 'u'
    without_refill = copy.deepcopy(audit)
    without_refill[-1]['resources']['u']['water_litres'] = 3
    assert not pilot.collect_evidence(without_refill)['refill']
    no_dispatch = copy.deepcopy(audit)
    no_dispatch[1]['resources']['u']['state'] = 'available'
    assert not pilot.collect_evidence(no_dispatch)['arrival']


def test_rejects_missing_minutes_and_changed_roster():
    with pytest.raises(ValueError, match='contiguous'):
        pilot.collect_evidence([record(0, 'available', 0), record(2, 'travel', 0)])
    second = record(1, 'travel', 0)
    second['population'] = {}
    with pytest.raises(ValueError, match='roster'):
        pilot.collect_evidence([record(0, 'available', 0), second])


@pytest.mark.parametrize('limit', [0, -1, 1801, float('nan'), True])
def test_wall_limit_is_bounded(limit):
    with pytest.raises(ValueError, match='limits'):
        pilot.run('/not-used', wall_seconds=limit)



@pytest.fixture
def package(tmp_path):
    import hashlib
    import json
    from eval import hub_runtime as hub
    from wildfire_core.catalog_lifecycle import active_day_case_ids, registry
    from wildfire_core.incident_day import fixture_inputs
    tmp_path = tmp_path / 'package'
    tmp_path.mkdir()
    inputs, network = fixture_inputs()
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    payload = {'runtime/inputs.json': encode(inputs), 'runtime/network.json': encode(network),
               'tasks/development.parquet': b'synthetic test'}
    files = []
    for name, raw in payload.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(raw)
        files.append(dict(path=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    entry = dict(inputs='runtime/inputs.json', network='runtime/network.json',
                 inputs_sha256=inputs['sha256'], network_sha256=network['sha256'],
                 allow_unapproved_data=False, scenario_ids=list(active_day_case_ids(inputs['sha256'], network['sha256'])))
    manifest = dict(version=hub.VERSION, release_version='0.0.0-test', dataset_repo='test', source_revision='test',
                    engine_implementation_sha256=hub.implementation_digest(), lifecycle_sha256=registry()['sha256'],
                    files=files, runtimes={'fixture': entry})
    (tmp_path / 'manifest.json').write_bytes(encode(manifest))
    return tmp_path


def test_tools_reads_checks_and_commits_use_named_api():
    class Tools:
        def __init__(self):
            self.calls = []
        def execute(self, name, args):
            self.calls.append((name, args))
            if name == 'check_action':
                return {'valid': False, 'error': 'invalid draft'}
            return {'options': []}
    public = pilot.PublicTools(None)
    public.tools = Tools()
    assert public.route_options('r')['options'] == []
    assert public.evacuation_options('p', 'd')['options'] == []
    assert not public.check_action({})['valid']
    assert [call[0] for call in public.tools.calls] == ['route_options', 'evacuation_options', 'check_action']


def test_command_limit_checkpoint_resume_and_identity(package, tmp_path):
    output = tmp_path / 'output'
    first = pilot.run(package, max_commands=2, output_dir=output)
    assert first['status'] == 'incomplete' and first['reason'] == 'command_limit'
    assert first['score'] is None and not first['gate_passed']
    assert (output / 'private' / 'checkpoint.json.gz').stat().st_mode & 0o777 == 0o600
    assert (output / 'private').stat().st_mode & 0o777 == 0o700
    second = pilot.run(package, max_commands=4, output_dir=output, resume=True)
    assert second['commands'] == 4 and second['simulated_minutes'] >= first['simulated_minutes']
    with pytest.raises(ValueError, match='identity'):
        pilot.run(package, case_id='incident-day-egress-v1-001', max_commands=5, output_dir=output, resume=True)


def test_timeout_checkpoint_and_resume(package, tmp_path, monkeypatch):
    output = tmp_path / 'timeout'
    original = pilot.PublicTools.call
    def slow(self, name, **kwargs):
        import time
        if name == 'get_situation':
            time.sleep(.05)
        return original(self, name, **kwargs)
    monkeypatch.setattr(pilot.PublicTools, 'call', slow)
    result = pilot.run(package, output_dir=output, wall_seconds=.01)
    assert result['reason'] == 'wall_timeout' and result['score'] is None
    assert result['checkpoint'] and result['simulated_minutes'] is not None
    monkeypatch.setattr(pilot.PublicTools, 'call', original)
    resumed = pilot.run(package, output_dir=output, resume=True, max_commands=2)
    assert resumed['commands'] == 2


def test_development_lifecycle_selection_and_archival_rejection():
    from wildfire_core.catalog_lifecycle import select_day_case_id
    from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
    case = 'incident-day-realmap-development-v1-000'
    assert select_day_case_id(case, PACK_PIN, NETWORK_PIN) == case
    with pytest.raises(ValueError):
        select_day_case_id(case, '0' * 64, NETWORK_PIN)
    with pytest.raises(ValueError):
        select_day_case_id('incident-day-realmap-v1-000', PACK_PIN, NETWORK_PIN)


def test_resume_matches_uninterrupted_policy_and_environment(package, tmp_path):
    resumed_dir = tmp_path / 'resumed'
    direct_dir = tmp_path / 'direct'
    pilot.run(package, output_dir=resumed_dir, max_commands=4)
    pilot.run(package, output_dir=resumed_dir, max_commands=8, resume=True)
    pilot.run(package, output_dir=direct_dir, max_commands=8)
    resumed = pilot._load(resumed_dir / 'private/checkpoint.json.gz')
    direct = pilot._load(direct_dir / 'private/checkpoint.json.gz')
    assert resumed['policy'] == direct['policy']
    assert resumed['public_trajectory'] == direct['public_trajectory']
    assert resumed['environment'] == direct['environment']


def test_checkpoint_tampering_rejected(package, tmp_path):
    import gzip
    import json
    output = tmp_path / 'tamper'
    pilot.run(package, output_dir=output, max_commands=1)
    checkpoint = output / 'private/checkpoint.json.gz'
    data = json.loads(gzip.decompress(checkpoint.read_bytes()))
    data['policy']['released'] = ['fabricated']
    checkpoint.write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(ValueError, match='integrity'):
        pilot.run(package, output_dir=output, max_commands=2, resume=True)


def test_development_case_must_be_advertised(tmp_path, monkeypatch):
    from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
    case = 'incident-day-realmap-development-v1-000'
    entry = dict(inputs_sha256=PACK_PIN, network_sha256=NETWORK_PIN,
                 scenario_ids=['incident-day-realmap-v2-000'])
    monkeypatch.setattr(pilot, 'make_environment', lambda *args, **kwargs: (object(), entry))
    with pytest.raises(ValueError, match='not advertised'):
        pilot.run('/unused', runtime='es-ma-2021', case_id=case, output_dir=tmp_path / 'unadvertised')
    entry['additional_scenario_ids'] = [case]
    class Accepted(Exception):
        pass
    class Env:
        def reset(self, chosen):
            assert chosen == case
            raise Accepted()
    monkeypatch.setattr(pilot, 'make_environment', lambda *args, **kwargs: (Env(), entry))
    with pytest.raises(Accepted):
        pilot.run('/unused', runtime='es-ma-2021', case_id=case, output_dir=tmp_path / 'advertised')


def test_verified_day_without_all_evidence_has_no_score(tmp_path, monkeypatch):
    class Env:
        minute = 0
        terminated = truncated = False
        _audit = [record(0, 'available', 0)]
        def reset(self, case):
            pass
        def verify_episode(self):
            return dict(passed=True, exact_replay=True, independent_reward_agreement=True,
                        outcomes=dict(completed_minutes=1440, score_eligible=True, graded_score=1))
    env = Env()
    class Public:
        def __init__(self, env, trajectory=None):
            pass
        def call(self, name, **kwargs):
            if name == 'get_situation':
                return {}
            env.minute = 1440
            env.terminated = True
    class Policy:
        def action(self, public, obs):
            return {'advance_minutes': 60}
    monkeypatch.setattr(pilot, 'make_environment', lambda *args, **kwargs: (env,
        {'inputs_sha256': 'x', 'network_sha256': 'y', 'scenario_ids': ['case']}))
    monkeypatch.setattr(pilot, 'select_day_case_id', lambda *args: 'case')
    monkeypatch.setattr(pilot, 'PublicTools', Public)
    monkeypatch.setattr(pilot, 'PilotPolicy', Policy)
    result = pilot.run('/unused', output_dir=tmp_path / 'empty')
    assert result['verification'] == 'passed' and result['reason'] == 'evidence_missing'
    assert result['score'] is None and not result['gate_passed']


def test_policy_uses_named_tools_for_real_interactions(package, tmp_path, monkeypatch):
    seen = []
    original = pilot.IncidentDayTools.execute
    def spy(self, name, args):
        seen.append(name)
        return original(self, name, args)
    monkeypatch.setattr(pilot.IncidentDayTools, 'execute', spy)
    pilot.run(package, output_dir=tmp_path / 'tools', max_commands=20)
    assert {'get_situation', 'check_action', 'route_options', 'commit_orders', 'advance_time'} <= set(seen)


def test_public_snapshot_excludes_audit_and_private_fields():
    observation = {'minute': 2, 'audit': 'secret-audit', 'private_label': 'secret-label',
        'resources': [{'id': 'r', 'kind': 'engine', 'state': 'working', 'private_label': 'secret-resource'}],
        'districts': [{'id': 'p', 'status': 'sheltering', 'private_label': 'secret-population'}],
        'reports': [{'kind': 'status', 'source': 'r', 'payload': {'private_label': 'secret-report'}}]}
    import json
    text = json.dumps(pilot._public_snapshot(observation))
    assert 'secret' not in text and 'private_label' not in text and 'audit' not in text
    assert 'sheltering' in text and 'working' in text


def test_success_artifacts_are_private_and_byte_counts_exact(tmp_path):
    import json
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    checkpoint = private / 'checkpoint.json.gz'
    checkpoint.write_bytes(b'checkpoint')
    result = {'status': 'complete', 'score': 0.5}
    trace = [{'minute': 1, 'tool': 'advance_time', 'observation': {'minute': 1}}]
    pilot._write_success(private, result, trace, 1)
    assert json.loads((private / 'result.json').read_text()) == result
    assert [json.loads(line) for line in (private / 'public_trajectory.jsonl').read_text().splitlines()] == trace
    sizes = result['artifact_bytes']
    assert sizes['result'] == (private / 'result.json').stat().st_size
    assert sizes['public_trajectory'] == (private / 'public_trajectory.jsonl').stat().st_size
    assert sizes['checkpoint'] == checkpoint.stat().st_size
    assert sizes['total'] == sum(sizes[key] for key in ('result', 'public_trajectory', 'checkpoint'))
    assert result['peak_rss_bytes'] > 0
    assert (private / 'result.json').stat().st_mode & 0o777 == 0o600
    assert (private / 'public_trajectory.jsonl').stat().st_mode & 0o777 == 0o600
