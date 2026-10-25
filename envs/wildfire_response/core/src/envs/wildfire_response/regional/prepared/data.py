"""Validate bounded, caller-pinned prepared inputs without geospatial dependencies."""
import copy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from ..malaga.pack import validate_pack
from ..ops.generator import digest


VERSION = 'wildfire-malaga-prepared-inputs-v1'
MAX_BYTES = 2_000_000
WEATHER_FIELDS = {'observed_at_utc', 'temperature_c', 'relative_humidity_percent',
                  'wind_speed_m_s', 'wind_direction_degrees', 'precipitation_mm'}
MAPPING_VERSION = 'wildfire-nffl-exercise-mapping-v1'
FUEL_MAPPING = {code: dict(terrain='field' if code <= 3 else 'scrub' if code <= 7 else 'woodland',
                          initial_fuel=.75 if code <= 3 else 1.0 if code <= 7 else .9)
                for code in range(1, 11)}
UNKNOWN_FUEL = dict(terrain='scrub', initial_fuel=1.0)


def utc_time(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('Expected an explicit UTC timestamp')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate input field')
        result[key] = value
    return result


def validate_inputs(value, expected_sha256):
    if (type(value) is not dict or not isinstance(expected_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_sha256)
            or value.get('sha256') != expected_sha256
            or digest({key: item for key, item in value.items() if key != 'sha256'}) != expected_sha256):
        raise ValueError('Prepared inputs differ from the caller-pinned digest')
    required = {'version', 'role', 'grid', 'fuel_classes', 'fuel_valid_mask', 'study_mask',
                'weather', 'episode_pack', 'episode_start_utc', 'slope_enabled',
                'historical_replay', 'provenance', 'assumptions', 'sha256'}
    if (set(value) != required or value['version'] != VERSION
            or value['role'] != 'simulation_configuration'
            or value['slope_enabled'] is not False or value['historical_replay'] is not False):
        raise ValueError('Unexpected prepared-input contract or enabled slope/replay')
    validate_pack(value['episode_pack'])
    start = datetime.fromisoformat(value['episode_pack']['detection_local']).replace(tzinfo=ZoneInfo('Europe/Madrid'))
    if utc_time(value['episode_start_utc']) != start.astimezone(timezone.utc):
        raise ValueError('Episode source local clock and UTC anchor disagree')
    grid = value['grid']
    if (set(grid) != {'width', 'height', 'cell_km', 'crs', 'transform', 'northwest_lon_lat', 'centre_lon_lat'}
            or type(grid['width']) is not int or type(grid['height']) is not int
            or (grid['width'], grid['height']) != (37, 41) or grid['cell_km'] != .5
            or grid['crs'] != 'EPSG:25830'
            or grid['transform'] != [500, 0, 297000, 0, -500, 4054500]):
        raise ValueError('Expected the prepared common metric grid')
    def matrix(name, allowed):
        rows = value[name]
        if (not isinstance(rows, list) or len(rows) != grid['height']
                or any(not isinstance(row, list) or len(row) != grid['width'] for row in rows)
                or any(type(item) is not int or item not in allowed for row in rows for item in row)):
            raise ValueError('Invalid raster matrix: ' + name)
    matrix('fuel_classes', set(range(11)))
    matrix('fuel_valid_mask', {0, 1})
    matrix('study_mask', {0, 1})
    for row in range(grid['height']):
        for column in range(grid['width']):
            if bool(value['fuel_classes'][row][column]) != bool(value['fuel_valid_mask'][row][column]):
                raise ValueError('Unknown fuel and validity mask disagree')
    centres = grid['centre_lon_lat']
    if (not isinstance(centres, list) or len(centres) != grid['height']
            or any(not isinstance(row, list) or len(row) != grid['width'] for row in centres)):
        raise ValueError('Missing grid-coordinate lookup')
    coordinates = [grid['northwest_lon_lat'], *(point for row in centres for point in row)]
    if any(not isinstance(point, list) or len(point) != 2 or not all(finite(item) for item in point)
           or not (-5.5 <= point[0] <= -4.8 and 36.2 <= point[1] <= 36.9) for point in coordinates):
        raise ValueError('Invalid geographic coordinate lookup')
    weather = value['weather']
    if (set(weather) != {'station', 'source_clock', 'records'} or weather['station'] != 'MA04 Estepona'
            or weather['source_clock'] != 'UTC' or not isinstance(weather['records'], list)
            or not 1 <= len(weather['records']) <= 384):
        raise ValueError('Unexpected weather source')
    previous = None
    for record in weather['records']:
        if type(record) is not dict or set(record) != WEATHER_FIELDS:
            raise ValueError('Unexpected weather fields')
        observed = utc_time(record['observed_at_utc'])
        if previous is not None and observed <= previous:
            raise ValueError('Weather timestamps must be unique and increasing')
        previous = observed
        if (not all(finite(record[key]) for key in WEATHER_FIELDS - {'observed_at_utc'})
                or not -100 <= record['temperature_c'] <= 100
                or not 0 <= record['relative_humidity_percent'] <= 100
                or not 0 <= record['wind_speed_m_s'] <= 150
                or not 0 <= record['wind_direction_degrees'] <= 360
                or not 0 <= record['precipitation_mm'] <= 1000):
            raise ValueError('Invalid weather measurement')
    start = utc_time(value['episode_start_utc'])
    if (utc_time(weather['records'][0]['observed_at_utc']) > start - timedelta(minutes=5)
            or utc_time(weather['records'][-1]['observed_at_utc']) < start + timedelta(minutes=120)):
        raise ValueError('Weather does not cover initial delayed context and the full episode')
    if not any(any(row) for row in value['study_mask']):
        raise ValueError('Empty study mask')
    return value


def load_inputs(source, *, expected_sha256):
    if isinstance(source, dict):
        value = copy.deepcopy(source)
        if len(json.dumps(value, allow_nan=False).encode()) > MAX_BYTES:
            raise ValueError('Prepared inputs exceed byte limit')
    else:
        path = Path(source)
        if path.is_symlink():
            raise ValueError('Input bundle must not be a symlink')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Prepared inputs exceed byte limit')
        value = json.loads(raw, object_pairs_hook=_unique)
    return validate_inputs(value, expected_sha256)
