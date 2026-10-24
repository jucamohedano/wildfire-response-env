"""Offline evaluator metadata gate; no fetching, physics or policy observations.

Context schema (all fields required except geography, which may be absent/null)::

    {"version": "wildfire-offline-context-v1", "role": "evaluator_only",
     "sources": [{"id": "weather-source", "url": "https://example.org/weather.csv",
                  "sha256": "<64 lowercase hex digits>", "format": "csv"}],
     "geography": {"crs": "EPSG:4326", "frame": "longitude_latitude_degrees",
                   "verified": true, "bbox": [-4, 39, -3, 41],
                   "source_id": "weather-source"},
     "weather": [{"id": "weather-001", "source_id": "weather-source",
                  "observed_at": "2021-07-01T10:00:00Z",
                  "available_at": "2021-07-01T10:05:00Z",
                  "longitude": -3.5, "latitude": 40,
                  "wind_speed_m_s": 4, "wind_from_degrees": 270,
                  "temperature_c": 30, "humidity_percent": 40}]}

At most 100 sources, 1000 weather rows and 1 MiB of JSON are accepted. IDs are
1-64 ASCII letters/digits/underscore/hyphen, starting with a letter or digit.
Source format is one of csv/json/geojson/xml/netcdf/geotiff (metadata only).
Source URLs cannot contain userinfo or credential query parameters. Query names
are URL-decoded and compared without case/punctuation; token, API-key, password,
secret and signed-access credential names are rejected, including empty values.
Geography declares verified WGS84 longitude/latitude degrees; bbox order is
west, south, east, north. Degenerate or antimeridian-crossing boxes are rejected.
Wind is meteorological FROM, clockwise degrees from true north, in m/s. No
conversion to an engine frame occurs. Temperature bounds [-90, 60] Celsius are
schema plausibility limits, not a claim that a measurement is correct.

Times use aware ISO seconds with optional microseconds and Z or +/-HH:MM.
Unknown offset -00:00 is rejected. Observed time must not follow availability.
Assessment accepts an aware ISO string or datetime incident_time and admits
only rows with observed_at <= available_at <= incident_time inside the bbox,
including its edges. Reports return eligible IDs, never observation values or
excluded row IDs. No freshness window, interpolation or incident-location match
is inferred; readiness_for_join means at least one such metadata-eligible row.

URLs/hashes, verification and availability are declarations, not authenticity,
publication/receipt proof, terrain, roads or historical replay certification.
Reports explicitly separate declared_metadata_validated from the always-false
source_authenticity_verified. An omitted or nonexistent CLI context file yields
a not-ready report with context_status/quality_flags, never replacement data.
All outputs remain evaluator-only. V1 anchors omit weather values/coordinates:
calendar-wrapper mismatches cannot verify observations, do not establish that
original measurements were wrong, and are never repaired or replaced by zero.
"""

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

from ..grounded.anchors import load_anchor_pack


VERSION = 'wildfire-offline-context-v1'
MAX_FILE_BYTES = 1024 * 1024
MAX_WEATHER_ROWS = 1000
MAX_SOURCES = 100
_CONTEXT_FIELDS = {'version', 'role', 'sources', 'weather'}
_SOURCE_FIELDS = {'id', 'url', 'sha256', 'format'}
_GEOGRAPHY_FIELDS = {'crs', 'frame', 'verified', 'bbox', 'source_id'}
_WEATHER_FIELDS = {
    'id', 'source_id', 'observed_at', 'available_at', 'longitude', 'latitude',
    'wind_speed_m_s', 'wind_from_degrees', 'temperature_c', 'humidity_percent',
}
_ANCHOR_FIELDS = {
    'id', 'source_incident_id', 'province_code', 'stratum', 'detection_local',
    'ground_response_minutes', 'historical_reference', 'quality_flags',
}
_FORMATS = {'csv', 'json', 'geojson', 'xml', 'netcdf', 'geotiff'}
_CREDENTIAL_QUERY_MARKERS = ('token', 'apikey', 'password', 'passwd', 'secret', 'credential', 'signature')
_CREDENTIAL_QUERY_NAMES = {'key', 'accesskey', 'subscriptionkey', 'authorization', 'auth', 'username', 'user', 'sig'}
_AWARE_TIME = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])'
)
_LIMITATIONS = [
    'Declared metadata only; source authenticity is unverified.',
    'Source hash strings are declarations; raw cached file bytes and row-to-source lineage are not checked here.',
    'Declared available_at is not proof of publication or receipt.',
    'No weather freshness window or incident-to-station location match is verified.',
    'No terrain, roads, engine wind conversion, interpolation or historical replay is verified.',
]


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f'Invalid {label} fields')


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('Invalid bounded identifier')
    return value


def _aware_time(value):
    if type(value) is not str or not _AWARE_TIME.fullmatch(value) or value.endswith('-00:00'):
        raise ValueError('Expected a timezone-aware ISO timestamp with a known offset')
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError('Invalid aware source timestamp') from error


def _incident_time(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Incident time must be timezone-aware')
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError) as error:
            raise ValueError('Invalid aware incident time') from error
    return _aware_time(value)


