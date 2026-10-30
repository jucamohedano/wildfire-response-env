import copy
import json
import threading

import pytest

from envs.wildfire_response.incident_day import environment as day
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.scenarios import build_scenario
from envs.wildfire_response.regional.ops.generator import digest
from eval import incident_weather_v1 as weather
from eval.incident_eval_bridge_v1 import CATALOG_VERSION
from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1, WeatherDayFire
from wildfire_core.incident_day import fixture_inputs
from wildfire_control_v3.control import create_population_control, interface_digest
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS


def rehash(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


@pytest.fixture(scope='module')
def setup():
    inputs, network = fixture_inputs()
    env = day.IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
    case = build_scenario(env.inputs, env.network, 'incident-day-egress-v1-000')
    case.update(id='weather-runtime-test-000', seed=8140000, warmup_minutes=60,
                private_evaluation_note='PRIVATE-WEATHER-SENTINEL',
                incoming_reports=[
                    dict(minute=3, source='watch', role='responder', channel='radio', location=[2, 3], text='Smoke seen', delay=15, category='true'),
                    dict(minute=4, source='caller', role='resident', channel='phone', location=None, text='Unclear smoke', delay=0, category='ambiguous'),
                    dict(minute=7, source='watch', role='responder', channel='radio', location=[2, 3], text='Smoke seen', delay=0, category='duplicate'),
                    dict(minute=40, source='watch', role='responder', channel='radio', location=None, text='FUTURE-REPORT-SENTINEL', delay=10, category='late'),
                ],
                weather_schedule=[
                    dict(minute=0, wind=[1., 0.], temperature_c=27, relative_humidity_pct=40, rain_mm_per_hour=0),
                    dict(minute=20, wind=[0., 1.], temperature_c=35, relative_humidity_pct=20, rain_mm_per_hour=0),
                    dict(minute=25, wind=[0., 1.], temperature_c=18, relative_humidity_pct=85, rain_mm_per_hour=8),
                ])
    case['events'] = [event for event in case['events'] if event['kind'] != 'wind']
    case['events'].append(dict(minute=20, kind='wind', wind=[0., 1.]))
    case['events'].sort(key=lambda x: x['minute'])
    rehash(case)
    catalog = rehash(dict(version=CATALOG_VERSION, inputs_sha256=inputs['sha256'],
                          network_sha256=network['sha256'], cases=[case]))
    return inputs, network, catalog


def control(setup):
    inputs, network, _ = setup
    return create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                     expected_network_sha256=network['sha256'])


def runtime(catalog):
    draft = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    return IncidentWeatherRuntimeV1(catalog, expected_pins=draft.pins)


def test_warmup_reporting_and_recovery(setup):
    catalog = setup[2]
    with runtime(catalog) as context:
        a = control(setup)
        observation, _ = a.reset(catalog['cases'][0]['id'])
        fire = a.environment.fire
        assert isinstance(fire, WeatherDayFire)
        assert fire.tick == 0
        assert fire._weather_runtime['state']['minute'] == 0
        warm = weather.WeatherProfile.from_records([catalog['cases'][0]['weather_schedule'][0]])
        expected = weather.advance(warm, weather.initial_state(warm), 60)
        assert fire._weather_runtime['state']['moisture_proxy'] == expected['moisture_proxy']
        assert 0.25 <= fire.rules['spread_factor'] <= 4
        reports = [r for r in observation['reports'] if r['kind'] == 'weather_update']
        assert len(reports) == 1 and reports[0]['observed_at'] == reports[0]['received_at'] == 0
        assert reports[0]['payload']['temperature_c'] == 27
        assert 'PRIVATE-WEATHER-SENTINEL' not in json.dumps(observation)
        assert 'FUTURE-REPORT-SENTINEL' not in json.dumps(observation)
        filtered = []
        original_emit = a.environment.bus.emit
        def spy(source, kind, payload, minute, **kwargs):
            filtered.append(kind)
            return original_emit(source, kind, payload, minute, **kwargs)
        a.environment.bus.emit = spy
        while a.environment.minute < 20:
            a.execute('advance', {'max_minutes': 1})
        reports = a.environment.bus.reports
        incoming = [r for r in reports if r['kind'] == 'incoming_report']
        assert [(r['observed_at'], r['received_at']) for r in incoming] == [(4, 4), (7, 7), (3, 18)]
        assert all(set(r['payload']) == {'text'} for r in incoming)
        assert [r['text'] for r in incoming].count('Smoke seen') == 2
        assert 'incoming_report' in filtered
        assert 'FUTURE-REPORT-SENTINEL' not in json.dumps(a.environment.get_situation())
        assert not any(r['kind'] == 'wind_update' for r in reports)
        assert not any(r['kind'] == 'weather_update' and r['observed_at'] == 20 for r in reports)
        queued = a.environment.bus.checkpoint()['queue']
        assert any(q['record']['kind'] == 'weather_update' and q['record']['observed_at'] == 20 for q in queued if 'record' in q)
        checkpoint = json.loads(json.dumps(a.private_checkpoint(), allow_nan=False))
        b = control(setup)
        assert context.restore_control(b, checkpoint) == a.environment.get_situation()
        for _ in range(15):
            assert a.execute('advance', {'max_minutes': 1}) == b.execute('advance', {'max_minutes': 1})
        reports = [r for r in a.environment.bus.reports if r['kind'] == 'weather_update']
        assert [r['observed_at'] for r in reports] == [0, 20, 25]
        assert all(r['received_at'] >= r['observed_at'] for r in reports)
        assert 'PRIVATE-WEATHER-SENTINEL' not in json.dumps(a.execute('get_briefing', {}))
        assert json.loads(json.dumps(a.environment.fire.checkpoint())) == json.loads(json.dumps(b.environment.fire.checkpoint()))


