import copy
import json

import pytest

from eval.incident_weather_v1 import (
    WeatherProfile, advance, checkpoint, conditions, initial_state, observation, restore,
    spread_factor,
)


def record(at=0, received=None, *, wind=(1, 0), temp=20, humidity=50, rain=0):
    return dict(minute=at, received_at=at if received is None else received,
                wind=list(wind), temperature_c=temp,
                relative_humidity_pct=humidity, rain_mm_per_hour=rain)


def profile(*records):
    return WeatherProfile.from_records(list(records))


@pytest.mark.parametrize('change', [
    {'minute': -1}, {'minute': True}, {'received_at': -1},
    {'received_at': 1.5}, {'wind': [0]}, {'wind': [4, 0]},
    {'wind': [True, 0]}, {'wind': [0, float('nan')]},
    {'temperature_c': -51}, {'temperature_c': float('inf')},
    {'relative_humidity_pct': -1}, {'relative_humidity_pct': 101},
    {'rain_mm_per_hour': -0.1}, {'rain_mm_per_hour': 201},
    {'rain_mm_per_hour': False}, {'extra': 1},
])
def test_reject_invalid_records(change):
    item = record()
    item.update(change)
    with pytest.raises(ValueError):
        profile(item)


def test_profile_requires_complete_causal_increasing_records():
    missing = record()
    del missing['wind']
    invalid = [[], [missing], [record(1)], [record(0), record(0)],
               [record(0), record(3, 2)], [record(0), record(2), record(1)]]
    for records in invalid:
        with pytest.raises(ValueError):
            WeatherProfile.from_records(records)


def test_hotter_drier_and_rainier_are_monotonic_from_same_state():
    inputs = [record(temp=20, humidity=60), record(temp=35, humidity=60),
              record(temp=20, humidity=20), record(temp=20, humidity=60, rain=8)]
    states = []
    for item in inputs:
        p = profile(item)
        states.append(advance(p, initial_state(p), 60))
    reference, hotter, drier, rainy = states
    assert hotter['moisture_proxy'] < reference['moisture_proxy'] < rainy['moisture_proxy']
    assert drier['moisture_proxy'] < reference['moisture_proxy']
    assert spread_factor(hotter) > spread_factor(reference) > spread_factor(rainy)
    assert spread_factor(drier) > spread_factor(reference)


def test_response_has_memory_and_rain_wets_faster_than_humidity():
    humid = profile(record(temp=20, humidity=100))
    rainy = profile(record(temp=20, humidity=50, rain=10))
    humid_start = initial_state(humid)
    rain_start = initial_state(rainy)
    humid_one = advance(humid, humid_start, 1)
    humid_hour = advance(humid, humid_start, 60)
    rain_one = advance(rainy, rain_start, 1)
    assert 0.5 < humid_one['moisture_proxy'] < humid_hour['moisture_proxy'] < 1.0
    assert 0.5 < rain_one['moisture_proxy'] < 1.0
    assert rain_one['moisture_proxy'] > humid_one['moisture_proxy']
    assert advance(humid, humid_start, 0) == humid_start


def test_change_applies_to_physics_at_observed_time_not_receipt():
    p = profile(record(), record(10, 20, temp=40, humidity=10, wind=(-2, 1)))
    baseline = profile(record())
    at_change = advance(p, initial_state(p), 10)
    assert at_change['moisture_proxy'] == advance(baseline, initial_state(baseline), 10)['moisture_proxy']
    assert advance(p, at_change, 11)['moisture_proxy'] < at_change['moisture_proxy']
    assert observation(p, 19)['observed_at'] == 0
    assert observation(p, 20)['observed_at'] == 10
    assert observation(p, 20)['received_at'] == 20
    assert observation(p, 20)['wind'] == [-2.0, 1.0]


def test_delayed_and_out_of_order_reports_do_not_leak_or_replace_newer_data():
    p = profile(record(0, 5), record(10, 30, temp=30), record(20, 21, temp=40))
    assert observation(p, 4) is None
    assert observation(p, 5)['observed_at'] == 0
    assert observation(p, 20)['observed_at'] == 0
    assert observation(p, 21)['observed_at'] == 20
    assert observation(p, 30)['observed_at'] == 20


