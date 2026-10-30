import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from envs.wildfire_response.incident_day import environment as day
from envs.wildfire_response.incident_day.scenarios import build_scenario
from envs.wildfire_response.regional.ops.generator import digest
from eval.incident_eval_bridge_v1 import CATALOG_VERSION, IncidentEvalBridgeV1
from eval.session_recovery_v1 import SessionRecoveryV1
from wildfire_core.incident_day import fixture_inputs
from wildfire_control_v3.control import create_population_control, interface_digest
from wildfire_control_v3.population_engine_v3 import population_engine_identity
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS


def rehash(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


@pytest.fixture(scope='module')
def fixture():
    inputs, network = fixture_inputs()
    env = day.IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    scenario = build_scenario(env.inputs, env.network, 'incident-day-egress-v1-000')
    scenario['id'] = 'bridge-v1-fixture-000'
    scenario['seed'] = 8140000
    # This bridge transports and hashes weather data; interpretation belongs to
    # a separately pinned runtime and is deliberately not assumed here.
    scenario['weather_schedule'] = [{'observed_at': 0, 'received_at': 0, 'wind': [1, 0],
                                     'temperature_c': 32, 'relative_humidity_percent': 20,
                                     'rainfall_mm_per_hour': 0}]
    scenario['private_evaluation_note'] = 'PRIVATE-CATALOG-SENTINEL'
    rehash(scenario)
    catalog = rehash(dict(version=CATALOG_VERSION, inputs_sha256=inputs['sha256'],
                          network_sha256=network['sha256'], cases=[scenario]))
    return inputs, network, catalog


def control(fixture):
    inputs, network, _ = fixture
    return create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                     expected_network_sha256=network['sha256'])


def test_reset_is_deterministic_deep_copied_and_private(fixture):
    inputs, network, catalog = fixture
    supplied = copy.deepcopy(catalog)
    bridge = IncidentEvalBridgeV1(supplied)
    supplied['cases'][0]['wind'] = [3, 3]
    original = day.build_scenario
    before = day.implementation_digest(), interface_digest()
    legacy_env = control(fixture).environment
    legacy_before = build_scenario(legacy_env.inputs, legacy_env.network, 'incident-day-egress-v1-000')
    with bridge:
        a, b = control(fixture), control(fixture)
        first, info = a.reset(catalog['cases'][0]['id'])
        second, _ = b.reset(catalog['cases'][0]['id'], catalog['cases'][0]['seed'])
        assert first == second
        assert a.environment.trajectory()['scenario_sha256'] == catalog['cases'][0]['sha256']
        assert a.environment._scenario['weather_schedule'] == catalog['cases'][0]['weather_schedule']
        assert 'PRIVATE-CATALOG-SENTINEL' not in json.dumps([first, info, a.execute('get_briefing', {})])
        assert bridge.pins['catalog_sha256'] == catalog['sha256']
        assert len(bridge.pins['bridge_source_sha256']) == 64
        bridge_path = Path(__file__).resolve().parents[1] / 'eval/incident_eval_bridge_v1.py'
        assert bridge.pins['bridge_source_sha256'] == hashlib.sha256(bridge_path.read_bytes()).hexdigest()
        a.environment._scenario['wind'][0] = -3
        assert b.environment._scenario['wind'] == catalog['cases'][0]['wind']
        legacy = control(fixture)
        legacy.reset('incident-day-egress-v1-000')
        assert legacy.environment.trajectory()['task_id'] == 'incident-day-egress-v1-000'
        assert legacy.environment._scenario == legacy_before
        with pytest.raises(ValueError):
            b.reset(catalog['cases'][0]['id'], True)
        with pytest.raises(ValueError):
            b.reset(catalog['cases'][0]['id'], catalog['cases'][0]['seed'] + 1)
    assert day.build_scenario is original
    assert (day.implementation_digest(), interface_digest()) == before
    with pytest.raises(ValueError):
        control(fixture).reset(catalog['cases'][0]['id'])


@pytest.mark.parametrize('damage', ['catalog_hash', 'case_hash', 'duplicate', 'duplicate_seed',
                                    'legacy_id', 'legacy_seed', 'bool_seed', 'nan', 'bad_version',
                                    'bad_pin', 'bad_event', 'bad_fire', 'missing'])
