import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_containment_v1/src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_containment_weather_v1/src'))
from envs.wildfire_response.incident_day import environment as day
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.scenarios import build_scenario
from envs.wildfire_response.regional.ops.generator import digest
from eval.incident_eval_bridge_v1 import CATALOG_VERSION
from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1
from wildfire_control_v3.control import create_population_control
from wildfire_core.incident_day import fixture_inputs
from wildfire_containment_weather_v1.weather import ContainmentWeatherOwner


CONFIG = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, wear_factor=1, report_delay=2)


def rehash(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


@pytest.fixture
def scenario():
    inputs, network = fixture_inputs()
    env = day.IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    case = build_scenario(env.inputs, env.network, 'incident-day-egress-v1-000')
    case.update(id='weather-runtime-test-000', seed=8140000, warmup_minutes=60,
                private_evaluation_note='PRIVATE-SENTINEL', weather_schedule=[
                    dict(minute=0, wind=[1., 0.], temperature_c=27, relative_humidity_pct=40, rain_mm_per_hour=0, received_at=0),
                    dict(minute=20, wind=[0., 1.], temperature_c=35, relative_humidity_pct=20, rain_mm_per_hour=0, received_at=80),
                    dict(minute=25, wind=[-1., 0.], temperature_c=18, relative_humidity_pct=85, rain_mm_per_hour=8, received_at=25)],
                incoming_reports=[dict(minute=3, source='watch', role='responder', channel='radio',
                                       location=None, text='DELAYED-SENTINEL', delay=10, category='late')])
    case['events'] = [e for e in case['events'] if e['kind'] != 'wind']
    case['events'] += [dict(minute=20, kind='wind', wind=[0., 1.]),
                       dict(minute=25, kind='wind', wind=[-1., 0.])]
    case['events'].sort(key=lambda e: e['minute'])
    rehash(case)
    catalog = rehash(dict(version=CATALOG_VERSION, inputs_sha256=inputs['sha256'],
                          network_sha256=network['sha256'], cases=[case]))
    return env.inputs, inputs, network, case, catalog


def make(scenario):
    return ContainmentWeatherOwner(scenario[0], scenario[3], containment_config=CONFIG, pressure_scale=1)


def test_fixture_parity_with_existing_weather_runtime(scenario):
    inputs, original_inputs, network, case, catalog = scenario
    owner = make(scenario)
    snapshots = {}
    for minute in range(31):
        if minute:
            owner.step()
        if minute in (0, 19, 20, 25, 30):
            cp = owner.checkpoint()
            snapshots[minute] = (cp['fire']['fire'], cp['state'])
    draft = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=draft.pins):
        control = create_population_control(original_inputs, network,
                expected_inputs_sha256=original_inputs['sha256'], expected_network_sha256=network['sha256'])
        control.reset(case['id'])
        for minute in range(31):
            if minute:
                control.execute('advance', {'max_minutes': 1})
            if minute in snapshots:
                reference = control.environment.fire.checkpoint()
                state = reference['state'].pop('_weather_runtime')['state']
                actual = snapshots[minute][0]
                if minute == 0:
                    assert json.loads(json.dumps(reference)) == json.loads(json.dumps(actual))
                assert reference['state']['wind'] == actual['state']['wind']
                assert reference['state']['rules']['spread_factor'] == actual['state']['rules']['spread_factor']
                assert state == snapshots[minute][1]


def test_delayed_causal_delivery_private_checkpoint_and_replay(scenario):
    owner = make(scenario)
    for _ in range(20):
        owner.step()
    assert owner._fire.wind == (0., 1.) or list(owner._fire.wind) == [0., 1.]
    assert [r['observed_at'] for r in owner.reports() if r['kind'] == 'weather_update'] == [0]
    snapshot = json.loads(json.dumps(owner.checkpoint(), allow_nan=False))
    clone = ContainmentWeatherOwner.restore(scenario[0], scenario[3], containment_config=CONFIG,
                                             pressure_scale=1, checkpoint=snapshot)
    for _ in range(65):
        assert owner.step() == clone.step()
    reports = [r for r in owner.reports() if r['kind'] == 'weather_update']
    assert [(r['observed_at'], r['received_at'], r['actionable']) for r in reports] == [
        (0, 0, True), (25, 25, True), (20, 80, False)]
    assert json.loads(json.dumps(owner.checkpoint())) == json.loads(json.dumps(clone.checkpoint()))
    before = owner.checkpoint()
    text = json.dumps(owner.reports())
    assert 'PRIVATE-SENTINEL' not in text and 'moisture_proxy' not in text and 'sha256' not in text
    incoming = next(r for r in owner.reports() if r['kind'] == 'incoming_report')
    assert (incoming['observed_at'], incoming['received_at']) == (3, 13)
    assert incoming['payload'] == {'text': 'DELAYED-SENTINEL'} and 'category' not in incoming
    owner.reports()[0]['payload']['wind'][0] = 999
    assert owner.checkpoint() == before
    tampered = copy.deepcopy(snapshot)
    tampered['state']['minute'] += 1
    with pytest.raises(ValueError, match='integrity'):
        ContainmentWeatherOwner.restore(scenario[0], scenario[3], containment_config=CONFIG,
                                        pressure_scale=1, checkpoint=tampered)