def test_context_legacy_pins_validation_and_cleanup(setup):
    catalog = setup[2]
    before = (day.implementation_digest(), interface_digest(), day.DayFire,
              day.IncidentDayEnv.reset, day.IncidentDayEnv._tick, day.IncidentDayEnv.restore, EventBus.tick, EventBus.emit)
    legacy = control(setup)
    legacy.reset('incident-day-egress-v1-000')
    old = digest(legacy.environment.checkpoint())
    assert old == 'b456b7df1fdf1a47613863150b89a84b3ca49f052526257e380934ed8e37b89b'
    assert before[0] == 'af4adc0b2fb92282246411dbac83c1eeac23eb3b4730200c0b34dc288b59323a'
    assert before[1] == '0614e35aec499997d68a720b7b8e0889742457e254b7e87d3c4e3ff7d7cc5454'
    with pytest.raises(LookupError):
        with runtime(catalog):
            c = control(setup)
            c.reset('incident-day-egress-v1-000')
            assert digest(c.environment.checkpoint()) == old
            with pytest.raises(RuntimeError, match='Nested'):
                with runtime(catalog):
                    pass
            errors = []
            def worker():
                try:
                    c.reset('incident-day-egress-v1-000')
                except Exception as exc:
                    errors.append(exc)
            t = threading.Thread(target=worker)
            t.start(); t.join()
            assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
            raise LookupError('cleanup')
    assert before == (day.implementation_digest(), interface_digest(), day.DayFire,
                      day.IncidentDayEnv.reset, day.IncidentDayEnv._tick, day.IncidentDayEnv.restore, EventBus.tick, EventBus.emit)
    assert c.execute('advance', {'max_minutes': 20}) == legacy.execute('advance', {'max_minutes': 20})
    bad = copy.deepcopy(catalog)
    duplicate = copy.deepcopy(bad['cases'][0]); duplicate['id'] = 'another-case'; rehash(duplicate)
    bad['cases'].append(duplicate); rehash(bad)
    with pytest.raises(ValueError, match='seed'):
        runtime(bad)
    bad = copy.deepcopy(catalog); bad['cases'][0]['seed'] = 2844000; rehash(bad['cases'][0]); rehash(bad)
    with pytest.raises(ValueError, match='seed'):
        runtime(bad)
    bad = copy.deepcopy(catalog); bad['cases'][0]['events'] = []; rehash(bad['cases'][0]); rehash(bad)
    with pytest.raises(ValueError, match='Wind events'):
        runtime(bad)
    with pytest.raises(ValueError, match='pins'):
        with IncidentWeatherRuntimeV1(catalog, expected_pins={}):
            pass


def test_full_day_exact_replay(setup):
    with runtime(setup[2]):
        c = control(setup)
        c.reset(setup[2]['cases'][0]['id'])
        identity = {key: c.environment.trajectory()[key] for key in IDENTITY_FIELDS}
        for _ in range(1500):
            result = c.execute('advance', {'max_minutes': 60})
            if result['terminated'] or result['truncated']:
                break
        assert result['terminated'] and c.environment.minute == 1440
        verdict = c.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
        assert verdict['verified'] and verdict['status'] == 'full_day'