def test_catalog_validation_rejects_invalid_content(fixture, damage):
    catalog = copy.deepcopy(fixture[2])
    case = catalog['cases'][0]
    if damage == 'catalog_hash':
        catalog['sha256'] = '0' * 64
        with pytest.raises(ValueError):
            IncidentEvalBridgeV1(catalog)
        return
    if damage == 'case_hash':
        case['wind'] = [2, 1]
    elif damage == 'duplicate':
        catalog['cases'].append(copy.deepcopy(case))
    elif damage == 'duplicate_seed':
        second = copy.deepcopy(case)
        second['id'] = 'bridge-v1-other'
        catalog['cases'].append(rehash(second))
    elif damage == 'legacy_id':
        case['id'] = 'incident-day-egress-v1-000'
        rehash(case)
    elif damage == 'legacy_seed':
        case['seed'] = 3866400
        rehash(case)
    elif damage == 'bool_seed':
        case['seed'] = True
        rehash(case)
    elif damage == 'nan':
        case['wind'] = [float('nan'), 0]
        with pytest.raises(ValueError):
            IncidentEvalBridgeV1(catalog)
        return
    elif damage == 'bad_version':
        catalog['version'] = 'other'
    elif damage == 'bad_pin':
        catalog['network_sha256'] = 'abc'
    elif damage == 'bad_event':
        case['events'].append(dict(minute=1400, kind='unsupported'))
        rehash(case)
    elif damage == 'bad_fire':
        case['initial_fire'].append(case['initial_fire'][0][:])
        rehash(case)
    elif damage == 'missing':
        del case['resources']
        rehash(case)
    rehash(catalog)
    with pytest.raises(ValueError):
        IncidentEvalBridgeV1(catalog)


def test_mismatched_input_or_network_pins_fail_at_reset(fixture):
    for key in ('inputs_sha256', 'network_sha256'):
        catalog = copy.deepcopy(fixture[2])
        catalog[key] = 'a' * 64
        rehash(catalog)
        with IncidentEvalBridgeV1(catalog):
            with pytest.raises(ValueError):
                control(fixture).reset(catalog['cases'][0]['id'])


def test_distinct_cases_keep_weather_and_seed_separate(fixture):
    catalog = copy.deepcopy(fixture[2])
    second = copy.deepcopy(catalog['cases'][0])
    second['id'], second['seed'] = 'bridge-v1-fixture-001', 8140001
    second['weather_schedule'][0]['temperature_c'] = 39
    catalog['cases'].append(rehash(second))
    rehash(catalog)
    with IncidentEvalBridgeV1(catalog):
        first, other = control(fixture), control(fixture)
        first.reset(catalog['cases'][0]['id'])
        other.reset(second['id'])
        assert first.environment._scenario['seed'] == 8140000
        assert other.environment._scenario['seed'] == 8140001
        assert other.environment._scenario['weather_schedule'] == second['weather_schedule']
        assert first.environment.trajectory()['scenario_sha256'] != other.environment.trajectory()['scenario_sha256']


def test_nested_thread_and_exception_cleanup(fixture):
    catalog = fixture[2]
    other = copy.deepcopy(catalog)
    other['cases'][0]['id'] = 'bridge-v1-other'
    rehash(other['cases'][0])
    rehash(other)
    original = day.build_scenario
    errors = []
    with IncidentEvalBridgeV1(catalog):
        with IncidentEvalBridgeV1(copy.deepcopy(catalog)):
            with pytest.raises(RuntimeError):
                with IncidentEvalBridgeV1(other):
                    pass
            def worker():
                try:
                    control(fixture).reset(catalog['cases'][0]['id'])
                except Exception as error:
                    errors.append(error)
                try:
                    with IncidentEvalBridgeV1(catalog):
                        pass
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=10)
            assert not thread.is_alive()
            assert len(errors) == 2 and all(isinstance(e, RuntimeError) for e in errors)
    assert day.build_scenario is original
    with pytest.raises(LookupError):
        with IncidentEvalBridgeV1(catalog):
            raise LookupError('test')
    assert day.build_scenario is original


def test_dispatcher_tampering_is_detected_and_restored(fixture):
    original = day.build_scenario
    with pytest.raises(RuntimeError, match='dispatcher changed'):
        with IncidentEvalBridgeV1(fixture[2]):
            day.build_scenario = lambda *args: None
    assert day.build_scenario is original


