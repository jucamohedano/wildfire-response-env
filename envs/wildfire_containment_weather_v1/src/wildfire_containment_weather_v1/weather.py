"""Trusted, standalone composition of v1 weather, containment and communications.

This is not an incident-day adapter. The incident host must own and serialize the
whole lifecycle before this component can be connected to actor-facing episodes.
"""
import copy
import hashlib
import json
from pathlib import Path

from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.events import EventBus, _without_timestamps
from envs.wildfire_response.incident_day.scenarios import DayFire
from eval import incident_weather_v1 as weather

from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.incident_day import CORE, _check_runtime
from wildfire_containment_v1.kernel import ContainmentKernel

VERSION = 'containment-weather-owner-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _sources():
    paths = [Path(__file__), Path(weather.__file__), Path(__import__('wildfire_containment_v1.fire', fromlist=['']).__file__),
             Path(__import__('wildfire_containment_v1.kernel', fromlist=['']).__file__)]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _validate_case(case):
    rows = case['weather_schedule']
    if type(rows) is not list:
        raise ValueError('Invalid weather schedule')
    profile = weather.WeatherProfile.from_records(rows)
    if profile.records[0].received_at != 0 or any(r.received_at - r.minute > 60 for r in profile.records):
        raise ValueError('Invalid weather receipt')
    if list(profile.records[0].wind) != case['wind']:
        raise ValueError('Handover wind differs from weather schedule')
    changes = [(r.minute, list(r.wind)) for old, r in zip(profile.records, profile.records[1:])
               if r.wind != old.wind]
    if changes != [(e['minute'], e['wind']) for e in case['events'] if e['kind'] == 'wind']:
        raise ValueError('Wind events must match weather schedule changes exactly')
    if any(round(v, 2) != v for r in profile.records for v in r.wind):
        raise ValueError('Wind precision exceeds kernel precision')
    if type(case['warmup_minutes']) is not int or not 0 <= case['warmup_minutes'] <= 1440:
        raise ValueError('Invalid warmup')
    reports = case.get('incoming_reports', [])
    required = {'minute', 'source', 'role', 'channel', 'location', 'text', 'delay'}
    if type(reports) is not list:
        raise ValueError('Invalid incoming reports')
    previous = -1
    for report in reports:
        if (type(report) is not dict or set(report) not in (required, required | {'category'})
                or type(report['minute']) is not int or not previous <= report['minute'] <= 1440
                or type(report['delay']) is not int or not 0 <= report['delay'] <= 60
                or any(type(report[k]) is not str or not report[k].strip() for k in ('source', 'role', 'text'))
                or report['channel'] not in ('radio', 'phone', 'routine')
                or ('category' in report and report['category'] not in ('true', 'ambiguous', 'duplicate', 'late'))):
            raise ValueError('Invalid incoming report')
        previous = report['minute']
    return profile


def _payload(record):
    return dict(wind=list(record.wind), temperature_c=record.temperature_c,
                relative_humidity_pct=record.relative_humidity_pct,
                rain_mm_per_hour=record.rain_mm_per_hour,
                text='EXERCISE. Synthetic weather input; uncalibrated proxy, not observed weather or fire physics.')


def _weather_report(record):
    return record.get('source') == 'weather' and record.get('kind') == 'weather_update'


def _deliver(bus, minute, center_views=None):
    previous = len(bus.reports)
    records = EventBus.tick(bus, minute, center_views if center_views is not None else {})
    prior = [r for r in bus.reports[:previous] if _weather_report(r)]
    latest = max(prior, key=lambda r: r['observed_at']) if prior else None
    for live, returned in zip(bus.reports[previous:], records):
        if not _weather_report(live):
            continue
        newer = latest is None or live['observed_at'] > latest['observed_at']
        keys = ('wind', 'temperature_c', 'relative_humidity_pct', 'rain_mm_per_hour')
        changed = latest is None or any(live['payload'][k] != latest['payload'][k] for k in keys)
        live['actionable'] = returned['actionable'] = bool(newer and changed)
        if newer:
            latest = live
    if latest is not None:
        key = json.dumps(['weather', 'weather_update'], separators=(',', ':'))
        bus._last_updates[key] = json.dumps([
            _without_timestamps(latest['payload']), _without_timestamps(latest['location']),
            latest['role'], latest['channel'], latest['urgent']], sort_keys=True, separators=(',', ':'))
    return records


