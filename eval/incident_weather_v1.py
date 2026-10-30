"""Deterministic, synthetic weather effects for incident-day experiments.

This is an *uncalibrated* dimensionless exercise proxy, not measured or
estimated fuel moisture, a forecast, or an operational safety model. The
coefficients below are arbitrary choices for sensitivity exercises. Qualitative
motivation only: Fosberg, Rothermel and Andrews (1981) describe fuel-moisture
memory and dependence on temperature, humidity and precipitation duration
(https://research.fs.usda.gov/treesearch/47360); Brackebusch (1975) describes
moisture changes associated with precipitation and temperature
(https://research.fs.usda.gov/treesearch/49034). Neither source validates this
proxy, its rates, the faster rain response, or its spread-factor mapping.

The generator's ``scenario['weather_schedule']`` is a JSON list, for example::

    [{"minute": 0, "wind": [1, 0], "temperature_c": 25,
      "relative_humidity_pct": 40, "rain_mm_per_hour": 0},
     {"minute": 120, "wind": [0, -2], "temperature_c": 20,
      "relative_humidity_pct": 75, "rain_mm_per_hour": 4,
      "received_at": 125}]

All six fields shown are required except ``received_at``, which defaults to
``minute`` (an explicit synthetic immediate-delivery assumption). Times are
integer minutes in [0, 1000000], strictly increasing from minute 0, and receipt
cannot precede the weather change. Wind is a two-component model vector in
[-3, 3] per component, positive X east and positive Y south, pointing toward
spread. Temperature is in [-50, 60] degrees C, relative humidity in [0, 100]
percent, and rainfall rate in [0, 200] mm/hour; values must be finite numbers.

Records describe piecewise-constant *environmental* conditions from ``minute``;
physics uses those conditions even before their synthetic report is received.
Public observations contain only records received by the requested time and
choose the newest observed record, so late, stale reports cannot overwrite a
newer one. All values supplied by the caller are exercise inputs; nothing here
asserts that they were measured or historically available.

Typical use::

    profile = WeatherProfile.from_records(scenario['weather_schedule'])
    state = initial_state(profile)
    state = advance(profile, state, 60)
    factor = spread_factor(state, base_factor=0.5)
    true_conditions = conditions(profile, state['minute'])  # evaluator only
    restored = restore(profile, checkpoint(profile, state))
    public = observation(profile, state['minute'])  # actor-safe as of this time

``initial_state`` and ``advance`` return plain JSON dictionaries with exactly
``version``, ``profile_sha256``, ``minute`` and ``moisture_proxy``. Checkpoint
and restore validate and copy that same state; the profile digest prevents
restoration against a different schedule. ``conditions`` exposes the active
schedule entry to the trusted integrator; never put it in an actor observation.
``observation`` returns None or a fresh dictionary with weather fields,
``observed_at``, ``received_at``, ``as_of`` and synthetic-source labels. It
never includes an undelivered or future record. The wind vector is exposed as
data only; this module does not apply it to an engine.

Advancing from t to t+1 applies the conditions at t. Repeated single-minute
updates make chunked and checkpointed execution exactly reproducible. The
module never changes a simulator, consumes randomness, or mutates caller data.
"""

from dataclasses import dataclass
import hashlib
import json
import math


VERSION = 'incident-weather-effects-v1'
_FIELDS = frozenset(('minute', 'wind', 'temperature_c',
                     'relative_humidity_pct', 'rain_mm_per_hour'))
_MAX_MINUTE = 1_000_000


def _minute(value, name):
    if type(value) is not int or not 0 <= value <= _MAX_MINUTE:
        raise ValueError(f'{name} must be an integer minute in [0, {_MAX_MINUTE}]')
    return value


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')
    return float(value)


@dataclass(frozen=True)
class WeatherRecord:
    minute: int
    received_at: int
    wind: tuple[float, float]
    temperature_c: float
    relative_humidity_pct: float
    rain_mm_per_hour: float

    def as_dict(self):
        return dict(minute=self.minute, received_at=self.received_at,
                    wind=list(self.wind), temperature_c=self.temperature_c,
                    relative_humidity_pct=self.relative_humidity_pct,
                    rain_mm_per_hour=self.rain_mm_per_hour)


@dataclass(frozen=True, init=False)
class WeatherProfile:
    records: tuple[WeatherRecord, ...]
    sha256: str

    @classmethod
    def from_records(cls, records):
        if not isinstance(records, (list, tuple)) or not records:
            raise ValueError('weather profile must contain records')
        result = []
        for item in records:
            if not isinstance(item, dict) or set(item) not in (_FIELDS, _FIELDS | {'received_at'}):
                raise ValueError('weather record has missing or unexpected fields')
            observed = _minute(item['minute'], 'minute')
            received = _minute(item.get('received_at', observed), 'received_at')
            if received < observed:
                raise ValueError('weather cannot be received before it is observed')
            if result and observed <= result[-1].minute:
                raise ValueError('minute must be strictly increasing')
            wind = item['wind']
            if not isinstance(wind, (list, tuple)) or len(wind) != 2:
                raise ValueError('wind must contain two model-vector components')
            result.append(WeatherRecord(
                observed, received,
                tuple(_number(v, 'wind component', -3, 3) for v in wind),
                _number(item['temperature_c'], 'temperature_c', -50, 60),
                _number(item['relative_humidity_pct'], 'relative_humidity_pct', 0, 100),
                _number(item['rain_mm_per_hour'], 'rain_mm_per_hour', 0, 200)))
        if result[0].minute != 0:
            raise ValueError('profile must provide conditions from minute zero')
        obj = object.__new__(cls)
        object.__setattr__(obj, 'records', tuple(result))
        payload = dict(version=VERSION, records=[record.as_dict() for record in result])
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        object.__setattr__(obj, 'sha256', hashlib.sha256(encoded).hexdigest())
        return obj