@pytest.mark.parametrize('bridge_outermost', [True, False])
def test_independent_process_hook_contexts_compose(fixture, bridge_outermost):
    # Model an independent runtime hook without assuming its weather semantics.
    original_tick = day.IncidentDayEnv._tick
    ticks = []

    @contextmanager
    def other_runtime():
        def hooked_tick(env):
            ticks.append(env.minute)
            return original_tick(env)
        day.IncidentDayEnv._tick = hooked_tick
        try:
            yield
        finally:
            day.IncidentDayEnv._tick = original_tick

    bridge = IncidentEvalBridgeV1(fixture[2])
    outer, inner = (bridge, other_runtime()) if bridge_outermost else (other_runtime(), bridge)
    original_builder = day.build_scenario
    with outer:
        with inner:
            c = control(fixture)
            c.reset(fixture[2]['cases'][0]['id'])
            c.execute('advance', {'max_minutes': 1})
            assert ticks
    assert day.IncidentDayEnv._tick is original_tick
    assert day.build_scenario is original_builder


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork is unavailable')
def test_fork_inherits_no_authorized_bridge(fixture):
    catalog = fixture[2]
    with IncidentEvalBridgeV1(catalog):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                control(fixture).reset(catalog['cases'][0]['id'])
            except RuntimeError:
                os.write(write_fd, b'rejected')
            else:
                os.write(write_fd, b'accepted')
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        result = os.read(read_fd, 20)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert result == b'rejected'


def test_v3_checkpoint_restore_and_replay(fixture):
    catalog = fixture[2]
    with IncidentEvalBridgeV1(catalog) as bridge:
        a = control(fixture)
        a.reset(catalog['cases'][0]['id'])
        a.execute('advance', {'max_minutes': 2})
        checkpoint = a.private_checkpoint()
        b = control(fixture)
        assert bridge.restore_control(b, checkpoint) == a.environment.get_situation()
        assert b.execute('advance', {'max_minutes': 2}) == a.execute('advance', {'max_minutes': 2})
        corrupt = copy.deepcopy(checkpoint)
        corrupt['engine_checkpoint']['core']['state']['_scenario']['wind'][0] = 3
        with pytest.raises(ValueError, match='pinned catalog'):
            bridge.restore_control(b, corrupt)
        # A valid legacy checkpoint must also be refused by the bridge gate.
        legacy = control(fixture)
        legacy.reset('incident-day-egress-v1-000')
        with pytest.raises(ValueError, match='pinned catalog'):
            bridge.restore_control(b, legacy.private_checkpoint())
        identity = {k: a.environment.trajectory()[k] for k in IDENTITY_FIELDS}
        assert identity['scenario_sha256'] == catalog['cases'][0]['sha256']
        for _ in range(3):
            result = a.execute('commit_orders', {'action': {'unknown': []}})
        assert result['truncated']
        verified = a.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
        assert verified['verified'] and verified['status'] == 'invalid_abandonment'


def test_real_full_day_v3_exact_replay(fixture):
    catalog = fixture[2]
    case = catalog['cases'][0]
    identity = dict(interface_version='wildfire-incident-day-population-v3', task_id=case['id'],
                    seed=case['seed'], inputs_sha256=catalog['inputs_sha256'],
                    network_sha256=catalog['network_sha256'], scenario_sha256=case['sha256'],
                    implementation_sha256=population_engine_identity())
    with IncidentEvalBridgeV1(catalog):
        c = control(fixture)
        c.reset(case['id'])
        assert {k: c.environment.trajectory()[k] for k in IDENTITY_FIELDS} == identity
        for _ in range(1500):
            result = c.execute('advance', {'max_minutes': 60})
            if result['terminated'] or result['truncated']:
                break
        else:
            pytest.fail('day did not finish')
        assert result['terminated'] and c.environment.minute == 1440
        verified = c.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
        assert verified['verified'] and verified['status'] == 'full_day'
        assert verified['score_eligible']


