"""Build evaluator-only Sierra Bermeja evidence from pinned, local source files."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import time
import zipfile
from zoneinfo import ZoneInfo

from eval.audit_operational_data import egif_rows, forest_area


VERSION = 'wildfire-malaga-evidence-v1'
MAX_JSON = 40_000_000
MAX_ARCHIVE = 40_000_000
MAX_EXPANDED = 300_000_000
LAYERS = ('areaOfInterestA', 'observedEventA', 'imageFootprintA')
CONTEXT_LAYERS = ('transportationL', 'builtUpA', 'naturalLandUseA', 'facilitiesA')
PROPERTIES = {
    'emsr_id', 'glide_no', 'area_id', 'locality', 'map_type', 'event_type',
    'obj_desc', 'det_method', 'notation', 'dmg_src_id', 'or_src_id', 'area',
    'obj_type', 'name', 'info', 'damage_gra', 'cd_value', 'real',
}
WGS84_WKT = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
             '6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]]')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def load_json(data):
    if len(data) > MAX_JSON:
        raise ValueError('JSON exceeds size cap')
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError('Nonfinite JSON value')))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def pinned_file(root, source):
    filename = source['filename']
    if Path(filename).name != filename or filename in ('.', '..'):
        raise ValueError('Expected a flat source filename')
    path = root / filename
    if path.is_symlink() or path.stat().st_size != source['bytes']:
        raise ValueError('Source size or path mismatch')
    if not 0 < source['bytes'] <= MAX_ARCHIVE:
        raise ValueError('Source exceeds acquisition cap')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != source['sha256']:
        raise ValueError('Source hash mismatch')
    return data


def source_table(data):
    if not 33 <= len(data) <= 1_000_000:
        raise ValueError('Invalid bounded DBF source table')
    count = struct.unpack_from('<I', data, 4)[0]
    header, length = struct.unpack_from('<HH', data, 8)
    if count > 100 or header < 33 or (header - 33) % 32 or length < 2:
        raise ValueError('Invalid DBF dimensions')
    if header + count * length > len(data) or data[header - 1] != 13:
        raise ValueError('Truncated DBF')
    fields = []
    for offset in range(32, header - 1, 32):
        descriptor = data[offset:offset + 32]
        name = descriptor[:11].split(b'\0')[0].decode('ascii')
        if descriptor[11:12] not in (b'C', b'N') or not descriptor[16]:
            raise ValueError('Unsupported DBF source field')
        fields.append((name, descriptor[16]))
    required = {'source_nam', 'src_date', 'source_tm', 'sensor_gsd', 'eventphase', 'src_id'}
    if len(fields) != len(required) or {name for name, _ in fields} != required:
        raise ValueError('Unexpected DBF source schema')
    if 1 + sum(size for _, size in fields) != length:
        raise ValueError('DBF row length mismatch')
    rows = {}
    for index in range(count):
        record = data[header + index * length:header + (index + 1) * length]
        if record[:1] != b' ':
            raise ValueError('Deleted or malformed source row')
        position = 1
        row = {}
        for name, size in fields:
            row[name] = record[position:position + size].decode('utf-8').strip()
            position += size
        identifier = row['src_id']
        if not identifier.isdigit() or identifier in rows:
            raise ValueError('Invalid or duplicate source identifier')
        row['observed_at_utc'] = None
        if row['source_tm'].endswith('Z'):
            date = datetime.strptime(row['src_date'], '%d/%m/%Y').date()
            stamp = datetime.fromisoformat(date.isoformat() + row['source_tm'])
            if stamp.utcoffset() is None:
                raise ValueError('Expected explicit source UTC clock')
            row['observed_at_utc'] = stamp.astimezone(timezone.utc).isoformat()
        rows[identifier] = row
    return rows


def check_geometry(geometry):
    depths = {'Point': 0, 'MultiPoint': 1, 'LineString': 1, 'MultiLineString': 2,
              'Polygon': 2, 'MultiPolygon': 3}
    if type(geometry) is not dict or geometry.get('type') not in depths:
        raise ValueError('Unsupported geometry')
    bounds = [180., 90., -180., -90.]
    count = 0

    def visit(value, depth):
        nonlocal count
        if type(value) is not list or not value:
            raise ValueError('Empty or malformed coordinates')
        if depth:
            for child in value:
                visit(child, depth - 1)
            return
        if len(value) != 2 or any(type(part) not in (int, float) or not math.isfinite(part)
                                  for part in value):
            raise ValueError('Expected finite 2D coordinates')
        longitude, latitude = value
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError('Invalid longitude/latitude')
        bounds[0] = min(bounds[0], longitude)
        bounds[1] = min(bounds[1], latitude)
        bounds[2] = max(bounds[2], longitude)
        bounds[3] = max(bounds[3], latitude)
        count += 1

    visit(geometry.get('coordinates'), depths[geometry['type']])
    return bounds, count


def layer_data(data, sources, verified_default_crs=False):
    value = load_json(data)
    if value.get('type') != 'FeatureCollection' or type(value.get('features')) is not list:
        raise ValueError('Expected a FeatureCollection')
    default_crs = 'crs' not in value
    if default_crs and verified_default_crs:
        value['crs'] = dict(type='name', properties=dict(name='urn:ogc:def:crs:OGC:1.3:CRS84'))
    if value.get('crs', {}).get('properties', {}).get('name') != 'urn:ogc:def:crs:OGC:1.3:CRS84':
        raise ValueError('Expected declared longitude/latitude CRS84')
    if not 0 < len(value['features']) <= 10000:
        raise ValueError('Invalid feature count')
    bounds = [180., 90., -180., -90.]
    points = 0
    features = []
    for index, feature in enumerate(value['features']):
        if feature.get('type') != 'Feature' or type(feature.get('properties')) is not dict:
            raise ValueError('Malformed feature')
        box, count = check_geometry(feature.get('geometry'))
        bounds = [min(bounds[0], box[0]), min(bounds[1], box[1]),
                  max(bounds[2], box[2]), max(bounds[3], box[3])]
        points += count
        properties = {key: item for key, item in feature['properties'].items() if key in PROPERTIES}
        references = {}
        for field in ('or_src_id', 'dmg_src_id'):
            identifier = properties.get(field)
            if identifier is not None:
                if type(identifier) is not int or str(identifier) not in sources:
                    raise ValueError('Unresolved feature source reference')
                references[field] = sources[str(identifier)]
        features.append(dict(type='Feature', geometry=feature['geometry'],
                             properties=properties, source_feature_index=index,
                             source_references=references))
    result = dict(type='FeatureCollection', crs=value['crs'], features=features,
                  role='retrospective_evaluator_evidence', actor_input_eligible=False)
    return result, dict(features=len(features), coordinate_pairs=points, bbox_crs84=bounds,
                        coordinate_interpretation='RFC7946 default with checked WGS84 PRJ' if default_crs else 'explicit CRS84 declaration',
                        member_sha256=hashlib.sha256(data).hexdigest())


def product_layers(data, product):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or any(item.flag_bits & 1 for item in infos):
            raise ValueError('Duplicate or encrypted ZIP member')
        if sum(item.file_size for item in infos) > MAX_EXPANDED:
            raise ValueError('Expanded archive exceeds cap')
        if any(item.file_size > MAX_JSON for item in infos):
            raise ValueError('Archive member exceeds cap')
        prefix = product['member_prefix']
        source_names = [name for name in names if name.startswith(prefix + '_source_')
                        and name.endswith('.dbf')]
        if len(source_names) != 1:
            raise ValueError('Expected one source table')
        sources = source_table(archive.read(source_names[0]))
        layers = {}
        requested = LAYERS + (CONTEXT_LAYERS if product['include_context'] else ())
        for layer in requested:
            members = [name for name in names if name.startswith(prefix + '_' + layer + '_')
                       and name.endswith('.json')]
            if len(members) != 1:
                raise ValueError('Expected one requested JSON layer')
            prj_name = members[0][:-5] + '.prj'
            prj = archive.read(prj_name) if prj_name in names else None
            verified_crs = prj is not None and prj.decode('ascii').strip() == WGS84_WKT
            value, summary = layer_data(archive.read(members[0]), sources, verified_crs)
            summary['crs_sidecar_sha256'] = hashlib.sha256(prj).hexdigest() if prj is not None else None
            summary['member'] = members[0]
            layers[layer] = (value, summary)
        return sources, layers


def local_clock(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not None or stamp.year != 2021:
        raise ValueError('Expected a naive 2021 Spanish local timestamp')
    interpreted = stamp.replace(tzinfo=ZoneInfo('Europe/Madrid'))
    return dict(source_timestamp=value, assumed_timezone='Europe/Madrid',
                timezone_explicit_in_source=False,
                interpreted_utc=interpreted.astimezone(timezone.utc).isoformat())


def eligible_actor_records(records, as_of):
    cutoff = datetime.fromisoformat(as_of)
    if cutoff.utcoffset() is None:
        raise ValueError('Expected aware decision time')
    result = []
    for record in records:
        if (record.get('actor_input_eligible') is not True
                or record.get('role') != 'received_observation'):
            continue
        observed, available = record.get('observed_at_utc'), record.get('available_at_utc')
        if not observed or not available:
            continue
        observed, available = datetime.fromisoformat(observed), datetime.fromisoformat(available)
        if observed.utcoffset() is None or available.utcoffset() is None:
            raise ValueError('Expected aware evidence clocks')
        if observed <= available <= cutoff:
            result.append(record)
    return result


def population_rows(data, metadata):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')), delimiter=';')
    if reader.fieldnames != ['Municipios', 'Sexo', 'Periodo', 'Total']:
        raise ValueError('Unexpected INE population schema')
    expected = {row['municipality_code']: row for row in metadata['municipalities']}
    if len(expected) != 7 or metadata['actor_input_eligible'] is not False:
        raise ValueError('Invalid population sidecar')
    selected = {}
    for line, row in enumerate(reader, 2):
        label = row['Municipios']
        code = label.split(' ', 1)[0]
        if code not in expected or row['Periodo'] != '2021' or row['Sexo'] != 'Total':
            continue
        if code in selected or row['Total'].replace('.', '').isdigit() is False:
            raise ValueError('Duplicate or invalid population row')
        recorded = expected[code]
        count = int(row['Total'].replace('.', ''))
        if (count != recorded['population_total'] or label != recorded['source_label']
                or line != recorded['source_line']):
            raise ValueError('Population provenance/value mismatch')
        selected[code] = dict(recorded)
    if set(selected) != set(expected):
        raise ValueError('Missing target population')
    return dict(role='retrospective_evaluator_evidence', actor_input_eligible=False,
                reference_date='2021-01-01', publication_date='2021-12-23',
                municipalities=[selected[code] for code in sorted(selected)],
                limitation='Municipal registered residents, not people present, exposed or evacuated; no settlement allocation.')


def terrain_summary(data):
    lines = iter(data.decode('ascii').splitlines())
    header = {}
    keys = {'ncols', 'nrows', 'xllcorner', 'yllcorner', 'cellsize', 'nodata_value'}
    values = []
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0].lower() in keys:
            if len(parts) != 2 or parts[0].lower() in header or values:
                raise ValueError('Invalid ArcGrid header')
            header[parts[0].lower()] = float(parts[1])
        else:
            values.extend(map(float, parts))
            if len(values) > 1_000_000:
                raise ValueError('Terrain grid exceeds cell cap')
    if not {'ncols', 'nrows', 'xllcorner', 'yllcorner', 'cellsize'} <= set(header):
        raise ValueError('Missing terrain header')
    if any(not math.isfinite(value) for value in [*header.values(), *values]):
        raise ValueError('Nonfinite terrain')
    columns, rows = header['ncols'], header['nrows']
    if columns < 1 or rows < 1 or not columns.is_integer() or not rows.is_integer():
        raise ValueError('Invalid terrain dimensions')
    if columns * rows != len(values) or header['cellsize'] != 25:
        raise ValueError('Terrain shape/resolution mismatch')
    valid = [value for value in values if value != header.get('nodata_value')]
    if not valid:
        raise ValueError('No usable numeric terrain values')
    return dict(header=header, cells=len(values), non_nodata_cells=len(valid),
                numeric_range=[min(valid), max(valid)], nodata_declared='nodata_value' in header,
                horizontal_crs='EPSG:25830', vertical_units_verified=False,
                historical_survey_epoch_verified=False, actor_input_eligible=False,
                limitation='Current official terrain subset; vertical units/datum, absent nodata declarations and survey epoch require verification before physics integration.')


def build_bundle(inputs, egif_zip, output, config):
    started = time.monotonic()
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    lock = load_json((Path(config) / 'sources.lock.json').read_bytes())
    claims = load_json((Path(config) / 'claims.json').read_bytes())
    if lock['version'] != VERSION or claims['version'] != VERSION:
        raise ValueError('Unsupported bundle configuration')
    raw = {source['id']: pinned_file(Path(inputs), source) for source in lock['files']}
    egif_source = lock['egif']
    pinned_file(Path(egif_zip).parent, {**egif_source, 'filename': Path(egif_zip).name})
    matches = [row for row in egif_rows(egif_zip, 2021)
               if row['province_code'] == '29' and row['detection'] == '2021-09-08T21:35:00']
    if len(matches) != 1:
        raise ValueError('Expected one province/detection EGIF match')
    incident = matches[0]
    if incident['controlled'] != '2021-09-14T06:45:00' or incident['extinguished'] != '2021-10-24T19:00:00':
        raise ValueError('EGIF milestone match changed')
    prepared = {}
    summaries = []
    for product in lock['products']:
        if (not product['id'] or any(character not in 'abcdefghijklmnopqrstuvwxyz0123456789-'
                                     for character in product['id'])):
            raise ValueError('Invalid product identifier')
        sources, layers = product_layers(raw[product['source_id']], product)
        summary = dict(id=product['id'], delivery_source_timestamp=product['delivery_source_timestamp'],
                       delivery_timezone_verified=False, available_at_utc=None, sources=sources, layers={})
        for layer, (value, metadata) in layers.items():
            filename = f'private/products/{product["id"]}/{layer}.geojson'
            data = encoded(value)
            prepared[filename] = data
            summary['layers'][layer] = {**metadata, 'path': filename,
                                      'sha256': hashlib.sha256(data).hexdigest()}
        summaries.append(summary)
    population_metadata = load_json((Path(config) / 'population_sources.json').read_bytes())
    population = population_rows(raw['ine-population'], population_metadata)
    terrain = terrain_summary(raw['terrain-aoi'])
    prepared['private/population.json'] = encoded(population)
    prepared['private/terrain_aoi.asc'] = raw['terrain-aoi']
    prepared['private/terrain_summary.json'] = encoded(terrain)
    clocks = {field: local_clock(incident[field]) for field in (
        'detection', 'ground_arrival', 'air_arrival', 'controlled', 'extinguished') if incident[field]}
    known_sources = {source['id'] for source in lock['files']} | set(lock['uncached_sources']) | {'egif'}
    identifiers = set()
    for claim in claims['records']:
        if claim['id'] in identifiers or claim['source_id'] not in known_sources:
            raise ValueError('Duplicate claim or unresolved source')
        identifiers.add(claim['id'])
        if claim['actor_input_eligible'] is not False or claim['available_at_utc'] is not None:
            raise ValueError('Curated historical claims must remain evaluator-only')
        if claim.get('event_local'):
            claim['interpreted_clock'] = local_clock(claim['event_local'])
    bundle = dict(version=VERSION, incident_id='ES-MA-SIERRA-BERMEJA-2021',
                  event_year=2021, actor_input_eligible=False, historical_replay_ready=False,
                  builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  population_sidecar_sha256=hashlib.sha256(encoded(population_metadata)).hexdigest(),
                  clock_policy='Local incident clocks interpreted as Europe/Madrid, not source-verified timezone; CEMS source DBF Z clocks are explicit UTC. Web delivery clocks have no verified zone.',
                  source_lock_sha256=hashlib.sha256(encoded(lock)).hexdigest(),
                  claims_sha256=hashlib.sha256(encoded(claims)).hexdigest(),
                  source_files=lock['files'], uncached_sources=lock['uncached_sources'],
                  egif_match=dict(method='unique province and detection match, independently checked control/extinction',
                                  clocks=clocks, final_forest_area_ha=forest_area(incident),
                                  role='retrospective_evaluator_only'), products=summaries,
                  population=dict(path='private/population.json', municipalities=7,
                                  reference_date='2021-01-01', published_after_incident=True,
                                  exposure_or_evacuation_count=False),
                  terrain=terrain,
                  conflicts=claims['conflicts'], remaining_gates=lock['remaining_gates'])
    prepared['private/egif_incident.json'] = encoded(incident)
    prepared['private/claims.json'] = encoded(claims)
    prepared['manifest.json'] = encoded(bundle)
    digest = hashlib.sha256(encoded({name: hashlib.sha256(data).hexdigest()
                                    for name, data in sorted(prepared.items())})).hexdigest()
    output.mkdir(parents=True)
    for filename, data in prepared.items():
        path = output / filename
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('xb') as stream:
            stream.write(data)
        if filename.startswith('private/'):
            path.chmod(0o600)
    result = dict(version=VERSION, content_sha256=digest, files=len(prepared),
                  artifact_bytes=sum(map(len, prepared.values())), elapsed_seconds=round(time.monotonic() - started, 3),
                  products=len(summaries), claims=len(claims['records']),
                  actor_input_eligible=False, historical_replay_ready=False)
    (output / 'audit.json').write_bytes(encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--egif-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'data/malaga_2021')
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.inputs, args.egif_zip, args.output, args.config), indent=2))


if __name__ == '__main__':
    main()