def _validate_state(profile, state):
    if not isinstance(profile, WeatherProfile) or not hasattr(profile, 'records'):
        raise ValueError('expected a validated WeatherProfile')
    if not isinstance(state, dict) or set(state) != {'version', 'profile_sha256', 'minute', 'moisture_proxy'}:
        raise ValueError('invalid weather state fields')
    if state['version'] != VERSION or state['profile_sha256'] != profile.sha256:
        raise ValueError('state does not belong to this profile')
    _minute(state['minute'], 'state minute')
    _number(state['moisture_proxy'], 'moisture_proxy', 0, 1)


def initial_state(profile, *, moisture_proxy=0.5):
    """Create a fresh state; 0 is dry and 1 is wet, in arbitrary units."""
    if not isinstance(profile, WeatherProfile) or not hasattr(profile, 'records'):
        raise ValueError('expected a validated WeatherProfile')
    return dict(version=VERSION, profile_sha256=profile.sha256, minute=0,
                moisture_proxy=_number(moisture_proxy, 'moisture_proxy', 0, 1))


def advance(profile, state, until_minute):
    """Return a new state after applying conditions through [minute, until)."""
    _validate_state(profile, state)
    end = _minute(until_minute, 'until_minute')
    if end < state['minute']:
        raise ValueError('cannot advance backwards')
    moisture = state['moisture_proxy']
    index = 0
    while index + 1 < len(profile.records) and profile.records[index + 1].minute <= state['minute']:
        index += 1
    for minute in range(state['minute'], end):
        while index + 1 < len(profile.records) and profile.records[index + 1].minute <= minute:
            index += 1
        weather = profile.records[index]
        # Deliberate synthetic sensitivity: high RH raises and high temperature
        # lowers the target; clipping preserves monotonicity and bounds.
        target = max(0.0, min(1.0, weather.relative_humidity_pct / 100
                              - (weather.temperature_c - 20) / 100))
        moisture += (target - moisture) / 180.0
        # Rain relaxes towards wet independently, faster than the 180-minute
        # atmospheric response for sustained moderate rain. No instant jump.
        rain_fraction = -math.expm1(-weather.rain_mm_per_hour / 300.0)
        moisture += (1.0 - moisture) * rain_fraction
        moisture = max(0.0, min(1.0, moisture))
    return dict(version=VERSION, profile_sha256=profile.sha256, minute=end, moisture_proxy=moisture)


def spread_factor(state, *, base_factor=1.0):
    """Return a clamped kernel-compatible factor; no simulator is modified."""
    if not isinstance(state, dict) or set(state) != {'version', 'profile_sha256', 'minute', 'moisture_proxy'} or state['version'] != VERSION:
        raise ValueError('invalid weather state')
    _minute(state['minute'], 'state minute')
    if not isinstance(state['profile_sha256'], str) or len(state['profile_sha256']) != 64:
        raise ValueError('invalid profile digest')
    moisture = _number(state['moisture_proxy'], 'moisture_proxy', 0, 1)
    base = _number(base_factor, 'base_factor', 0.25, 4.0)
    return max(0.25, min(4.0, base * (2.0 - 1.5 * moisture)))


def conditions(profile, at_minute):
    """Return active true conditions for the trusted integrator, not actors."""
    if not isinstance(profile, WeatherProfile) or not hasattr(profile, 'records'):
        raise ValueError('expected a validated WeatherProfile')
    now = _minute(at_minute, 'at_minute')
    return next(record.as_dict() for record in reversed(profile.records) if record.minute <= now)


def observation(profile, at_minute):
    """Return newest received weather as detached public data, or None."""
    if not isinstance(profile, WeatherProfile) or not hasattr(profile, 'records'):
        raise ValueError('expected a validated WeatherProfile')
    now = _minute(at_minute, 'at_minute')
    available = [record for record in profile.records if record.received_at <= now]
    if not available:
        return None
    result = available[-1].as_dict()
    result['observed_at'] = result.pop('minute')
    result.update(as_of=now, source='synthetic_exercise_input',
                  wind_units='relative_model_units',
                  limitations='Uncalibrated exercise conditions; not verified weather or fuel moisture')
    return result


def checkpoint(profile, state):
    """Return a detached JSON-compatible checkpoint tied to this profile."""
    _validate_state(profile, state)
    return dict(state)


def restore(profile, saved):
    """Validate and restore a checkpoint without mutating it or the profile."""
    _validate_state(profile, saved)
    return dict(saved)