def test_session_recovery_on_fresh_control(fixture, tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    baseline_dir = tmp_path / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    catalog = fixture[2]
    with IncidentEvalBridgeV1(catalog):
        first = control(fixture)
        first.reset(catalog['cases'][0]['id'])
        session = SessionRecoveryV1(first, directory, run_identity='bridge-test',
                                    policy_name='passive', evaluation={})
        session.initialize()
        expected = session.step(1)
        baseline_control = control(fixture)
        baseline_control.reset(catalog['cases'][0]['id'])
        baseline = SessionRecoveryV1(baseline_control, baseline_dir, run_identity='baseline',
                                     policy_name='passive', evaluation={})
        baseline.initialize()
        assert baseline.step(1) == expected
        fresh = control(fixture)
        fresh.reset(catalog['cases'][0]['id'])
        resumed = SessionRecoveryV1(fresh, directory, run_identity='bridge-test',
                                    policy_name='passive', evaluation={})
        assert resumed.recover() == expected
        assert resumed.step(1) == expected
        assert resumed.step(2) == baseline.step(2)


def weather_catalog(fixture):
    catalog = copy.deepcopy(fixture[2])
    case = catalog['cases'][0]
    case['weather_schedule'] = [
        dict(minute=minute, wind=wind, temperature_c=temperature,
             relative_humidity_pct=25, rain_mm_per_hour=0)
        for minute, wind, temperature in ((0, [1, 0], 30), (240, [0, 1], 31),
                                          (600, [-1, 0], 32), (1020, [0, -1], 33))]
    rehash(case)
    rehash(catalog)
    return catalog


@pytest.mark.parametrize('bridge_outermost', [True, False])
def test_weather_runtime_composes_and_replays(fixture, bridge_outermost):
    # The separate runtime owns this schema and its weather source pins.
    IncidentWeatherRuntimeV1 = pytest.importorskip('eval.incident_weather_runtime_v1').IncidentWeatherRuntimeV1
    catalog = weather_catalog(fixture)
    case = catalog['cases'][0]
    expected = IncidentWeatherRuntimeV1(catalog, expected_pins={}).pins
    runtime = IncidentWeatherRuntimeV1(catalog, expected_pins=expected)
    bridge = IncidentEvalBridgeV1(catalog)
    outer, inner = (bridge, runtime) if bridge_outermost else (runtime, bridge)
    with outer:
        with inner:
            assert runtime.pins['catalog_sha256'] == bridge.pins['catalog_sha256']
            assert runtime.pins['runtime_source_sha256'] != bridge.pins['bridge_source_sha256']
            c = control(fixture)
            observation, _ = c.reset(case['id'])
            assert 'weather_schedule' not in json.dumps(observation)
            assert 'PRIVATE-CATALOG-SENTINEL' not in json.dumps(observation)
            identity = {k: c.environment.trajectory()[k] for k in IDENTITY_FIELDS}
            c.execute('advance', {'max_minutes': 2})
            restored = control(fixture)
            runtime.restore_control(restored, c.private_checkpoint())
            assert restored.execute('advance', {'max_minutes': 2}) == c.execute('advance', {'max_minutes': 2})
            for _ in range(3):
                c.execute('commit_orders', {'action': {'unknown': []}})
            result = c.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
            assert result['verified'] and result['status'] == 'invalid_abandonment'


def test_weather_runtime_full_day_v3_replay(fixture):
    IncidentWeatherRuntimeV1 = pytest.importorskip('eval.incident_weather_runtime_v1').IncidentWeatherRuntimeV1
    catalog = weather_catalog(fixture)
    case = catalog['cases'][0]
    expected = IncidentWeatherRuntimeV1(catalog, expected_pins={}).pins
    identity = dict(interface_version='wildfire-incident-day-population-v3', task_id=case['id'],
                    seed=case['seed'], inputs_sha256=catalog['inputs_sha256'],
                    network_sha256=catalog['network_sha256'], scenario_sha256=case['sha256'],
                    implementation_sha256=population_engine_identity())
    with IncidentWeatherRuntimeV1(catalog, expected_pins=expected):
        c = control(fixture)
        c.reset(case['id'])
        for _ in range(1500):
            result = c.execute('advance', {'max_minutes': 60})
            if result['terminated'] or result['truncated']:
                break
        else:
            pytest.fail('weather day did not finish')
        assert result['terminated'] and c.environment.minute == 1440
        verified = c.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
        assert verified['verified'] and verified['status'] == 'full_day'