@pytest.mark.parametrize('warmup', [0, 120, 240])
def test_warmup_clocks_and_preflight_state(setup, warmup):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    case['warmup_minutes'] = warmup
    rehash(case); rehash(catalog)
    with runtime(catalog):
        a, b = control(setup), control(setup)
        a.reset(case['id'])
        b.reset(case['id'])
        left, right = a.environment.fire, b.environment.fire
        assert json.loads(json.dumps(left.checkpoint())) == json.loads(json.dumps(right.checkpoint()))
        warm = weather.WeatherProfile.from_records([case['weather_schedule'][0]])
        expected = weather.advance(warm, weather.initial_state(warm), warmup)
        assert left._weather_runtime['state']['moisture_proxy'] == expected['moisture_proxy']
        assert left._weather_runtime['state']['minute'] == left.tick == 0
        a.execute('advance', {'max_minutes': 1})
        assert left._weather_runtime['state']['minute'] == left.tick == 1


def test_incoming_report_queued_recovery_and_private_categories(setup):
    with runtime(setup[2]) as ctx:
        a = control(setup)
        a.reset(setup[2]['cases'][0]['id'])
        while a.environment.minute < 42:
            a.execute('advance', {'max_minutes': 1})
        public = json.dumps(a.environment.get_situation())
        assert 'FUTURE-REPORT-SENTINEL' not in public
        queued = a.environment.bus.checkpoint()['queue']
        assert any(q.get('record', {}).get('text') == 'FUTURE-REPORT-SENTINEL' for q in queued)
        cp = json.loads(json.dumps(a.private_checkpoint()))
        b = control(setup)
        ctx.restore_control(b, cp)
        for _ in range(12):
            assert a.execute('advance', {'max_minutes': 1}) == b.execute('advance', {'max_minutes': 1})
        found = [r for r in a.environment.bus.reports if r['text'] == 'FUTURE-REPORT-SENTINEL']
        assert len(found) == 1 and found[0]['observed_at'] == 40 and found[0]['received_at'] == 50
        assert set(found[0]['payload']) == {'text'}
        assert 'category' not in json.dumps(found[0])
        assert a.environment._population_evidence == b.environment._population_evidence


def test_reject_invalid_reports_and_missing_weather_checkpoint(setup):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    case['incoming_reports'][0]['delay'] = 61
    rehash(case); rehash(catalog)
    with pytest.raises(ValueError, match='incoming report'):
        runtime(catalog)
    with runtime(setup[2]) as ctx:
        a = control(setup)
        a.reset(setup[2]['cases'][0]['id'])
        cp = a.private_checkpoint()
        del cp['engine_checkpoint']['core']['state']['fire']['state']['_weather_runtime']
        with pytest.raises(ValueError, match='weather'):
            ctx.restore_control(control(setup), cp)


def test_weather_changes_factor_and_context_cannot_reuse_old_fire(setup):
    dry = copy.deepcopy(setup[2])
    wet = copy.deepcopy(setup[2])
    for catalog, temp, humidity, rain in ((dry, 40, 10, 0), (wet, 10, 90, 20)):
        first = catalog['cases'][0]['weather_schedule'][0]
        first.update(temperature_c=temp, relative_humidity_pct=humidity, rain_mm_per_hour=rain)
        rehash(catalog['cases'][0]); rehash(catalog)
    with runtime(dry):
        a = control(setup)
        a.reset(dry['cases'][0]['id'])
        dry_state = a.environment.fire._weather_runtime['state']['moisture_proxy']
        dry_factor = a.environment.fire.rules['spread_factor']
    with runtime(wet):
        b = control(setup)
        b.reset(wet['cases'][0]['id'])
        wet_state = b.environment.fire._weather_runtime['state']['moisture_proxy']
        wet_factor = b.environment.fire.rules['spread_factor']
        assert wet_state > dry_state and wet_factor < dry_factor
        with pytest.raises(RuntimeError, match='different weather catalog'):
            a.environment.fire.step()
        response = a.execute('advance', {'max_minutes': 1})
        assert response['error'] and a.environment.minute == 0


def single_condition_step(row, moisture):
    row = copy.deepcopy(row)
    row.update(minute=0, received_at=0)
    profile = weather.WeatherProfile.from_records([row])
    return weather.advance(profile, weather.initial_state(profile, moisture_proxy=moisture), 1)