def test_isolation_invalid_wind_and_interruption(scenario):
    before = (day.DayFire, day.IncidentDayEnv.reset, day.IncidentDayEnv._tick, EventBus.emit, EventBus.tick)
    owner = make(scenario)
    checkpoint = owner.checkpoint()
    with pytest.raises(ValueError):
        owner.step(assignments=[{'resource_id': 'bad'}])
    with pytest.raises(RuntimeError, match='restore or reset'):
        owner.checkpoint()
    restored = ContainmentWeatherOwner.restore(scenario[0], scenario[3], containment_config=CONFIG,
                                                pressure_scale=1, checkpoint=checkpoint)
    assert restored.checkpoint() == checkpoint
    bad = copy.deepcopy(scenario[3])
    bad['events'] = [e for e in bad['events'] if e['kind'] != 'wind']
    with pytest.raises(ValueError, match='Wind events'):
        ContainmentWeatherOwner(scenario[0], bad, containment_config=CONFIG, pressure_scale=1)
    assert before == (day.DayFire, day.IncidentDayEnv.reset, day.IncidentDayEnv._tick, EventBus.emit, EventBus.tick)


def test_fixture_hold_work_uses_weather_fire(scenario):
    owner = make(scenario)
    assignment = dict(resource_id='crew-1', edge=[[10, 10], [11, 10]], kind='crew', ready=True,
                      work_remaining=10, water_litres=0, water_cost=0)
    result = owner.step(assignments=[assignment])
    assert result['costs'] == [dict(resource_id='crew-1', work_minutes=1, water_litres=0)]
    saved = owner.checkpoint()
    assert saved['fire']['containment']['minute'] == saved['state']['minute'] == 1
    assert saved['fire']['containment']['lines'][0]['edge'] == assignment['edge']


def test_fresh_driver_replays_identical_hold_and_weather(scenario):
    first, replay = make(scenario), make(scenario)
    assignment = dict(resource_id='crew-1', edge=[[10, 10], [11, 10]], kind='crew', ready=True,
                      work_remaining=10, water_litres=0, water_cost=0)
    for minute in range(1, 31):
        actions = [assignment] if minute <= 2 else []
        assert first.step(assignments=actions) == replay.step(assignments=actions)
    assert json.loads(json.dumps(first.checkpoint())) == json.loads(json.dumps(replay.checkpoint()))


def incident(scenario):
    from wildfire_containment_weather_v1.incident_day import ContainmentWeatherIncidentDayEnv
    return ContainmentWeatherIncidentDayEnv(scenario[1], scenario[2],
        expected_inputs_sha256=scenario[1]['sha256'], expected_network_sha256=scenario[2]['sha256'],
        containment_config=CONFIG, pressure_scale=1, weather_catalog=scenario[4],
        expected_catalog_sha256=scenario[4]['sha256'])


def test_incident_weather_reports_restore_and_fresh_replay(scenario):
    env = incident(scenario)
    obs, _ = env.reset(scenario[3]['id'])
    assert obs['wind'] == [1., 0.]
    assert 'PRIVATE-SENTINEL' not in json.dumps(obs) and 'DELAYED-SENTINEL' not in json.dumps(obs)
    while env.minute < 20:
        env.advance_time(1)
    assert list(env.fire.wind) == [0., 1.] and env.get_situation()['wind'] == [1., 0.]
    checkpoint = json.loads(json.dumps(env.checkpoint()))
    resumed = incident(scenario)
    restored_view = resumed.restore(checkpoint)
    assert env.get_situation() == resumed.get_situation() == restored_view
    for _ in range(65):
        assert env.advance_time(1) == resumed.advance_time(1)
    records = [r for r in env.bus.reports if r['kind'] == 'weather_update']
    assert [(r['observed_at'], r['received_at'], r['actionable']) for r in records] == [
        (0, 0, True), (25, 25, True), (20, 80, False)]
    assert env.get_situation()['wind'] == resumed.get_situation()['wind'] == [-1., 0.]
    assert not any(r['kind'] == 'wind_update' for r in env.bus.reports)
    assert 'PRIVATE-SENTINEL' not in json.dumps(env.trajectory())
    fresh = incident(scenario)
    fresh.replay_transcript(env.trajectory(), **env.private_replay_pins())
    assert fresh.checkpoint() == env.checkpoint() == resumed.checkpoint()


