"""Bounded offline EGIF anchors for private retrospective evaluation.

Detection is a naive source-local timestamp, not ignition or verified UTC.
Final areas define evaluator strata, not initial actor observations. Weather
values are deliberately omitted: even nonzero wind is unverified while the
meaning and calendar wrapper of the source weather hour remain unresolved.
No copyright, reuse permission, or redistribution rights are asserted.
"""

import argparse
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ElementTree
import zipfile


VERSION = 'wildfire-spanish-anchors-v1'
EXPECTED_SOURCE_ARCHIVE_SHA256 = '316e34acf653079efd6ea895f0991681468af6c9bc84897030a9bbde220d60e0'
MAX_PACK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 10000
_PACK_FIELDS = {
    'version', 'source_archive_sha256', 'source_year', 'role',
    'actor_input_eligible', 'anchors', 'sha256',
}
_ANCHOR_FIELDS = {
    'id', 'source_incident_id', 'province_code', 'stratum', 'detection_local',
    'ground_response_minutes', 'historical_reference', 'quality_flags',
}
_HISTORICAL_FIELDS = {
    'ground_arrival': 'pif_tiempos/llegadapmt',
    'air_arrival': 'pif_tiempos/llegadapmae',
    'controlled': 'pif_tiempos/controlado',
    'extinguished': 'pif_tiempos/extinguido',
    'burned_wooded_ha': 'pif_perdidas/superficiearboladatotal',
    'burned_unwooded_ha': 'pif_perdidas/superficienoarboladatotal',
}
_BASE_FLAGS = {
    'detection_local_timezone_unresolved', 'weather_not_actor_input',
    'weather_hour_semantics_unresolved',
}
_WEATHER_FLAGS = {
    'weather_hour_missing', 'weather_hour_invalid', 'weather_hour_date_mismatch',
    'weather_hour_year_mismatch', 'zero_wind_temperature_missing_humidity',
}
_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?')
_BUCKETS = (('small', '4'), ('small', '28'), ('large', '21'), ('large', 'other'))


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f'Invalid {label} fields')


def _timestamp(value):
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        raise ValueError('Expected a naive ISO source timestamp')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError('Invalid source timestamp') from error


def _number(value):
    if type(value) is not str or not 0 < len(value) <= 128 or value != value.strip():
        raise ValueError('Expected a bounded numeric source string')
    if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
        raise ValueError('Invalid source number')
    try:
        numeric = float(value)
    except ValueError as error:
        raise ValueError('Invalid source number') from error
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError('Expected a finite nonnegative source number')
    return numeric


def _chronology(detection, historical):
    times = {field: None if historical[field] is None else _timestamp(historical[field])
             for field in ('ground_arrival', 'air_arrival', 'controlled', 'extinguished')}
    ground = times['ground_arrival']
    if ground is None or ground < detection:
        raise ValueError('Ground arrival must follow detection')
    for field in ('air_arrival', 'controlled', 'extinguished'):
        if times[field] is not None and times[field] < detection:
            raise ValueError('Source event precedes detection')
    for end in ('controlled', 'extinguished'):
        if times[end] is not None and any(
                times[arrival] is not None and times[arrival] > times[end]
                for arrival in ('ground_arrival', 'air_arrival')):
            raise ValueError('Source arrival follows control or extinction')
    if times['controlled'] is not None and times['extinguished'] is not None:
        if times['controlled'] > times['extinguished']:
            raise ValueError('Control follows extinction')
    duration = (ground - detection).total_seconds() / 60
    if not 0 <= duration <= 120:
        raise ValueError('Ground response is outside the bounded duration')
    return duration


def _bucket(stratum, province):
    if stratum == 'small' and province in ('4', '28'):
        return stratum, province
    if stratum == 'large':
        return stratum, '21' if province == '21' else 'other'
    raise ValueError('Unexpected anchor stratum or province')


