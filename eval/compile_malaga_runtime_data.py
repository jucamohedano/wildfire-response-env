"""Compile pinned local inputs without downloading or changing source snapshots.

Run with the isolated data Python and the core source on PYTHONPATH::

    PYTHONPATH=envs/wildfire_response/core/src \
      .runtime/malaga-data-venv/bin/python -m eval.compile_malaga_runtime_data \
      --output .runtime/malaga-prepared-inputs-v1/inputs.json

--root selects a repository containing the exact pinned artifacts. --output is a
fresh .json file, or a fresh directory in which inputs.json is created. Relative
outputs are rooted at --root. Outputs must stay in .runtime outside input folders.
No dependency installation, network access or terrain release is performed.
Rasterio is needed only when compiling; pyproj is optional (rasterio's offline
PROJ transform is the fallback). The generated JSON needs neither at runtime.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo


VERSION = 'wildfire-malaga-prepared-inputs-v1'
DEFAULT_OUTPUT = '.runtime/malaga-prepared-inputs-v1/inputs.json'
LANDSCAPE = 'data/malaga_2021/landscape_preparation_v1.json'
WEATHER = 'data/malaga_2021/weather_normalization_v1.json'
EPISODE = '.runtime/malaga-episode-v1/episode.json'
SNAPSHOT_PINS = {
    LANDSCAPE: 'aae8d75b6504ae468a1f53292138949f53991a1e29ba1ca25c64c6a9de109fc8',
    WEATHER: 'b01a26eb2e0eb39888ba92ff0ed6888b24d3fe8e83f98db1c92dec7326c9f124',
    '.runtime/malaga-landscape-prepared-v1/audit.json':
        'aae8d75b6504ae468a1f53292138949f53991a1e29ba1ca25c64c6a9de109fc8',
    '.runtime/malaga-weather-normalized-v1/audit.json':
        '9f96d95922a4267d7270b3f1c3c84ec42587e5543ec75558b90522239d860eab',
    EPISODE: '943f43848bc179095231c853ac540095dd31939deb66ab71c890941438f4ab9a',
}
GRID = dict(width=37, height=41, cell_km=0.5, crs='EPSG:25830',
            transform=[500, 0, 297000, 0, -500, 4054500])
RASTER_DIRECTORY = '.runtime/malaga-landscape-prepared-v1/'
RASTER_NAMES = ('fuel_classes', 'fuel_valid_mask', 'fuel_source_coverage_mask',
                'study_envelope_mask', 'terrain_valid_mask', 'terrain_suspect_mask')
WEATHER_FILE = '.runtime/malaga-weather-normalized-v1/weather.json'
MAX_JSON_BYTES = 1_000_000
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_FILES = 64
MAX_OUTPUT_BYTES = 1_000_000
ASSUMPTIONS = {
    'fuel_baseline': 'EFFIS categorical baseline; vegetation epoch and 2021 local fuel state unverified.',
    'unknown_fuel_physics': 'Synthetic scrub fallback in the runtime; retain unknown mask; unknown is not a barrier or measured scrub.',
    'fuel_physics': 'Class-to-engine mapping and spread/suppression remain synthetic and uncalibrated; moisture unknown.',
    'weather_usage': 'Station weather is context only; no weather-to-physics mapping is authorized.',
    'wind_direction': 'Source FROM/TO convention and sensor height unverified; never use for physical propagation.',
    'weather_delivery': 'Runtime delivery lag of 5 model minutes is assumed, not historical availability or receipt.',
    'weather_intervals': 'Source UTC labels retained; interval start/end semantics unknown; no interpolation or imputation.',
    'terrain': 'Terrain remains quarantined; slope disabled; no elevation, slope or aspect copied.',
    'grid': '500 m outward-aligned study envelope; row-major centres; not a historical burned perimeter.',
    'episode_clock': 'Detection local clock interpreted with Europe/Madrid; detection is not ignition.',
    'exercise': 'Simulation configuration with synthetic operations; historical replay is false.',
}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Nonfinite JSON constant')


def load_json(data):
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('JSON exceeds size cap')
    try:
        text = data.decode('utf-8')
        depth, quoted, escaped = 0, False, False
        for character in text:
            if quoted:
                if escaped:
                    escaped = False
                elif character == '\\':
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in '[{':
                depth += 1
                if depth > 64:
                    raise ValueError('JSON nesting exceeds depth cap')
            elif character in ']}':
                depth -= 1
        result = json.loads(text, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError('Invalid bounded JSON document') from error
    if type(result) is not dict:
        raise ValueError('Expected a JSON object')
    return result


def safe_path(root, relative):
    if (type(relative) is not str or not relative or len(relative) > 512
            or '\\' in relative or '\x00' in relative or '<' in relative or '>' in relative):
        raise ValueError('Invalid artifact path')
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts) or Path(relative).is_absolute():
        raise ValueError('Artifact path escapes root or is noncanonical')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Artifact/output path must not traverse a symlink')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Artifact path escapes root')
    return path


class PinnedFiles:
    def __init__(self, root):
        self.root = root
        self.records = {}
        self.total_bytes = 0

    def read(self, relative, expected_sha256, expected_bytes=None, *, retain=True):
        if type(expected_sha256) is not str or re.fullmatch('[0-9a-f]{64}', expected_sha256) is None:
            raise ValueError('Invalid SHA256 pin')
        if expected_bytes is not None and (type(expected_bytes) is not int
                                           or not 0 < expected_bytes <= MAX_FILE_BYTES):
            raise ValueError('Artifact byte count exceeds size cap')
        if len(self.records) >= MAX_FILES and relative not in self.records:
            raise ValueError('Artifact count exceeds cap')
        path = safe_path(self.root, relative)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        chunks = []
        hasher = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, 'rb') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_FILE_BYTES:
                raise ValueError('Expected a bounded regular artifact file')
            if expected_bytes is not None and metadata.st_size != expected_bytes:
                raise ValueError('Artifact byte count mismatch')
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError('Artifact exceeds size cap')
                hasher.update(chunk)
                if retain:
                    chunks.append(chunk)
        if size != metadata.st_size or hasher.hexdigest() != expected_sha256:
            raise ValueError(f'SHA256/size mismatch: {relative}')
        record = dict(path=relative, bytes=size, sha256=expected_sha256)
        if relative in self.records and self.records[relative] != record:
            raise ValueError('Conflicting artifact pins')
        if relative not in self.records:
            self.total_bytes += size
            if self.total_bytes > MAX_TOTAL_BYTES:
                raise ValueError('Total input size exceeds cap')
        self.records[relative] = record
        return b''.join(chunks), record

    def verify(self, record, *, retain=False):
        if type(record) is not dict or set(record) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Expected an exact file descriptor')
        return self.read(record['path'], record['sha256'], record['bytes'], retain=retain)


def _inventory(document, field):
    records = document.get(field)
    if type(records) is not list or not 0 < len(records) <= MAX_FILES:
        raise ValueError('Invalid audited inventory')
    indexed = {}
    for record in records:
        if type(record) is not dict or type(record.get('path')) is not str:
            raise ValueError('Invalid audited file descriptor')
        if record['path'] in indexed:
            raise ValueError('Duplicate audited artifact')
        indexed[record['path']] = record
    return indexed


def read_raster(data, name):
    try:
        import rasterio
        from rasterio.io import MemoryFile
    except ImportError as error:
        raise RuntimeError('Compilation requires optional rasterio; use .runtime/malaga-data-venv/bin/python') from error
    if data[:4] not in (b'II*\x00', b'MM\x00*'):
        raise ValueError('Expected a small GeoTIFF, not an external raster reference')
    with rasterio.Env(GDAL_PAM_ENABLED='NO', PROJ_NETWORK='OFF', GDAL_NUM_THREADS='1',
                      GDAL_CACHEMAX=8_000_000), MemoryFile(data) as memory:
        with memory.open() as source:
            expected_nodata = 0 if name == 'fuel_classes' else 255 if name == 'terrain_suspect_mask' else None
            if (source.driver != 'GTiff' or source.count != 1
                    or (source.height, source.width) != (GRID['height'], GRID['width'])
                    or source.crs is None or source.crs.to_string() != GRID['crs']
                    or list(source.transform)[:6] != GRID['transform']
                    or source.dtypes != ('uint8',) or source.scales != (1.0,)
                    or source.offsets != (0.0,) or source.nodata != expected_nodata
                    or any(height * width > 65_536 for height, width in source.block_shapes)):
                raise ValueError('Raster differs from the pinned common grid/layout')
            values = source.read(1).tolist()
    allowed = set(range(11)) if name == 'fuel_classes' else {0, 1, 255} if name == 'terrain_suspect_mask' else {0, 1}
    if any(type(value) is not int or value not in allowed for row in values for value in row):
        raise ValueError('Invalid categorical raster values')
    return values


def geographic_grid():
    eastings = [297000] + [297000 + (column + 0.5) * 500
                          for row in range(41) for column in range(37)]
    northings = [4054500] + [4054500 - (row + 0.5) * 500
                            for row in range(41) for column in range(37)]
    try:
        from pyproj import Transformer, network
    except ImportError:
        import rasterio
        from rasterio.warp import transform
        with rasterio.Env(PROJ_NETWORK='OFF'):
            longitudes, latitudes = transform(GRID['crs'], 'EPSG:4326', eastings, northings)
    else:
        network.set_network_enabled(False)
        transformer = Transformer.from_crs(GRID['crs'], 'EPSG:4326', always_xy=True,
                                           allow_ballpark=False)
        longitudes, latitudes = transformer.transform(eastings, northings, errcheck=True)
    points = []
    for longitude, latitude in zip(longitudes, latitudes):
        if not (math.isfinite(longitude) and math.isfinite(latitude)
                and -5.5 < longitude < -4.8 and 36.2 < latitude < 36.9):
            raise ValueError('Invalid transformed geographic point')
        points.append([round(float(longitude), 10), round(float(latitude), 10)])
    if len(points) != 1 + 37 * 41:
        raise ValueError('Incomplete geographic grid')
    return dict(GRID, transform=GRID['transform'][:], northwest_lon_lat=points[0],
                centre_lon_lat=[points[1 + row * 37:1 + (row + 1) * 37] for row in range(41)])


def weather_records(document):
    if (document.get('version') != 'malaga-weather-normalization-v1'
            or document.get('status') != 'source_clock_normalized'
            or document.get('station') != 'MA04 Estepona'
            or document.get('actor_input_eligible') is not False
            or document.get('historical_replay_ready') is not False):
        raise ValueError('Unexpected normalized weather contract')
    records = document.get('records')
    if type(records) is not list or len(records) != 384:
        raise ValueError('Expected 384 weather records')
    fields = dict(temperature_c=(-60, 70), relative_humidity_percent=(0, 100),
                  wind_speed_m_s=(0, 150), wind_direction_degrees=(0, 360),
                  precipitation_mm=(0, 1000))
    output = []
    start = datetime(2021, 9, 8, 0, 30, tzinfo=timezone.utc)
    for index, row in enumerate(records):
        expected = (start + timedelta(minutes=30 * index)).strftime('%Y-%m-%dT%H:%M:%SZ')
        if (type(row) is not dict or row.get('observed_at_utc') != expected
                or row.get('clock_ambiguous') is not False
                or row.get('received_at_utc') is not None or row.get('available_at_utc') is not None):
            raise ValueError('Invalid weather clock, ordering, gap or receipt status')
        clean = dict(observed_at_utc=expected)
        for field, (lower, upper) in fields.items():
            value = row.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f'Invalid weather measurement: {field}')
            clean[field] = value
        output.append(clean)
    return dict(station='MA04 Estepona', source_clock='UTC', records=output)


def output_path(root, output, records):
    output = Path(output)
    if output.is_absolute():
        try:
            relative = output.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError('Output must stay within repository .runtime') from error
    else:
        relative = output.as_posix()
    destination = safe_path(root, relative)
    if not relative.startswith('.runtime/'):
        raise ValueError('Output must stay within repository .runtime')
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing output file or directory')
    for record in records:
        if record['path'].startswith('.runtime/') and destination.is_relative_to((root / record['path']).parent):
            raise ValueError('Output must stay outside input artifact directories')
    return destination if destination.suffix == '.json' else destination / 'inputs.json'


def compile_inputs(root, output=DEFAULT_OUTPUT):
    from envs.wildfire_response.regional.malaga.pack import validate_pack
    from envs.wildfire_response.regional.ops.generator import digest

    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError('Repository root must be a canonical non-symlink directory')
    files = PinnedFiles(root)
    snapshots = {}
    snapshot_records = []
    for relative, pin in SNAPSHOT_PINS.items():
        data, record = files.read(relative, pin)
        snapshots[relative] = load_json(data)
        snapshot_records.append(record)
    landscape, weather = snapshots[LANDSCAPE], snapshots[WEATHER]
    for document, local_audit in ((landscape, '.runtime/malaga-landscape-prepared-v1/audit.json'),
                                   (weather, '.runtime/malaga-weather-normalized-v1/audit.json')):
        for field in ('version', 'status', 'checks', 'input_files', 'output_files'):
            if document.get(field) != snapshots[local_audit].get(field):
                raise ValueError('Local audit differs from pinned snapshot')
    if (landscape.get('version') != 'wildfire-malaga-landscape-preparation-v1'
            or landscape.get('status') != 'fuel_prepared_terrain_quarantined'
            or landscape.get('terrain', {}).get('quarantine_released') is not False
            or any(landscape.get('grid', {}).get(key) != value for key, value in GRID.items()
                   if key != 'cell_km')
            or landscape.get('grid', {}).get('resolution_metres') != 500):
        raise ValueError('Unexpected landscape contract or grid')
    input_records, output_records, retained = {}, {}, {}
    wanted = {WEATHER_FILE, *(RASTER_DIRECTORY + name + '.tif' for name in RASTER_NAMES)}
    for document in (landscape, weather):
        for field, collection in (('input_files', input_records), ('output_files', output_records)):
            for relative, record in _inventory(document, field).items():
                data, verified = files.verify(record, retain=relative in wanted)
                collection[relative] = verified
                if relative in wanted:
                    retained[relative] = data
    landscape_outputs = set(_inventory(landscape, 'output_files'))
    if landscape_outputs != {RASTER_DIRECTORY + name + '.tif' for name in RASTER_NAMES}:
        raise ValueError('Unexpected landscape output inventory')
    if not wanted.issubset(retained):
        raise ValueError('Missing prepared runtime inputs')
    rasters = {name: read_raster(retained[RASTER_DIRECTORY + name + '.tif'], name)
               for name in RASTER_NAMES}
    for row in range(41):
        for column in range(37):
            fuel = rasters['fuel_classes'][row][column]
            valid = rasters['fuel_valid_mask'][row][column]
            if (valid != int(fuel != 0)
                    or valid > rasters['fuel_source_coverage_mask'][row][column]
                    or rasters['terrain_valid_mask'][row][column] != 0):
                raise ValueError('Inconsistent fuel validity/coverage or terrain quarantine mask')
    if not any(value for row in rasters['study_envelope_mask'] for value in row):
        raise ValueError('Empty study mask')
    episode = validate_pack(snapshots[EPISODE])
    moment = datetime.fromisoformat(episode['detection_local']).replace(tzinfo=ZoneInfo('Europe/Madrid'))
    episode_start = moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if episode_start != '2021-09-08T19:35:00Z':
        raise ValueError('Unexpected UTC detection anchor')
    payload = dict(version=VERSION, role='simulation_configuration', grid=geographic_grid(),
                   fuel_classes=rasters['fuel_classes'], fuel_valid_mask=rasters['fuel_valid_mask'],
                   study_mask=rasters['study_envelope_mask'],
                   weather=weather_records(load_json(retained[WEATHER_FILE])),
                   episode_start_utc=episode_start, episode_pack=episode,
                   provenance=dict(snapshot_files=sorted(snapshot_records, key=lambda item: item['path']),
                                   input_files=[input_records[key] for key in sorted(input_records)],
                                   output_files=[output_records[key] for key in sorted(output_records)],
                                   episode_pack=files.records[EPISODE]),
                   slope_enabled=False, historical_replay=False, assumptions=dict(ASSUMPTIONS))
    payload['sha256'] = digest(payload)
    encoded = (json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError('Runtime JSON exceeds output cap')
    destination = output_path(root, output, files.records.values())
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_path(root, destination.relative_to(root).as_posix())
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(encoded)
    return dict(path=destination.relative_to(root).as_posix(), bytes=len(encoded),
                sha256=payload['sha256'], file_sha256=hashlib.sha256(encoded).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, default=Path(DEFAULT_OUTPUT),
                        help='Fresh .json file or fresh directory (creates inputs.json); relative to --root')
    args = parser.parse_args()
    try:
        result = compile_inputs(args.root, args.output)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, OverflowError, RecursionError) as error:
        parser.exit(2, f'Compilation failed: {error}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