def _finite(value, minimum=None, maximum=None):
    if type(value) not in (int, float):
        raise ValueError('Expected a finite numeric value in the declared units')
    try:
        finite = math.isfinite(value)
    except OverflowError as error:
        raise ValueError('Numeric value exceeds finite bounds') from error
    if not finite or minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError('Numeric value is outside declared bounds')
    return value


def _source_url(value):
    if (type(value) is not str or not 0 < len(value) <= 2048 or not value.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in value)):
        raise ValueError('Expected a bounded HTTPS source URL')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        if (parsed.scheme != 'https' or not hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or port == 0 or '\\' in value):
            raise ValueError('Invalid HTTPS source reference')
        for name, _ in parse_qsl(parsed.query.replace(';', '&'), keep_blank_values=True,
                                 encoding='utf-8', errors='strict'):
            normalized = re.sub(r'[^a-z0-9]', '', name.lower())
            if ('%' in name or not name.isascii() or normalized in _CREDENTIAL_QUERY_NAMES
                    or any(marker in normalized for marker in _CREDENTIAL_QUERY_MARKERS)):
                raise ValueError('Credential-bearing source references are not accepted')
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            if len(hostname) > 253 or any(not re.fullmatch(
                    r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                    for label in hostname.split('.')):
                raise ValueError('Invalid HTTPS source hostname')
    except ValueError:
        raise ValueError('Invalid or credential-bearing HTTPS source reference') from None


def _json_bytes(value):
    try:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                              allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Expected finite bounded JSON data') from error
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError('JSON exceeds the 1 MiB file cap')
    return encoded


def validate_context(context):
    """Validate exact metadata fields, references, units, coordinates and times.

    Returns the input object; no caching, verification by transport, mutation,
    zero filling or inference is performed. Missing geography is permitted.
    """
    if type(context) is not dict or set(context) not in (
            _CONTEXT_FIELDS, _CONTEXT_FIELDS | {'geography'}):
        raise ValueError('Invalid context fields')
    if context['version'] != VERSION or context['role'] != 'evaluator_only':
        raise ValueError('Invalid evaluator context provenance')
    sources = context['sources']
    weather = context['weather']
    if type(sources) is not list or len(sources) > MAX_SOURCES:
        raise ValueError('Too many or invalid source references')
    if type(weather) is not list or len(weather) > MAX_WEATHER_ROWS:
        raise ValueError('Too many or invalid weather rows')
    source_ids = set()
    for source in sources:
        _fields(source, _SOURCE_FIELDS, 'source')
        identifier = _identifier(source['id'])
        if identifier in source_ids:
            raise ValueError('Duplicate source ID')
        source_ids.add(identifier)
        _source_url(source['url'])
        if type(source['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', source['sha256']):
            raise ValueError('Expected a lowercase SHA256 source digest')
        if type(source['format']) is not str or source['format'] not in _FORMATS:
            raise ValueError('Unsupported or ambiguous source format')
    geography = context.get('geography')
    if geography is not None:
        _fields(geography, _GEOGRAPHY_FIELDS, 'geography')
        if (geography['crs'] != 'EPSG:4326' or geography['frame'] != 'longitude_latitude_degrees'
                or geography['verified'] is not True):
            raise ValueError('Geography requires a declared verified CRS and coordinate frame')
        if _identifier(geography['source_id']) not in source_ids:
            raise ValueError('Unknown geography source ID')
        bounds = geography['bbox']
        if type(bounds) is not list or len(bounds) != 4:
            raise ValueError('Expected west/south/east/north bounding box')
        west, south, east, north = bounds
        _finite(west, -180, 180)
        _finite(east, -180, 180)
        _finite(south, -90, 90)
        _finite(north, -90, 90)
        if west >= east or south >= north:
            raise ValueError('Degenerate or crossing geographic bounds')
    row_ids = set()
    for row in weather:
        _fields(row, _WEATHER_FIELDS, 'weather row')
        identifier = _identifier(row['id'])
        if identifier in row_ids:
            raise ValueError('Duplicate weather row ID')
        row_ids.add(identifier)
        if _identifier(row['source_id']) not in source_ids:
            raise ValueError('Unknown weather source ID')
        observed = _aware_time(row['observed_at'])
        available = _aware_time(row['available_at'])
        if observed > available:
            raise ValueError('Observation follows its declared availability')
        _finite(row['longitude'], -180, 180)
        _finite(row['latitude'], -90, 90)
        _finite(row['wind_speed_m_s'], 0)
        direction = _finite(row['wind_from_degrees'], 0, 360)
        if direction == 360:
            raise ValueError('Wind FROM degrees must be in [0, 360)')
        _finite(row['temperature_c'], -90, 60)
        _finite(row['humidity_percent'], 0, 100)
    _json_bytes(context)
    return context


def assess_source_anchor(anchor):
    """Summarize a v1 anchor already validated through its parent anchor pack.

    Only shape, bounded ID and flags are checked here; full source chronology
    and pack digest validation belong to load_anchor_pack/validate_anchor_pack.
    Weather missing means values absent from v1, not absent in the original.
    """
    _fields(anchor, _ANCHOR_FIELDS, 'v1 anchor')
    identifier = anchor['id']
    if type(identifier) is not str or not re.fullmatch(r'anchor-00[0-3]', identifier):
        raise ValueError('Invalid v1 anchor ID')
    flags = anchor['quality_flags']
    if (type(flags) is not list or len(flags) > 32 or any(
            type(flag) is not str or not re.fullmatch(r'[a-z0-9_]{1,80}', flag) for flag in flags)
            or len(set(flags)) != len(flags)):
        raise ValueError('Invalid source quality flags')
    return dict(
        version=VERSION, role='evaluator_only', actor_input_eligible=False,
        source_authenticity_verified=False,
        anchor_id=identifier, source_quality_flags=sorted(flags),
        weather_unverified=True, weather_missing=True, georeference_missing=True,
        weather_calendar_mismatch=bool(set(flags) & {
            'weather_hour_date_mismatch', 'weather_hour_year_mismatch'}),
        readiness_for_historical_replay=False,
    )


def assess_context(context, incident_time):
    """Report only IDs eligible under declared coverage and as-of metadata.

    Future observations/availability and outside-coverage rows are excluded,
    not repaired. No context values are exported as engine/policy observations.
    """
    validate_context(context)
    incident = _incident_time(incident_time)
    geography = context.get('geography')
    eligible = []
    excluded = dict(future_observation=0, future_availability=0,
                    outside_geography=0, missing_geography=0)
    for row in context['weather']:
        if _aware_time(row['observed_at']) > incident:
            excluded['future_observation'] += 1
        elif _aware_time(row['available_at']) > incident:
            excluded['future_availability'] += 1
        elif geography is None:
            excluded['missing_geography'] += 1
        else:
            west, south, east, north = geography['bbox']
            if west <= row['longitude'] <= east and south <= row['latitude'] <= north:
                eligible.append(row['id'])
            else:
                excluded['outside_geography'] += 1
    flags = []
    if geography is None:
        flags.append('geography_missing')
    if not eligible:
        flags.append('weather_missing_at_incident_within_coverage')
    if excluded['future_observation'] or excluded['future_availability']:
        flags.append('future_weather_excluded')
    if excluded['outside_geography']:
        flags.append('weather_outside_coverage_excluded')
    return dict(
        version=VERSION, role='evaluator_only', actor_input_eligible=False,
        declared_metadata_validated=True, source_authenticity_verified=False,
        incident_time=incident.isoformat(), geography_missing=geography is None,
        weather_missing=not eligible, weather_unverified=True,
        eligible_weather_ids=sorted(eligible), excluded_weather_counts=excluded,
        readiness_for_join=bool(eligible), historical_replay_ready=False,
        quality_flags=sorted(flags), limitations=list(_LIMITATIONS),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _load_context(path):
    with Path(path).open('rb') as stream:
        payload = stream.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError('Context exceeds the 1 MiB file cap')
    try:
        context = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Invalid context JSON') from error
    return validate_context(context)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline evaluator-only anchor/context quality report')
    parser.add_argument('--pack', required=True)
    parser.add_argument('--context')
    parser.add_argument('--incident-time')
    parser.add_argument('--output', required=True)
    options = parser.parse_args(argv)
    if bool(options.context) != bool(options.incident_time):
        parser.error('--context and --incident-time must be supplied together')
    destination = Path(options.output)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Quality report output already exists')
    pack = load_anchor_pack(options.pack)
    context_report = None
    context_status = 'not_provided'
    if options.context:
        incident = _incident_time(options.incident_time)
        try:
            supplied_context = _load_context(options.context)
        except FileNotFoundError:
            context_status = 'file_missing'
        else:
            context_report = assess_context(supplied_context, incident)
            context_status = 'assessed'
    report = dict(
        version=VERSION, role='evaluator_only', actor_input_eligible=False,
        declared_metadata_validated=context_report is not None, source_authenticity_verified=False,
        historical_replay_ready=False, source_pack_sha256=pack['sha256'],
        anchors=[assess_source_anchor(anchor) for anchor in pack['anchors']],
        context=context_report, context_status=context_status,
        quality_flags=[] if context_report is not None else [
            'context_file_missing' if context_status == 'file_missing' else 'context_not_provided'],
        geography_missing=True if context_report is None else context_report['geography_missing'],
        weather_missing=True if context_report is None else context_report['weather_missing'],
        readiness_for_join=False if context_report is None else context_report['readiness_for_join'],
        limitations=list(_LIMITATIONS),
    )
    payload = _json_bytes(report)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(payload)
    print(json.dumps(dict(version=VERSION, role='evaluator_only', anchors=len(report['anchors']),
                         context_status=context_status, declared_metadata_validated=context_report is not None,
                         source_authenticity_verified=False,
                         readiness_for_join=report['readiness_for_join'], historical_replay_ready=False,
                         actor_input_eligible=False)))
    return report


if __name__ == '__main__':
    main()
