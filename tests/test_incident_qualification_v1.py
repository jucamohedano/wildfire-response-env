"""Fail-closed qualification reporting using a small deterministic fake engine."""
import json
from types import SimpleNamespace

import pytest

from eval import check_incident_evaluation_v1 as runner


PRIVATE_MARKER = 'PRIVATE-FUTURE-EVENT-AND-AUDIT-SENTINEL'


@pytest.fixture
def qualification(tmp_path, monkeypatch):
    pack = tmp_path / 'pack'
    data = tmp_path / 'data'
    pack.mkdir()
    data.mkdir()
    (data / 'manifest.json').write_text('{}\n')
    output = tmp_path / 'public.json'
    now = [0.0]
    settings = {'exit_error': False, 'timeout': None, 'runtime_pins': None,
                'triggered': False, 'drift': None, 'verification_failure': False}
    cases = [dict(id=f'incident-eval-v1-{i:03}', seed=8140000 + i,
                  weather_schedule=[{'private': PRIVATE_MARKER}],
                  incoming_reports=[{'text': PRIVATE_MARKER}],
                  events=[{'private': PRIVATE_MARKER}]) for i in range(12)]
    catalog = dict(version='wildfire-incident-eval-catalog-v1', cases=cases,
                   sha256='a' * 64, inputs_sha256='b' * 64, network_sha256='c' * 64)
    pins = dict(catalog_sha256=catalog['sha256'], inputs_sha256=catalog['inputs_sha256'],
                network_sha256=catalog['network_sha256'], runtime_source_sha256='d' * 64)
    lock = dict(version='wildfire-incident-eval-lock-v1',
                generator_sha256=runner._file_sha(
                    runner.Path(runner.__file__).with_name('generate_incident_evaluation_v1.py')),
                policy_sha256=runner._file_sha(
                    runner.Path(runner.__file__).with_name('incident_day_policies.py')),
                facade_sha256=runner._file_sha(
                    runner.Path(runner.__file__).with_name('validate_incident_day_runtime.py')),
                dataset_manifest_sha256=runner._file_sha(data / 'manifest.json'),
                runtime_pins=pins.copy(), case_ids=[case['id'] for case in cases])

    def save_pack():
        (pack / 'catalog.json').write_text(json.dumps(catalog))
        (pack / 'lock.json').write_text(json.dumps(lock))

    class Runtime:
        def __init__(self, value, *, expected_pins):
            self.pins = (settings['runtime_pins'] if settings['runtime_pins'] is not None
                         else pins.copy())

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if settings['exit_error']:
                # Success must not be visible even transiently before this check.
                assert_no_pass(output)
                raise RuntimeError(PRIVATE_MARKER)
            return False

    class Environment:
        def __init__(self, *args, **kwargs):
            self.minute = 0
            self.terminated = self.truncated = self.censored = self.abandoned = False
            self._input_censored = False
            self._audit = [PRIVATE_MARKER]

        def reset(self, case_id, seed):
            self.case_id = case_id
            return {'minute': 0, 'reports': []}, {}

        def verify_episode(self):
            if settings['timeout'] == 'replay' and self.case_id.endswith('011'):
                settings['triggered'] = True
                now[0] = 100.0
            return {'passed': not settings['verification_failure'], 'exact_replay': True,
                    'independent_reward_agreement': True,
                    'private_audit': PRIVATE_MARKER}

    class Policy:
        def action(self, public, observation):
            return {'advance_minutes': 60}

    def commit(public, action):
        public.minute = 1440
        public.terminated = True
        if settings['timeout'] == 'final_step' and public.case_id.endswith('011'):
            settings['triggered'] = True
            now[0] = 100.0
        if settings['drift'] and public.case_id.endswith('011'):
            settings['triggered'] = True
        return {'observation': {'minute': 1440, 'reports': [
            {'observed_at': 1, 'received_at': 1, 'text': PRIVATE_MARKER}]}}

    base = SimpleNamespace(inputs={'sha256': 'b' * 64},
                           network=SimpleNamespace(document={}, sha256='c' * 64))
    monkeypatch.setattr(runner, '_RUNNER_MATCH', True)
    monkeypatch.setattr(runner, 'verify_package', lambda _: None)
    monkeypatch.setattr(runner, 'make_environment', lambda *args, **kwargs: (base, None))
    monkeypatch.setattr(runner, 'IncidentWeatherRuntimeV1', Runtime)
    monkeypatch.setattr(runner, 'PopulationEngineV3', Environment)
    monkeypatch.setattr(runner, 'make_policy', lambda _: Policy())
    monkeypatch.setattr(runner, 'PublicFacade', lambda env, journal: env)
    monkeypatch.setattr(runner, 'commit', commit)
    monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    original_file_sha = runner._file_sha

    def file_sha(path):
        if settings['triggered'] and settings['drift'] == runner.Path(path).name:
            return '0' * 64
        return original_file_sha(path)

    monkeypatch.setattr(runner, '_file_sha', file_sha)
    save_pack()

    def run(**kwargs):
        kwargs.setdefault('expected_lock_sha256', runner._file_sha(pack / 'lock.json'))
        kwargs.setdefault('expected_catalog_sha256', catalog['sha256'])
        return runner.run(data, pack, output, wall_seconds=10, **kwargs)

    return SimpleNamespace(run=run, save_pack=save_pack, catalog=catalog, lock=lock,
                           pins=pins, settings=settings, output=output, data=data)