def test_scheduled_weather_affects_the_same_fire_tick(setup, monkeypatch):
    from envs.wildfire_response.incident_day.scenarios import DayFire

    seen = []
    original = DayFire.step
    def inspect_step(fire, *args, **kwargs):
        seen.append((fire.tick + 1, tuple(fire.wind), fire.rules['spread_factor']))
        return original(fire, *args, **kwargs)

    monkeypatch.setattr(DayFire, 'step', inspect_step)
    with runtime(setup[2]):
        c = control(setup)
        c.reset(setup[2]['cases'][0]['id'])
        seen.clear()
        case = setup[2]['cases'][0]
        for target, row_index in [(20, 1), (25, 2)]:
            while c.environment.minute < target - 1:
                c.execute('advance', {'max_minutes': 1})
            before = c.environment.fire._weather_runtime['state']['moisture_proxy']
            expected = single_condition_step(case['weather_schedule'][row_index], before)
            old_weather = single_condition_step(case['weather_schedule'][row_index - 1], before)
            assert expected['moisture_proxy'] != old_weather['moisture_proxy']
            c.execute('advance', {'max_minutes': 1})
            fire = c.environment.fire
            assert fire.tick == target
            assert fire._weather_runtime['state']['moisture_proxy'] == expected['moisture_proxy']
            assert seen[-1] == (target, tuple(case['weather_schedule'][row_index]['wind']),
                                weather.spread_factor(expected, base_factor=.5))


def test_explicit_receipts_out_of_order_and_checkpoint_replay(setup):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    case['weather_schedule'][0]['received_at'] = 0
    case['weather_schedule'][1]['received_at'] = 80
    case['weather_schedule'][2].update(wind=[-1., 0.], received_at=25)
    case['events'].append(dict(minute=25, kind='wind', wind=[-1., 0.]))
    case['events'].sort(key=lambda event: event['minute'])
    rehash(case); rehash(catalog)
    with runtime(catalog) as ctx:
        a = control(setup)
        obs, _ = a.reset(case['id'])
        assert [(r['observed_at'], r['received_at']) for r in obs['reports']
                if r['kind'] == 'weather_update'] == [(0, 0)]
        while a.environment.minute < 20:
            a.execute('advance', {'max_minutes': 1})
        assert list(a.environment.fire.wind) == [0., 1.]
        assert a.environment.get_situation()['wind'] == [1., 0.]
        assert not any(r['kind'] == 'weather_update' and r['observed_at'] == 20
                       for r in a.environment.bus.reports)
        assert not any(q.get('record', {}).get('observed_at') == 25
                       and q.get('record', {}).get('kind') == 'weather_update'
                       for q in a.environment.bus.checkpoint()['queue'])
        checkpoint = json.loads(json.dumps(a.private_checkpoint()))
        b = control(setup)
        ctx.restore_control(b, checkpoint)
        for _ in range(60):
            assert a.execute('advance', {'max_minutes': 1}) == b.execute('advance', {'max_minutes': 1})
        reports = [r for r in a.environment.bus.reports if r['kind'] == 'weather_update']
        assert [(r['observed_at'], r['received_at']) for r in reports] == [(0, 0), (25, 25), (20, 80)]
        assert a.environment.get_situation()['wind'] == [-1., 0.]
        assert b.environment.get_situation()['wind'] == [-1., 0.]
        assert a.environment.fire._weather_runtime == b.environment.fire._weather_runtime


@pytest.mark.parametrize('index,receipt', [(0, 1), (1, 19), (1, 81), (1, True)])
def test_reject_invalid_weather_receipt(setup, index, receipt):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    case['weather_schedule'][index]['received_at'] = receipt
    rehash(case); rehash(catalog)
    with pytest.raises(ValueError):
        runtime(catalog)


@pytest.mark.parametrize('warmup', [0, 60, 120, 240])
def test_manual_preflight_matches_runtime_fire_checkpoint(setup, warmup):
    from envs.wildfire_response.incident_day.scenarios import DayFire

    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    case['warmup_minutes'] = warmup
    rehash(case); rehash(catalog)
    with runtime(catalog):
        c = control(setup)
        c.reset(case['id'])
        actual = json.loads(json.dumps(c.environment.fire.checkpoint()))
        del actual['state']['_weather_runtime']
        reference = DayFire(c.environment.inputs, case['seed'])
        reference.set_wind(x=case['wind'][0], y=case['wind'][1])
        for x, y in case['initial_fire']:
            reference.add_fire(x, y)
        profile = weather.WeatherProfile.from_records([case['weather_schedule'][0]])
        state = weather.initial_state(profile)
        for minute in range(warmup):
            state = weather.advance(profile, state, minute + 1)
            reference.rules['spread_factor'] = weather.spread_factor(state, base_factor=.5)
            reference.step()
        reference.tick = 0
        reference.boundary_contact_at = None
        assert actual == json.loads(json.dumps(reference.checkpoint()))