def test_full_day_weather_with_authorized_hold_and_verification(scenario):
    env = incident(scenario)
    env.reset(scenario[3]['id'])
    options = env.route_options('engine-1')['options']
    option = options[1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=option['id'])]})
    while not env.get_situation()['offers']:
        assert env.minute < 50
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    proposal = env.propose_hold(offer['id'])[1]
    env.authorize_hold(offer['id'], proposal['id'])
    while not env.terminated and not env.truncated:
        env.advance_time(60)
    assert env.terminated and not env.truncated and env.minute == 1440
    assert env._containment_audit
    result = env.verify_episode()
    assert result['exact_replay'] and result['independent_reward_agreement']
    public = json.dumps(env.trajectory())
    assert 'PRIVATE-SENTINEL' not in public and 'moisture_proxy' not in public
    assert 'weather_schedule' not in public and 'category' not in public


def test_incident_matches_existing_weather_runtime_without_holds(scenario):
    env = incident(scenario)
    env.reset(scenario[3]['id'])
    snapshots = {}
    for minute in range(31):
        if minute:
            env.advance_time(1)
        if minute in (0, 19, 20, 25, 30):
            snapshots[minute] = (env.fire.checkpoint()['fire'], copy.deepcopy(env._weather._state),
                                 [r for r in env.bus.reports if r['kind'] == 'weather_update'])
    draft = IncidentWeatherRuntimeV1(scenario[4], expected_pins={})
    with IncidentWeatherRuntimeV1(scenario[4], expected_pins=draft.pins):
        control = create_population_control(scenario[1], scenario[2],
            expected_inputs_sha256=scenario[1]['sha256'], expected_network_sha256=scenario[2]['sha256'])
        control.reset(scenario[3]['id'])
        for minute in range(31):
            if minute:
                control.execute('advance', {'max_minutes': 1})
            if minute in snapshots:
                reference = control.environment.fire.checkpoint()
                state = reference['state'].pop('_weather_runtime')['state']
                actual, actual_state, reports = snapshots[minute]
                assert json.loads(json.dumps(actual)) == json.loads(json.dumps(reference))
                assert state == actual_state
                assert reports == [r for r in control.environment.bus.reports if r['kind'] == 'weather_update']


def test_interrupted_step_restore_and_catalog_pin(scenario, monkeypatch):
    from wildfire_containment_v1.fire import ContainmentDayFire
    env = incident(scenario)
    env.reset(scenario[3]['id'])
    saved = env.checkpoint()
    def fail(*args, **kwargs):
        raise RuntimeError('injected interruption')
    with monkeypatch.context() as patch:
        patch.setattr(ContainmentDayFire, 'step_with_containment', fail)
        with pytest.raises(RuntimeError, match='injected interruption'):
            env.advance_time(1)
        assert not env._transcript_valid
        with pytest.raises(RuntimeError, match='valid reset transcript'):
            env.trajectory()
    env.restore(saved)
    other = incident(scenario)
    other.restore(saved)
    assert env.advance_time(1) == other.advance_time(1)
    pins = env.private_replay_pins()
    pins['expected_catalog_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='pins'):
        incident(scenario).replay_transcript(env.trajectory(), **pins)
    bad = copy.deepcopy(saved)
    bad['weather']['minute'] = 10
    bad['sha256'] = digest({k: v for k, v in bad.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='clocks'):
        incident(scenario).restore(bad)


def test_original_v10_checkpoint_survives_weather_episode(scenario):
    from wildfire_containment_v1.incident_day import ContainmentIncidentDayEnv, VERSION
    kwargs = dict(expected_inputs_sha256=scenario[1]['sha256'], expected_network_sha256=scenario[2]['sha256'],
                  containment_config=CONFIG, pressure_scale=1)
    legacy = ContainmentIncidentDayEnv(scenario[1], scenario[2], **kwargs)
    legacy.reset('incident-day-dev-000')
    saved = json.loads(json.dumps(legacy.checkpoint()))
    assert saved['version'] == VERSION and legacy.trajectory()['adapter_version'] == VERSION
    weather_env = incident(scenario)
    weather_env.reset(scenario[3]['id'])
    weather_env.advance_time(2)
    recovered = ContainmentIncidentDayEnv(scenario[1], scenario[2], **kwargs)
    recovered.restore(saved)
    assert recovered.checkpoint() == saved
    assert legacy.advance_time(3) == recovered.advance_time(3)
    assert weather_env.checkpoint()['version'] != VERSION