class ContainmentWeatherOwner:
    """Single trusted driver for a fire, weather proxy, and private event bus.

    Caller supplies validated trusted scenario and inputs. Never expose this
    object, its checkpoint, case, or fire to an actor. Calls are not concurrent.
    """
    def __init__(self, inputs, case, *, containment_config, pressure_scale):
        _check_runtime()
        if implementation_digest() != CORE:
            raise RuntimeError('Unreviewed incident-day core implementation')
        self._inputs, self._case = copy.deepcopy(inputs), copy.deepcopy(case)
        self._profile = _validate_case(self._case)
        self._config = copy.deepcopy(containment_config)
        self._scale = pressure_scale
        self._pins = dict(version=VERSION, core=CORE, sources=_sources(), inputs=_digest(inputs),
                          case=_digest(case), config=_digest(containment_config), scale=pressure_scale)
        self._valid = False
        fire = DayFire(self._inputs, case['seed'])
        fire.set_wind(x=case['wind'][0], y=case['wind'][1])
        for x, y in case['initial_fire']:
            fire.add_fire(x, y)
        warm = weather.WeatherProfile.from_records([self._profile.records[0].as_dict()])
        state = weather.initial_state(warm)
        for minute in range(case['warmup_minutes']):
            state = weather.advance(warm, state, minute + 1)
            fire.rules['spread_factor'] = weather.spread_factor(state, base_factor=.5)
            fire.step()
        fire.tick = 0
        fire.boundary_contact_at = None
        self._state = weather.initial_state(self._profile, moisture_proxy=state['moisture_proxy'])
        self._fire = ContainmentDayFire.from_day_fire(fire, containment=ContainmentKernel(**self._config),
                                                     pressure_scale=pressure_scale)
        self._bus = EventBus(case['seed'], [])
        self._emit_at(0)
        _deliver(self._bus, 0)
        self._valid = True

    def _check(self):
        _check_runtime()
        if not self._valid or self._pins['sources'] != _sources() or implementation_digest() != CORE:
            raise RuntimeError('Invalid or changed weather owner; restore or reset')

    def _emit_at(self, minute):
        for row, record in zip(self._case['weather_schedule'], self._profile.records):
            if record.minute == minute:
                delay = 0 if minute == 0 else row['received_at'] - minute if 'received_at' in row else None
                self._bus.emit('weather', 'weather_update', _payload(record), minute,
                               channel='radio', delay=delay)
        for report in self._case.get('incoming_reports', []):
            if report['minute'] == minute:
                self._bus.emit(report['source'], 'incoming_report', {'text': report['text']}, minute,
                               role=report['role'], channel=report['channel'],
                               location=copy.deepcopy(report['location']), delay=report['delay'])

    def reports(self):
        """Detached delivered reports only; reads do not advance time."""
        self._check()
        return copy.deepcopy(self._bus.reports)

    def step(self, *, assignments=(), suppressing=()):
        """Advance exactly one minute; returns trusted resource costs and delivered reports."""
        self._check()
        if self._state['minute'] >= 1440:
            raise ValueError('End of day')
        minute = self._state['minute'] + 1
        row = weather.conditions(self._profile, minute)
        row.update(minute=0, received_at=0)
        selected = weather.WeatherProfile.from_records([row])
        one = weather.initial_state(selected, moisture_proxy=self._state['moisture_proxy'])
        updated = weather.advance(selected, one, 1)
        next_state = weather.initial_state(self._profile, moisture_proxy=updated['moisture_proxy'])
        next_state['minute'] = minute
        # Once mutation begins, failures poison the owner; a prior checkpoint is required.
        self._valid = False
        self._fire.set_wind(x=row['wind'][0], y=row['wind'][1])
        self._fire.rules['spread_factor'] = weather.spread_factor(next_state, base_factor=.5)
        costs = self._fire.step_with_containment(assignments=assignments, suppressing=suppressing)
        self._state = next_state
        self._emit_at(minute)
        delivered = _deliver(self._bus, minute)
        self._valid = True
        return dict(costs=costs, reports=delivered)

    def checkpoint(self):
        """Private, integrity-checked snapshot; pins are not authentication."""
        self._check()
        value = dict(pins=copy.deepcopy(self._pins), fire=self._fire.checkpoint(),
                     state=weather.checkpoint(self._profile, self._state), bus=self._bus.checkpoint())
        return dict(value, sha256=_digest(value))

    @classmethod
    def restore(cls, inputs, case, *, containment_config, pressure_scale, checkpoint):
        obj = cls(inputs, case, containment_config=containment_config, pressure_scale=pressure_scale)
        saved = copy.deepcopy(checkpoint)
        if not isinstance(saved, dict) or set(saved) != {'pins', 'fire', 'state', 'bus', 'sha256'}:
            raise ValueError('Invalid owner checkpoint')
        if saved['sha256'] != _digest({k: v for k, v in saved.items() if k != 'sha256'}) or saved['pins'] != obj._pins:
            raise ValueError('Owner checkpoint identity or integrity differs')
        fire = ContainmentDayFire.restore(saved['fire'])
        state = weather.restore(obj._profile, saved['state'])
        bus = EventBus.restore(saved['bus'])
        if fire.tick != state['minute'] or bus._last_tick != state['minute'] or bus.seed != case['seed']:
            raise ValueError('Owner clocks or seed differ')
        obj._fire, obj._state, obj._bus = fire, state, bus
        return obj