@pytest.mark.parametrize('changed_field,new_value', [
    ('wind', [0., 1.]),
    ('temperature_c', 38),
    ('relative_humidity_pct', 18),
    ('rain_mm_per_hour', 7),
])
def test_causal_weather_dedup_with_stale_delivery_and_recovery(setup, changed_field, new_value):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    initial = dict(minute=0, wind=[1., 0.], temperature_c=27,
                   relative_humidity_pct=40, rain_mm_per_hour=0, received_at=0)
    changed = copy.deepcopy(initial)
    changed[changed_field] = new_value
    changed.update(minute=20, received_at=80)
    duplicate = copy.deepcopy(initial)
    duplicate.update(minute=25, received_at=25)
    latest = copy.deepcopy(changed)
    latest.update(minute=90, received_at=90)
    case['weather_schedule'] = [initial, changed, duplicate, latest]
    case['events'] = [event for event in case['events'] if event['kind'] != 'wind']
    if changed_field == 'wind':
        case['events'].extend([dict(minute=20, kind='wind', wind=[0., 1.]),
                               dict(minute=25, kind='wind', wind=[1., 0.]),
                               dict(minute=90, kind='wind', wind=[0., 1.])])
    case['events'].sort(key=lambda event: event['minute'])
    rehash(case); rehash(catalog)
    with runtime(catalog) as ctx:
        a = control(setup)
        a.reset(case['id'])
        while a.environment.minute < 25:
            a.execute('advance', {'max_minutes': 1})
        checkpoint = json.loads(json.dumps(a.private_checkpoint(), allow_nan=False))
        b = control(setup)
        ctx.restore_control(b, checkpoint)
        while a.environment.minute < 89:
            assert a.execute('advance', {'max_minutes': 1}) == b.execute('advance', {'max_minutes': 1})
        early = [r for r in a.environment.bus.reports if r['kind'] == 'weather_update']
        assert [(r['observed_at'], r['received_at'], r['actionable']) for r in early] == [
            (0, 0, True), (25, 25, False), (20, 80, False)]
        assert 90 not in [r['observed_at'] for r in early]
        saved_baseline = json.loads(a.environment.bus.checkpoint()['last_updates']['["weather","weather_update"]'])[0]
        for field in ('wind', 'temperature_c', 'relative_humidity_pct', 'rain_mm_per_hour'):
            assert saved_baseline[field] == initial[field]
        after_stale = control(setup)
        ctx.restore_control(after_stale, json.loads(json.dumps(a.private_checkpoint())))
        # Before the next hourly boundary, a longer wait must wake at minute 90.
        result = a.execute('advance', {'max_minutes': 10})
        assert result == b.execute('advance', {'max_minutes': 10})
        assert result == after_stale.execute('advance', {'max_minutes': 10})
        assert a.environment.minute == 90
        assert result['info']['wake_reason'] == 'actionable_delivery'
        records = [r for r in a.environment.bus.reports if r['kind'] == 'weather_update']
        assert [(r['observed_at'], r['received_at'], r['actionable']) for r in records] == [
            (0, 0, True), (25, 25, False), (20, 80, False), (90, 90, True)]
        assert a.environment.bus.checkpoint() == b.environment.bus.checkpoint()
        # Continuation after the corrected baseline is checkpoint-safe too.
        checkpoint = json.loads(json.dumps(a.private_checkpoint(), allow_nan=False))
        restored = control(setup)
        ctx.restore_control(restored, checkpoint)
        for _ in range(5):
            assert a.execute('advance', {'max_minutes': 1}) == restored.execute('advance', {'max_minutes': 1})
        if changed_field == 'wind':
            identity = {key: a.environment.trajectory()[key] for key in IDENTITY_FIELDS}
            while not a.environment.terminated and not a.environment.truncated:
                result = a.execute('advance', {'max_minutes': 60})
            assert a.environment.terminated and a.environment.minute == 1440
            verdict = a.finalize(expected_identity=identity, expected_interface_sha256=interface_digest())
            assert verdict['verified'] and verdict['status'] == 'full_day'