def test_generator_schedule_json_state_and_trusted_conditions_contract():
    schedule = [
        {'minute': 0, 'wind': [1, 0], 'temperature_c': 25,
         'relative_humidity_pct': 40, 'rain_mm_per_hour': 0},
        {'minute': 120, 'wind': [0, -2], 'temperature_c': 20,
         'relative_humidity_pct': 75, 'rain_mm_per_hour': 4, 'received_at': 125},
    ]
    original = copy.deepcopy(schedule)
    p = WeatherProfile.from_records(schedule)
    start = initial_state(p)
    state = advance(p, json.loads(json.dumps(start)), 120)
    assert set(state) == {'version', 'profile_sha256', 'minute', 'moisture_proxy'}
    assert json.loads(json.dumps(state, allow_nan=False)) == state
    assert conditions(p, 120)['wind'] == [0.0, -2.0]
    assert observation(p, 120)['observed_at'] == 0
    assert observation(p, 120)['received_at'] == 0
    assert observation(p, 125)['observed_at'] == 120
    assert observation(p, 125)['relative_humidity_pct'] == 75
    assert observation(p, 125)['rain_mm_per_hour'] == 4
    assert advance(p, state, 121) == advance(p, restore(p, json.loads(json.dumps(state))), 121)
    assert schedule == original


def test_exact_replay_checkpoint_reset_and_chunk_independence():
    p = profile(record(), record(15, 25, temp=39, humidity=10),
                record(60, 60, temp=12, humidity=90, rain=15))
    start = initial_state(p, moisture_proxy=0.7)
    direct = advance(p, start, 240)
    first = advance(p, start, 77)
    saved = checkpoint(p, first)
    encoded = json.dumps(saved, allow_nan=False)
    resumed = advance(p, restore(p, json.loads(encoded)), 240)
    sequential = start
    for minute in range(1, 241):
        sequential = advance(p, sequential, minute)
    assert direct == resumed == sequential
    assert advance(p, initial_state(p, moisture_proxy=0.7), 240) == direct
    assert start['minute'] == 0 and start['moisture_proxy'] == 0.7


def test_reject_bad_states_checkpoints_and_wrong_profile():
    p = profile(record())
    other = profile(record(temp=30))
    state = advance(p, initial_state(p), 10)
    saved = checkpoint(p, state)
    with pytest.raises(ValueError):
        restore(other, saved)
    with pytest.raises(ValueError):
        advance(other, state, 20)
    for invalid in (dict(saved, minute=-1), dict(saved, moisture_proxy=2),
                    dict(saved, moisture_proxy=float('nan')), dict(saved, version='wrong'),
                    dict(saved, unexpected=1)):
        with pytest.raises(ValueError):
            restore(p, invalid)
    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError):
            advance(p, state, invalid)
    with pytest.raises(ValueError):
        advance(p, state, 9)
    for invalid in (-0.1, 1.1, float('nan'), True):
        with pytest.raises(ValueError):
            initial_state(p, moisture_proxy=invalid)
    for invalid in (0, 4.1, float('nan'), True):
        with pytest.raises(ValueError):
            spread_factor(state, base_factor=invalid)


def test_extreme_profiles_and_factors_remain_bounded():
    for item in (record(temp=-50, humidity=100, rain=200, wind=(3, -3)),
                 record(temp=60, humidity=0, rain=0, wind=(-3, 3))):
        p = profile(item)
        for initial in (0, 0.5, 1):
            state = advance(p, initial_state(p, moisture_proxy=initial), 1440)
            assert 0 <= state['moisture_proxy'] <= 1
            for base in (0.25, 0.5, 1, 4):
                assert 0.25 <= spread_factor(state, base_factor=base) <= 4


def test_original_inputs_and_exported_data_are_not_mutated():
    records = [record(), record(5, 7, wind=(-1, 1), rain=2)]
    original = copy.deepcopy(records)
    p = WeatherProfile.from_records(records)
    state = advance(p, initial_state(p), 10)
    saved = checkpoint(p, state)
    saved_copy = copy.deepcopy(saved)
    restore(p, saved)
    public = observation(p, 10)
    public['wind'][0] = 99
    records[1]['wind'][0] = 99
    assert observation(p, 10)['wind'] == [-1.0, 1.0]
    assert saved == saved_copy
    records[1]['wind'][0] = original[1]['wind'][0]
    assert records == original