def anchor_pack_sha256(pack):
    """Hash canonical UTF-8 JSON, excluding only the top-level sha256 field."""
    if type(pack) is not dict:
        raise ValueError('Expected an anchor pack object')
    try:
        payload = json.dumps({key: value for key, value in pack.items() if key != 'sha256'},
                             sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Anchor pack must contain finite JSON data') from error
    return hashlib.sha256(payload).hexdigest()


def validate_anchor_pack(pack):
    """Validate the exact private schema, chronology, selection strata and digest.

    Returns the validated object without caching it. A digest detects accidental
    tampering; it does not authenticate a pack from an untrusted author.
    """
    _keys(pack, _PACK_FIELDS, 'pack')
    if (pack['version'] != VERSION or type(pack['source_year']) is not int
            or pack['source_year'] != 2021 or pack['role'] != 'retrospective_evaluator_evidence'
            or pack['actor_input_eligible'] is not False):
        raise ValueError('Invalid evaluator provenance')
    if pack['source_archive_sha256'] != EXPECTED_SOURCE_ARCHIVE_SHA256:
        raise ValueError('Unexpected source archive SHA256')
    if type(pack['anchors']) is not list or len(pack['anchors']) != 4:
        raise ValueError('Expected exactly four anchors')
    source_ids = set()
    for index, anchor in enumerate(pack['anchors']):
        _keys(anchor, _ANCHOR_FIELDS, 'anchor')
        if anchor['id'] != f'anchor-{index:03d}':
            raise ValueError('Expected unique sequential opaque anchor IDs')
        identifier = anchor['source_incident_id']
        if type(identifier) is not str or not re.fullmatch(r'[0-9]{1,32}', identifier):
            raise ValueError('Invalid source incident identifier')
        if identifier in source_ids:
            raise ValueError('Duplicate source incident identifier')
        source_ids.add(identifier)
        province = anchor['province_code']
        if (type(province) is not str or not re.fullmatch(r'[1-9][0-9]?', province)
                or int(province) > 52):
            raise ValueError('Invalid province code')
        if _bucket(anchor['stratum'], province) != _BUCKETS[index]:
            raise ValueError('Unexpected anchor bucket order')
        detection = _timestamp(anchor['detection_local'])
        if detection.year != 2021:
            raise ValueError('Detection must belong to the source year')
        historical = anchor['historical_reference']
        _keys(historical, _HISTORICAL_FIELDS, 'historical reference')
        area = sum(_number(historical[field]) for field in ('burned_wooded_ha', 'burned_unwooded_ha'))
        if not math.isfinite(area) or not (area < 1 if anchor['stratum'] == 'small' else area > 500):
            raise ValueError('Forest area does not match the stratum')
        duration = anchor['ground_response_minutes']
        if (type(duration) not in (int, float) or not 0 <= duration <= 120
                or duration != _chronology(detection, historical)):
            raise ValueError('Invalid ground response duration')
        flags = anchor['quality_flags']
        if (type(flags) is not list or any(type(flag) is not str for flag in flags)
                or flags != sorted(set(flags)) or not _BASE_FLAGS.issubset(flags)
                or not set(flags).issubset(_BASE_FLAGS | _WEATHER_FLAGS)):
            raise ValueError('Invalid quality flags')
        if ('weather_hour_year_mismatch' in flags and 'weather_hour_date_mismatch' not in flags
                or 'weather_hour_missing' in flags and 'weather_hour_invalid' in flags
                or set(flags) & {'weather_hour_missing', 'weather_hour_invalid'}
                and set(flags) & {'weather_hour_date_mismatch', 'weather_hour_year_mismatch'}):
            raise ValueError('Inconsistent weather quality flags')
    if (type(pack['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', pack['sha256'])
            or pack['sha256'] != anchor_pack_sha256(pack)):
        raise ValueError('Anchor pack SHA256 mismatch')
    return pack


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def load_anchor_pack(path):
    """Load at most 1 MiB of JSON and return an independently validated pack."""
    with Path(path).open('rb') as stream:
        payload = stream.read(MAX_PACK_BYTES + 1)
    if len(payload) > MAX_PACK_BYTES:
        raise ValueError('Anchor pack exceeds the size cap')
    try:
        pack = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Invalid anchor pack JSON') from error
    return validate_anchor_pack(pack)


def _clean(value):
    if value is None or value.strip().lower() in ('', 'nan', 'null', 'none'):
        return None
    return value.strip()


def _weather_flags(element, detection):
    flags = set(_BASE_FLAGS)
    hour = _clean(element.findtext('pif_condiciones/hora'))
    if hour is None:
        flags.add('weather_hour_missing')
    else:
        try:
            weather_time = _timestamp(hour)
        except ValueError:
            flags.add('weather_hour_invalid')
        else:
            if weather_time.date() != detection.date():
                flags.add('weather_hour_date_mismatch')
            if weather_time.year != detection.year:
                flags.add('weather_hour_year_mismatch')
    wind = _clean(element.findtext('pif_condiciones/velocidadviento'))
    temperature = _clean(element.findtext('pif_condiciones/tempmaxima'))
    humidity = _clean(element.findtext('pif_condiciones/humrelativa'))
    try:
        zero_pair = _number(wind) == 0 and _number(temperature) == 0
    except ValueError:
        zero_pair = False
    if zero_pair and humidity is None:
        flags.add('zero_wind_temperature_missing_humidity')
    return sorted(flags)


def _candidate(element, identifier):
    province = _clean(element.findtext('pif_localizacion/idprovincia'))
    community = _clean(element.findtext('pif_localizacion/idcomunidad'))
    if province is None or not re.fullmatch(r'[1-9][0-9]?', province) or int(province) > 52:
        return None
    if province in ('4', '21', '28') and community != {'4': '4', '21': '4', '28': '16'}[province]:
        return None
    historical = {field: _clean(element.findtext(source)) for field, source in _HISTORICAL_FIELDS.items()}
    try:
        area = sum(_number(historical[field]) for field in ('burned_wooded_ha', 'burned_unwooded_ha'))
        if not math.isfinite(area):
            return None
        stratum = 'small' if area < 1 else 'large' if area > 500 else None
        _bucket(stratum, province)
        detection_local = _clean(element.findtext('pif_tiempos/deteccion'))
        detection = _timestamp(detection_local)
        if detection.year != 2021:
            return None
        duration = _chronology(detection, historical)
    except ValueError:
        return None
    return dict(id='', source_incident_id=identifier, province_code=province, stratum=stratum,
                detection_local=detection_local, ground_response_minutes=duration,
                historical_reference=historical, quality_flags=_weather_flags(element, detection))


def _xml_member(archive):
    members = archive.infolist()
    xml = [item for item in members if item.filename.lower().endswith('.xml')]
    if len(members) > 128 or len(xml) != 1 or xml[0].flag_bits & 1 or xml[0].file_size > MAX_XML_BYTES:
        raise ValueError('Expected one unencrypted bounded annual XML member')
    with archive.open(xml[0]) as stream:
        total = 0
        tail = b''
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            scanned = tail + chunk.upper()
            if (total > MAX_XML_BYTES or b'<!DOCTYPE' in scanned or b'<!ENTITY' in scanned
                    or b'\x00' in scanned):
                raise ValueError('Unsafe or oversized annual XML')
            tail = scanned[-16:]
    return xml[0]


def build_anchor_pack(egif_zip, output_path):
    """Select four eligible annual-export records by SHA256 incident-ID rank.

    Bucket order is small Almeria, small Madrid, large Huelva, large other.
    Source fields are read with stdlib streaming XML; no audit scripts, samples,
    services, narratives, cause fields or identities enter the pack. The pinned
    annual archive is mandatory. Output is created exclusively with mode 0600.
    """
    output_path = Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError('Anchor output already exists')
    with Path(egif_zip).open('rb') as stream:
        payload = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError('Source archive exceeds the size cap')
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SOURCE_ARCHIVE_SHA256:
        raise ValueError('Unexpected source archive SHA256')
    chosen = {}
    identifiers = set()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = _xml_member(archive)
            with archive.open(member) as stream:
                parser = ElementTree.iterparse(stream, events=('start', 'end'))
                _, root = next(parser)
                for event, element in parser:
                    if event != 'end' or element.tag != 'Pif':
                        continue
                    if element.findtext('pif_comun/anio') != '2021':
                        raise ValueError('Unexpected source record year')
                    identifier = _clean(element.findtext('numeroparte'))
                    if (identifier is None or not re.fullmatch(r'[0-9]{1,32}', identifier)
                            or identifier in identifiers):
                        raise ValueError('Expected unique numeric source incident IDs')
                    identifiers.add(identifier)
                    if len(identifiers) > MAX_RECORDS:
                        raise ValueError('Source record count exceeds the cap')
                    candidate = _candidate(element, identifier)
                    if candidate is not None:
                        bucket = _bucket(candidate['stratum'], candidate['province_code'])
                        rank = hashlib.sha256(identifier.encode('utf-8')).hexdigest(), identifier
                        if bucket not in chosen or rank < chosen[bucket][0]:
                            chosen[bucket] = rank, candidate
                    element.clear()
                    root.clear()
    except (zipfile.BadZipFile, ElementTree.ParseError, StopIteration) as error:
        raise ValueError('Invalid annual EGIF XML archive') from error
    if set(chosen) != set(_BUCKETS):
        raise ValueError('Missing eligible anchor bucket')
    anchors = [dict(chosen[bucket][1], id=f'anchor-{index:03d}') for index, bucket in enumerate(_BUCKETS)]
    pack = dict(version=VERSION, source_archive_sha256=digest, source_year=2021,
                role='retrospective_evaluator_evidence', actor_input_eligible=False, anchors=anchors)
    pack['sha256'] = anchor_pack_sha256(pack)
    validate_anchor_pack(pack)
    serialized = (json.dumps(pack, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with os.fdopen(os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(serialized)
    return pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--egif-zip', required=True)
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    pack = build_anchor_pack(options.egif_zip, options.output)
    print(json.dumps(dict(version=pack['version'], sha256=pack['sha256'], anchors=len(pack['anchors']))))


if __name__ == '__main__':
    main()
