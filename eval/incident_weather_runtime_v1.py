"""Opt-in synthetic weather coupling for trusted, isolated incident-day runs.

Use ``IncidentWeatherRuntimeV1(catalog, expected_pins=...)`` around reset,
execution, verification and recovery, in one process/thread. The catalog is
validated by IncidentEvalBridgeV1. Independently pin its catalog and these
sources before scoring. This is an uncalibrated exercise proxy, not empirical
fire physics, observed weather, or an operational forecast.

Schedule minutes start at handover. Warm-up holds the minute-zero weather
constant; its moisture state carries over to the post-reset schedule. For each
warm-up tick, advance the moisture proxy by one minute under the fixed
minute-zero conditions, apply the factor from that updated state, then step
the fire once. After the final tick, retain the moisture value while
setting the profile clock to zero; the core resets ``fire.tick`` to zero. A
preflight can use the actual runtime and ``control.reset(case_id)``, then read
``control.environment.fire.checkpoint()``; this also applies the core guards.
At each post-handover tick t, first evolve the carried proxy by one minute using
conditions at t, then apply its bounded factor to that fire step. The core
applies the wind event for t before the same step. This discrete exercise
convention lets conditions change together while the proxy retains memory.

The bus emits at observation time. An optional schedule ``received_at`` sets
an exact delivery minute between observation and 60 minutes later. If omitted,
the bus chooses its deterministic radio delay; handover is always immediate
and an explicit handover ``received_at`` must be zero. The helper's standalone
``observation`` default is immediate; the bus owns actual runtime receipt.
Optional ``incoming_reports`` are sorted entries containing ``minute``, ``source``,
``role``, ``channel``, ``location``, ``text`` and ``delay`` (0..60). An
optional ``category`` may be true, ambiguous, duplicate or late; it is private
and never emitted. Reports are text only, not structured population evidence.
Weather updates are deduplicated against the newest delivered observation,
including wind, temperature, humidity and rain. Late stale updates remain in
the ledger but cannot change that baseline or wake the policy.
Weather-catalog handover text describes the public initial resource states;
the scenario's public handover notice is retained. The delayed follow-up carries
the original handover resource snapshot, without asserting a past assignment.
No future schedule is copied into public messages.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading

from envs.wildfire_response.incident_day import environment as day
from envs.wildfire_response.incident_day.events import EventBus, _without_timestamps
from envs.wildfire_response.incident_day.scenarios import DayFire
from wildfire_control_v3.control import interface_digest, _loaded_sources_match
from wildfire_control_v3 import _capture_source
from eval import incident_eval_bridge_v1 as bridge_module
from eval import incident_weather_v1 as weather
from eval.incident_eval_bridge_v1 import IncidentEvalBridgeV1

VERSION = 'incident-weather-runtime-v1'
_SOURCE, _SOURCE_MATCH = _capture_source(__name__, __file__, __loader__)
_WEATHER_SOURCE, _WEATHER_MATCH = _capture_source(weather.__name__, weather.__file__, weather.__loader__)
_BRIDGE_SOURCE, _BRIDGE_MATCH = _capture_source(bridge_module.__name__, bridge_module.__file__, bridge_module.__loader__)
_ORIGINAL_FIRE = day.DayFire
_ORIGINAL_RESET = day.IncidentDayEnv.reset
_ORIGINAL_TICK = day.IncidentDayEnv._tick
_ORIGINAL_RESTORE = day.IncidentDayEnv.restore
_ORIGINAL_BUS_TICK = EventBus.tick
_ORIGINAL_BUS_EMIT = EventBus.emit
_active = None
_lock = threading.RLock()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _profile(case):
    schedule = case.get('weather_schedule')
    if type(schedule) is not list or not schedule:
        raise ValueError('Each case needs a weather_schedule')
    rows = []
    fields = {'minute', 'wind', 'temperature_c', 'relative_humidity_pct', 'rain_mm_per_hour'}
    for row in schedule:
        if type(row) is not dict or set(row) not in (fields, fields | {'received_at'}):
            raise ValueError('Invalid weather schedule fields')
        minute = row['minute']
        if type(minute) is not int or not 0 <= minute <= 1440:
            raise ValueError('Invalid weather schedule minute')
        rows.append(copy.deepcopy(row))
    profile = weather.WeatherProfile.from_records(rows)
    if profile.records[0].received_at != 0 or any(
            r.received_at - r.minute > 60 for r in profile.records):
        raise ValueError('Weather receipt must be within 60 minutes and immediate at handover')
    if list(profile.records[0].wind) != case['wind']:
        raise ValueError('Handover wind differs from weather schedule')
    # Existing core wind events update the true wind before each fire tick.
    changes = [(r.minute, list(r.wind)) for previous, r in zip(profile.records, profile.records[1:])
               if r.wind != previous.wind]
    events = [(e['minute'], e['wind']) for e in case['events'] if e['kind'] == 'wind']
    if changes != events:
        raise ValueError('Wind events must match weather schedule changes exactly')
    if any(round(v, 2) != v for r in profile.records for v in r.wind):
        raise ValueError('Wind precision exceeds the kernel precision')
    warm = weather.WeatherProfile.from_records([profile.records[0].as_dict()])
    # Static one-minute profiles keep the helper's equations authoritative while
    # selecting the conditions in force at the core's upcoming tick.
    tick_profiles = []
    for record in profile.records:
        row = record.as_dict()
        row.update(minute=0, received_at=0)
        tick_profiles.append((record.minute, weather.WeatherProfile.from_records([row])))
    return profile, warm, tuple(tick_profiles)


def _incoming(case):
    reports = case.get('incoming_reports', [])
    if type(reports) is not list:
        raise ValueError('incoming_reports must be a list')
    required = {'minute', 'source', 'role', 'channel', 'location', 'text', 'delay'}
    previous = -1
    for report in reports:
        if (type(report) is not dict or set(report) not in (required, required | {'category'})
                or type(report['minute']) is not int or not 0 <= report['minute'] <= 1440
                or report['minute'] < previous or type(report['delay']) is not int
                or not 0 <= report['delay'] <= 60
                or any(type(report[field]) is not str or not report[field].strip()
                       for field in ('source', 'role', 'text'))
                or report['channel'] not in ('radio', 'phone', 'routine')
                or ('category' in report and report['category'] not in ('true', 'ambiguous', 'duplicate', 'late'))):
            raise ValueError('Invalid incoming report')
        previous = report['minute']
    return copy.deepcopy(reports)


class WeatherDayFire(DayFire):
    """DayFire with a JSON-only private weather state."""
    def __init__(self, inputs, seed, case):
        _active_check()
        super().__init__(inputs, seed)
        profile, warm, _ = _active._profiles[case['id']]
        self._weather_runtime = dict(version=VERSION, case_id=case['id'], seed=seed,
            catalog_sha256=_active._catalog_sha256,
            warmup_remaining=case['warmup_minutes'], warmup_total=case['warmup_minutes'],
            state=weather.checkpoint(warm if case['warmup_minutes'] else profile,
                                     weather.initial_state(warm if case['warmup_minutes'] else profile)))

    def step(self, count=1):
        _active_check()
        if type(count) is not int or count < 0:
            raise ValueError('Invalid fire step count')
        data = self._weather_runtime
        _validate_fire_context(data)
        profile, warm, tick_profiles = _active._profiles[data['case_id']]
        for _ in range(count):
            is_warm = data['warmup_remaining'] > 0
            selected = warm if is_warm else profile
            state = weather.restore(selected, data['state'])
            if is_warm:
                next_state = weather.advance(warm, state, state['minute'] + 1)
            else:
                tick = state['minute'] + 1
                conditions = next(p for start, p in reversed(tick_profiles) if start <= tick)
                one_minute = weather.initial_state(conditions, moisture_proxy=state['moisture_proxy'])
                updated = weather.advance(conditions, one_minute, 1)
                next_state = weather.initial_state(profile, moisture_proxy=updated['moisture_proxy'])
                next_state['minute'] = tick
            self.rules['spread_factor'] = weather.spread_factor(next_state, base_factor=0.5)
            super().step()
            state = next_state
            if is_warm:
                data['warmup_remaining'] -= 1
                if not data['warmup_remaining']:
                    state = weather.initial_state(profile, moisture_proxy=state['moisture_proxy'])
                    selected = profile
            data['state'] = weather.checkpoint(selected, state)

    @classmethod
    def restore(cls, value):
        _active_check()
        saved = value.get('state', {}).get('_weather_runtime')
        if type(saved) is not dict or set(saved) != {'version', 'case_id', 'seed', 'catalog_sha256', 'warmup_remaining', 'warmup_total', 'state'}:
            raise ValueError('Invalid weather fire checkpoint')
        case = _active._cases.get(saved['case_id'])
        if (case is None or saved['version'] != VERSION or saved['seed'] != case['seed']
                or saved['catalog_sha256'] != _active._catalog_sha256
                or saved['warmup_total'] != case['warmup_minutes']
                or type(saved['warmup_remaining']) is not int
                or not 0 <= saved['warmup_remaining'] <= saved['warmup_total']):
            raise ValueError('Weather fire checkpoint does not match catalog')
        profile, warm, _ = _active._profiles[saved['case_id']]
        state = weather.restore(warm if saved['warmup_remaining'] else profile, saved['state'])
        if saved['warmup_remaining'] and state['minute'] != saved['warmup_total'] - saved['warmup_remaining']:
            raise ValueError('Invalid weather warm-up clock')
        obj = super().restore(value)
        if not saved['warmup_remaining'] and obj.tick != state['minute']:
            raise ValueError('Fire and weather clocks differ')
        return obj


def _validate_fire_context(data):
    case = _active._cases.get(data.get('case_id'))
    if (case is None or data.get('seed') != case['seed']
            or data.get('catalog_sha256') != _active._catalog_sha256):
        raise RuntimeError('Fire belongs to a different weather catalog')


def _active_check():
    if _active is None:
        raise RuntimeError('Weather runtime context is inactive')
    _active._check()


class _FireDispatch:
    def __new__(cls, inputs, seed):
        _active_check()
        env = _active._current
        case = _active._cases.get(env._scenario['id']) if env is not None else None
        if case is not None:
            if seed != case['seed']:
                raise ValueError('Weather seed differs from pinned case')
            return WeatherDayFire(inputs, seed, case)
        return _ORIGINAL_FIRE(inputs, seed)

    @classmethod
    def restore(cls, value):
        _active_check()
        state = value.get('state', {})
        if '_weather_runtime' in state:
            return WeatherDayFire.restore(value)
        return _ORIGINAL_FIRE.restore(value)


def _reset(env, *args, **kwargs):
    _active_check()
    if _active._current is not None:
        raise RuntimeError('Nested incident execution is unsupported')
    _active._current = env
    try:
        return _ORIGINAL_RESET(env, *args, **kwargs)
    finally:
        _active._current = None


def _restore(env, checkpoint):
    _active_check()
    try:
        state = checkpoint['state']
        scenario = state['_scenario']
        marker = state['fire']['state'].get('_weather_runtime')
        case = _active._cases.get(scenario['id'])
        if case is not None:
            if scenario != case or not isinstance(marker, dict) or marker.get('case_id') != case['id']:
                raise ValueError('Checkpoint does not match the pinned weather case')
        elif marker is not None:
            raise ValueError('Weather checkpoint case is not in this catalog')
    except (KeyError, TypeError):
        raise ValueError('Invalid incident checkpoint') from None
    return _ORIGINAL_RESTORE(env, checkpoint)


def _tick(env):
    _active_check()
    if isinstance(getattr(env, 'fire', None), WeatherDayFire):
        _validate_fire_context(env.fire._weather_runtime)
    if _active._current is not None:
        raise RuntimeError('Nested incident execution is unsupported')
    _active._current = env
    try:
        return _ORIGINAL_TICK(env)
    finally:
        _active._current = None


def _emit(bus, source, kind, payload, minute, **kwargs):
    # PopulationEngineV3 captures the bound emitter in an instance filter.
    # A legacy bus created in this context may outlive it; that captured method
    # must keep working after the class hooks have been restored.
    if _active is None:
        return _ORIGINAL_BUS_EMIT(bus, source, kind, payload, minute, **kwargs)
    _active_check()
    env = _active._current
    weather_case = (env is not None and getattr(env, 'bus', None) is bus
                    and isinstance(getattr(env, 'fire', None), WeatherDayFire))
    if weather_case and source == 'weather' and kind == 'wind_update':
        return None  # One synthetic weather report is emitted below instead.
    if weather_case and minute == 0 and (source, kind) == ('handover', 'handover'):
        payload = copy.deepcopy(payload)
        states = ', '.join(f"{unit['id']}: {unit['state']}" for unit in
                           sorted(env._known_resources.values(), key=lambda item: item['id']))
        notice = env._scenario.get('handover_notice', '')
        payload['text'] = ('EXERCISE. Large-fire handover. Reported perimeter is partial. '
                           f'Initial resource states: {states}. ' + notice)
    elif weather_case and minute == 0 and (source, kind) == ('prior-shift', 'handover_followup'):
        payload = copy.deepcopy(payload)
        resource = payload['resource']
        payload['text'] = (f"EXERCISE. Handover resource snapshot: {resource['id']} "
                           f"was reported as {resource['state']}. Resource details are included. "
                           'Prehistory is synthetic initialization, not an operational record.')
    return _ORIGINAL_BUS_EMIT(bus, source, kind, payload, minute, **kwargs)


def _weather_values(record):
    payload = record['payload']
    return (tuple(payload['wind']), payload['temperature_c'],
            payload['relative_humidity_pct'], payload['rain_mm_per_hour'])


def _is_weather_report(record):
    return record.get('source') == 'weather' and record.get('kind') == 'weather_update'


def _causal_weather_deliveries(bus, records, previous_count):
    # The ledger is already checkpointed by EventBus. Reconstruct the newest
    # delivered observation from it instead of adding uncheckpointed state.
    prior = [r for r in bus.reports[:previous_count] if _is_weather_report(r)]
    latest = max(prior, key=lambda r: r['observed_at']) if prior else None
    for live, returned in zip(bus.reports[previous_count:], records):
        if not _is_weather_report(live):
            continue
        newer = latest is None or live['observed_at'] > latest['observed_at']
        changed = latest is None or _weather_values(live) != _weather_values(latest)
        actionable = bool(newer and changed)
        live['actionable'] = returned['actionable'] = actionable
        if newer:
            latest = live
    if latest is not None:
        # Keep the bus's own checkpointed dedup baseline coherent with the
        # causal baseline, even after a stale report arrived most recently.
        key = json.dumps([latest['source'], latest['kind']], separators=(',', ':'))
        bus._last_updates[key] = json.dumps([
            _without_timestamps(latest['payload']), _without_timestamps(latest['location']),
            latest['role'], latest['channel'], latest['urgent']], sort_keys=True, separators=(',', ':'))
    return records


def _bus_tick(bus, minute, center_views):
    _active_check()
    env = _active._current
    if (env is not None and getattr(env, 'bus', None) is bus
            and isinstance(getattr(env, 'fire', None), WeatherDayFire)):
        profile, _, _ = _active._profiles[env.fire._weather_runtime['case_id']]
        for record in profile.records:
            if record.minute == minute:
                payload = dict(wind=list(record.wind), temperature_c=record.temperature_c,
                    relative_humidity_pct=record.relative_humidity_pct,
                    rain_mm_per_hour=record.rain_mm_per_hour,
                    text='EXERCISE. Synthetic weather input; uncalibrated proxy, not observed weather or fire physics.')
                case = _active._cases[env.fire._weather_runtime['case_id']]
                row = next(row for row in case['weather_schedule'] if row['minute'] == minute)
                delay = (0 if minute == 0 else
                         row['received_at'] - minute if 'received_at' in row else None)
                bus.emit('weather', 'weather_update', payload, minute,
                         channel='radio', delay=delay)
        for report in _active._incoming[env.fire._weather_runtime['case_id']]:
            if report['minute'] == minute:
                bus.emit(report['source'], 'incoming_report', {'text': report['text']}, minute,
                         role=report['role'], channel=report['channel'],
                         location=copy.deepcopy(report['location']), delay=report['delay'])
    previous_count = len(bus.reports)
    delivered = _ORIGINAL_BUS_TICK(bus, minute, center_views)
    if (env is not None and getattr(env, 'bus', None) is bus
            and isinstance(getattr(env, 'fire', None), WeatherDayFire)
            and any(_is_weather_report(record) for record in delivered)):
        return _causal_weather_deliveries(bus, delivered, previous_count)
    return delivered


class IncidentWeatherRuntimeV1:
    """Trusted process-scoped catalog + weather dispatcher; contexts cannot nest."""
    def __init__(self, catalog, *, expected_pins):
        self.bridge = IncidentEvalBridgeV1(catalog)
        self._catalog_sha256 = catalog['sha256']
        self._cases = {case['id']: copy.deepcopy(case) for case in catalog['cases']}
        seeds = [case['seed'] for case in self._cases.values()]
        if len(seeds) != len(set(seeds)) or any(seed in range(base, base + 6)
                for seed in seeds for base in (2844000, 3866400)):
            raise ValueError('Duplicate or legacy scenario seed')
        self._profiles = {key: _profile(case) for key, case in self._cases.items()}
        self._incoming = {key: _incoming(case) for key, case in self._cases.items()}
        self._expected = copy.deepcopy(expected_pins)
        self._owner = None
        self._current = None

    @property
    def pins(self):
        return dict(**self.bridge.pins, runtime_version=VERSION, runtime_source_sha256=_SOURCE,
                    weather_source_sha256=_WEATHER_SOURCE, implementation_sha256=day.implementation_digest(),
                    interface_sha256=interface_digest())

    def _check(self):
        if (_active is not self or self._owner != (os.getpid(), threading.get_ident())
                or day.DayFire is not _FireDispatch or day.IncidentDayEnv.reset is not _reset
                or day.IncidentDayEnv._tick is not _tick or day.IncidentDayEnv.restore is not _restore
                or EventBus.tick is not _bus_tick
                or EventBus.emit is not _emit):
            raise RuntimeError('Weather runtime is inactive, modified, or on another thread/process')

    def __enter__(self):
        global _active
        with _lock:
            if _active is not None:
                raise RuntimeError('Nested or concurrent weather runtimes are unsupported')
            if (day.DayFire is not _ORIGINAL_FIRE or day.IncidentDayEnv.reset is not _ORIGINAL_RESET
                    or day.IncidentDayEnv._tick is not _ORIGINAL_TICK or day.IncidentDayEnv.restore is not _ORIGINAL_RESTORE
                    or EventBus.tick is not _ORIGINAL_BUS_TICK
                    or EventBus.emit is not _ORIGINAL_BUS_EMIT):
                raise RuntimeError('Incident engine hooks already modified')
            if (self.pins != self._expected or not (_SOURCE_MATCH and _WEATHER_MATCH and _BRIDGE_MATCH)
                    or _file_hash(__file__) != _SOURCE or _file_hash(weather.__file__) != _WEATHER_SOURCE
                    or _file_hash(bridge_module.__file__) != _BRIDGE_SOURCE or not _loaded_sources_match()):
                raise ValueError('Runtime or engine pins differ')
            self.bridge.__enter__()
            self._owner = (os.getpid(), threading.get_ident())
            _active = self
            day.DayFire, day.IncidentDayEnv.reset, day.IncidentDayEnv._tick = _FireDispatch, _reset, _tick
            day.IncidentDayEnv.restore = _restore
            EventBus.tick, EventBus.emit = _bus_tick, _emit
            return self

    def restore_control(self, control, checkpoint):
        self._check()
        self.bridge.validate_checkpoint(checkpoint)
        core = checkpoint['engine_checkpoint']
        if core.get('version') == 'wildfire-incident-day-population-v3':
            core = core['core']
        fire = core['state']['fire']['state'].get('_weather_runtime')
        case = core['state']['_scenario']
        if not isinstance(fire, dict) or fire.get('case_id') != case['id']:
            raise ValueError('Checkpoint lacks matching weather state')
        return self.bridge.restore_control(control, checkpoint)

    def __exit__(self, exc_type, exc, traceback):
        global _active
        with _lock:
            valid = True
            try:
                self._check()
                valid = (self.pins == self._expected and _file_hash(__file__) == _SOURCE
                         and _file_hash(weather.__file__) == _WEATHER_SOURCE
                         and _file_hash(bridge_module.__file__) == _BRIDGE_SOURCE and _loaded_sources_match())
            except Exception:
                valid = False
            day.DayFire, day.IncidentDayEnv.reset, day.IncidentDayEnv._tick = _ORIGINAL_FIRE, _ORIGINAL_RESET, _ORIGINAL_TICK
            day.IncidentDayEnv.restore = _ORIGINAL_RESTORE
            EventBus.tick, EventBus.emit = _ORIGINAL_BUS_TICK, _ORIGINAL_BUS_EMIT
            _active = None
            self._owner = None
            self._current = None
            self.bridge.__exit__(exc_type, exc, traceback)
            if not valid:
                raise RuntimeError('Weather runtime hooks or pins changed')
        return False