def test_weather_handover_without_active_assignments_and_recovery(setup):
    catalog = copy.deepcopy(setup[2])
    case = catalog['cases'][0]
    for unit in case['resources']:
        unit['sector_id'] = None
        unit['target_grid'] = None
        if unit['id'] == 'engine-0':
            unit['state'] = 'available'
            unit['position'] = copy.deepcopy(unit['base_position'])
    case['handover_notice'] = 'EXERCISE. PUBLIC-HANDOVER-NOTICE.'
    rehash(case); rehash(catalog)
    with runtime(catalog) as ctx:
        a = control(setup)
        initial, _ = a.reset(case['id'])
        report = next(r for r in initial['reports'] if r['kind'] == 'handover')
        assert (report['source'], report['role'], report['channel']) == ('handover', 'center', 'radio')
        assert (report['observed_at'], report['reported_at'], report['received_at']) == (0, 0, 0)
        assert 'PUBLIC-HANDOVER-NOTICE' in report['text']
        assert 'engine-0: available' in report['text']
        for resource in initial['resources']:
            assert f"{resource['id']}: {resource['state']}" in report['text']
        assert 'assignments are carried forward' not in report['text']
        assert 'FUTURE-REPORT-SENTINEL' not in json.dumps(initial)
        assert 'PRIVATE-WEATHER-SENTINEL' not in json.dumps(initial)
        engine = next(r for r in initial['resources'] if r['id'] == 'engine-0')
        assert engine['state'] == 'available' and engine['sector_id'] is None and engine['target_grid'] is None
        snapshot = json.loads(json.dumps(a.private_checkpoint()))
        b = control(setup)
        assert ctx.restore_control(b, snapshot) == a.environment.get_situation()
        while a.environment.minute < 15:
            assert a.execute('advance', {'max_minutes': 1}) == b.execute('advance', {'max_minutes': 1})
        follow = next(r for r in a.environment.bus.reports if r['kind'] == 'handover_followup')
        assert (follow['source'], follow['role'], follow['channel']) == ('prior-shift', 'center', 'radio')
        assert (follow['observed_at'], follow['reported_at'], follow['received_at']) == (0, 0, 15)
        assert follow['payload']['resource'] == engine
        assert 'engine-0 was reported as available' in follow['text']
        assert 'assignment' not in follow['text'].lower()
        assert 'confirmation' not in follow['text'].lower()
        assert 'synthetic initialization' in follow['text']
        assert follow == next(r for r in b.environment.bus.reports if r['kind'] == 'handover_followup')


def test_legacy_handover_message_is_unchanged_inside_and_outside_context(setup):
    legacy_id = 'incident-day-egress-v1-000'
    outside = control(setup)
    outside.reset(legacy_id)
    with runtime(setup[2]):
        inside = control(setup)
        inside.reset(legacy_id)
        for _ in range(15):
            assert inside.execute('advance', {'max_minutes': 1}) == outside.execute('advance', {'max_minutes': 1})
        for kind in ('handover', 'handover_followup'):
            first = next(r for r in outside.environment.bus.reports if r['kind'] == kind)
            second = next(r for r in inside.environment.bus.reports if r['kind'] == kind)
            assert first == second
        assert 'assignments are carried forward' in next(
            r['text'] for r in inside.environment.bus.reports if r['kind'] == 'handover')
        assert 'Confirmation of the carried ground assignment' in next(
            r['text'] for r in inside.environment.bus.reports if r['kind'] == 'handover_followup')


def test_weather_handover_reports_on_scene_status_without_inventing_history(setup):
    with runtime(setup[2]):
        c = control(setup)
        observation, _ = c.reset(setup[2]['cases'][0]['id'])
        unit = next(item for item in observation['resources'] if item['id'] == 'engine-0')
        assert unit['state'] == 'on_scene'
        handover = next(r for r in observation['reports'] if r['kind'] == 'handover')
        assert 'engine-0: on_scene' in handover['text']
        queued = next(q['record'] for q in c.environment.bus.checkpoint()['queue']
                      if q.get('record', {}).get('kind') == 'handover_followup')
        assert 'engine-0 was reported as on_scene' in queued['text']
        assert queued['payload']['resource'] == unit
        assert 'confirmation' not in queued['text'].lower()