def report_if_written(output):
    return json.loads(output.read_text()) if output.exists() else None


def assert_no_pass(output):
    report = report_if_written(output)
    assert report is None or report['status'] != 'passed'


def test_full_pack_can_pass_and_excludes_private_data(qualification):
    result = qualification.run()
    assert result['status'] == 'passed'
    assert len(result['cases']) == 12
    assert PRIVATE_MARKER not in qualification.output.read_text()
    assert PRIVATE_MARKER not in json.dumps(result)


def test_runtime_exit_error_cannot_publish_pass(qualification):
    qualification.settings['exit_error'] = True
    with pytest.raises(RuntimeError):
        qualification.run()
    assert_no_pass(qualification.output)
    if qualification.output.exists():
        assert PRIVATE_MARKER not in qualification.output.read_text()


@pytest.mark.parametrize('phase', ['final_step', 'replay'])
def test_timeout_during_last_action_or_replay_cannot_pass(qualification, phase):
    qualification.settings['timeout'] = phase
    result = qualification.run()
    assert qualification.settings['triggered']
    assert result['status'] != 'passed'
    assert_no_pass(qualification.output)


def test_smaller_pack_cannot_be_accepted_as_full(qualification):
    qualification.catalog['cases'] = qualification.catalog['cases'][:1]
    qualification.lock['case_ids'] = qualification.lock['case_ids'][:1]
    qualification.save_pack()
    try:
        result = qualification.run()
    except ValueError:
        result = None
    assert result is None or result['status'] != 'passed'
    assert_no_pass(qualification.output)


def test_selected_subset_is_incomplete(qualification):
    result = qualification.run(limit=1)
    assert result['status'] == 'incomplete'
    assert len(result['cases']) == 1
    assert report_if_written(qualification.output)['status'] == 'incomplete'


def test_failed_replay_verification_cannot_pass(qualification):
    qualification.settings['verification_failure'] = True
    result = qualification.run()
    assert result['status'] != 'passed'
    assert all(case['status'] != 'passed' for case in result['cases'])
    assert_no_pass(qualification.output)


@pytest.mark.parametrize('source', ['manifest.json', 'lock.json',
                                    'generate_incident_evaluation_v1.py',
                                    'incident_day_policies.py',
                                    'validate_incident_day_runtime.py'])
def test_source_or_dataset_drift_during_final_action_cannot_pass(qualification, source):
    qualification.settings['drift'] = source
    with pytest.raises(ValueError):
        qualification.run()
    assert qualification.settings['triggered']
    assert_no_pass(qualification.output)


@pytest.mark.parametrize('mismatch', ['generator', 'dataset', 'case_ids', 'runtime',
                                       'policy', 'facade', 'expected_lock', 'expected_catalog'])
def test_pin_mismatch_is_rejected(qualification, mismatch):
    arguments = {}
    if mismatch == 'generator':
        qualification.lock['generator_sha256'] = '0' * 64
    elif mismatch == 'dataset':
        qualification.lock['dataset_manifest_sha256'] = '0' * 64
    elif mismatch == 'case_ids':
        qualification.lock['case_ids'][0] = 'wrong-case'
    elif mismatch == 'runtime':
        qualification.settings['runtime_pins'] = {**qualification.pins, 'catalog_sha256': '0' * 64}
    elif mismatch in ('policy', 'facade'):
        qualification.lock[f'{mismatch}_sha256'] = '0' * 64
    else:
        arguments[f'{mismatch}_sha256'] = '0' * 64
    qualification.save_pack()
    with pytest.raises(ValueError):
        qualification.run(**arguments)
    assert_no_pass(qualification.output)
