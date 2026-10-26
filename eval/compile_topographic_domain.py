"""Compile a complete rectangular height domain from pinned, local MDT05 inputs.

    PYTHONPATH=envs/wildfire_response/core/src \
      .runtime/malaga-data-venv/bin/python -m eval.compile_topographic_domain

No downloads are needed. Offline pyproj is used only to compile the cropped
northwest corner; runtime JSON requires only the standard library. The ASCII
source is a 25 m sample of MDT05, with metre units documented by the pinned 2017 producer
PDF. Positive finite samples and their full one-sample Horn halo are required;
this exclusion policy makes no claim about native void semantics, source survey
edition, historical availability, or physical calibration. Fuel and its labelled
synthetic fallback remain in the separately supplied prepared parent bundle.

The crop includes its boundary guard cells. Boundary fire contact must censor
the simulation, rather than treating the boundary as a firebreak. Rectangle ties
prefer the smallest top, then left, then the greatest width, then height.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from eval.compile_malaga_runtime_data import PinnedFiles, load_json, output_path, safe_path


VERSION = 'wildfire-topographic-inputs-v1'
DEFAULT_OUTPUT = '.runtime/topographic-inputs-v1/inputs.json'
PREPARED_INPUTS = '.runtime/malaga-prepared-inputs-v1/inputs.json'
PREPARED_INPUTS_SHA256 = 'ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108'
TERRAIN_DIRECTORY = '.runtime/malaga-terrain-validation-v2/'
TERRAIN_ASCII = TERRAIN_DIRECTORY + 'mdt05-sampled25m-halo.asc'
INPUT_PINS = {
    PREPARED_INPUTS: '9e1998653eaf154d29350db5f080f70812510b8abd7751517a71868cf2bdb998',
    TERRAIN_ASCII: '1b2e4f470bf456b0189cf22e60dddcf09af413617eb9b76f5a2c1650be70e917',
    TERRAIN_DIRECTORY + 'ign-wcs-2017-slides.pdf':
        'af354454dc544a12bd90d60219dde2181bbc1f5689af0c9fbbe6e0fae72f4b61',
    TERRAIN_DIRECTORY + 'mdt05-description.xml':
        'a92a29c071dcdb991fea98a89dd1105d1fd85bc81702b01f787ebffb4b40de04',
}
MAX_ASCII_BYTES = 8_000_000
MAX_OUTPUT_BYTES = 1_000_000
MAX_SOURCE_SIDE = 1024
MAX_MASK_SIDE = 128
MAX_MASK_CELLS = 4096
POLICY = {
    'height_units': 'metre_documented_MDT05_ASCII',
    'missing_height': 'reject_domain_no_fill',
    'boundary': 'censor_on_fire_contact_not_firebreak',
}


def _matrix_shape(values, max_side, max_cells):
    if (type(values) is not list or not 0 < len(values) <= max_side
            or type(values[0]) is not list or not 0 < len(values[0]) <= max_side
            or len(values) * len(values[0]) > max_cells
            or any(type(row) is not list or len(row) != len(values[0]) for row in values)):
        raise ValueError('Expected a bounded nonempty rectangular matrix')
    return len(values), len(values[0])


def _mask_shape(mask):
    shape = _matrix_shape(mask, MAX_MASK_SIDE, MAX_MASK_CELLS)
    if any(type(value) not in (bool, int) or value not in (0, 1)
           for row in mask for value in row):
        raise ValueError('Support/study masks must contain only booleans or binary integers')
    return shape


def largest_supported_rectangle(mask, min_width=20, min_height=16):
    """Return the largest all-true crop, including fully supported guard cells."""
    height, width = _mask_shape(mask)
    if (type(min_width) is not int or type(min_height) is not int
            or not 1 <= min_width <= MAX_MASK_SIDE or not 1 <= min_height <= MAX_MASK_SIDE):
        raise ValueError('Minimum rectangle dimensions must be positive bounded integers')
    best, best_rank = None, None
    for top in range(height - min_height + 1):
        columns = [True] * width
        for bottom in range(top, height):
            columns = [supported and bool(mask[bottom][column])
                       for column, supported in enumerate(columns)]
            rectangle_height = bottom - top + 1
            if rectangle_height < min_height:
                continue
            left = 0
            while left < width:
                if not columns[left]:
                    left += 1
                    continue
                right = left + 1
                while right < width and columns[right]:
                    right += 1
                rectangle_width = right - left
                if rectangle_width >= min_width:
                    rank = (-rectangle_width * rectangle_height, top, left,
                            -rectangle_width, -rectangle_height)
                    if best_rank is None or rank < best_rank:
                        best_rank = rank
                        best = dict(left=left, top=top, width=rectangle_width, height=rectangle_height)
                left = right
    if best is None:
        raise ValueError('No fully height-supported rectangle meets the minimum dimensions')
    return best


def coarsen_heights(samples, study_mask, *, block_size=20, offset=20):
    """Average complete blocks only; unsupported cells remain None, never filled."""
    source_height, source_width = _matrix_shape(samples, MAX_SOURCE_SIDE, MAX_SOURCE_SIDE ** 2)
    height, width = _mask_shape(study_mask)
    if (type(block_size) is not int or not 1 <= block_size <= 32
            or type(offset) is not int or not 1 <= offset <= MAX_SOURCE_SIDE
            or offset + height * block_size >= source_height
            or offset + width * block_size >= source_width):
        raise ValueError('Source must contain every full block and its one-sample halo')
    positive = []
    for source_row in samples:
        valid_row = []
        for value in source_row:
            if type(value) not in (int, float):
                raise ValueError('Heights must be numeric, not strings, booleans or nulls')
            try:
                valid_row.append(math.isfinite(value) and value > 0)
            except OverflowError as error:
                raise ValueError('Height exceeds numeric bounds') from error
        positive.append(valid_row)
    elevations = [[None] * width for row in range(height)]
    support = [[0] * width for row in range(height)]
    for row in range(height):
        first_row = offset + row * block_size
        for column in range(width):
            first_column = offset + column * block_size
            if not study_mask[row][column] or not all(
                    all(positive[source_row][first_column - 1:first_column + block_size + 1])
                    for source_row in range(first_row - 1, first_row + block_size + 1)):
                continue
            mean = math.fsum(float(value) / (block_size ** 2)
                             for source_row in samples[first_row:first_row + block_size]
                             for value in source_row[first_column:first_column + block_size])
            if not math.isfinite(mean) or mean <= 0:
                raise ValueError('Invalid block mean; height filling is forbidden')
            elevations[row][column] = mean
            support[row][column] = 1
    return elevations, support


def read_ascii(data):
    if type(data) is not bytes or not 0 < len(data) <= MAX_ASCII_BYTES:
        raise ValueError('ASCII source exceeds byte cap or has invalid encoding')
    try:
        lines = data.decode('ascii').splitlines()
    except UnicodeError as error:
        raise ValueError('Expected ASCII height encoding') from error
    if len(lines) != 865:
        raise ValueError('Expected five ASCII headers and exactly 860 source rows')
    expected = (('ncols', 780), ('nrows', 860), ('xllcorner', 296500),
                ('yllcorner', 4033500), ('cellsize', 25))
    for line, (name, value) in zip(lines[:5], expected):
        parts = line.split()
        if len(parts) != 2 or parts[0].lower() != name or len(parts[1]) > 64:
            raise ValueError('Unexpected ASCII source header or missing-value declaration')
        try:
            actual = float(parts[1])
        except ValueError as error:
            raise ValueError('Invalid ASCII header number') from error
        if not math.isfinite(actual) or not math.isclose(actual, value, rel_tol=0, abs_tol=1e-7):
            raise ValueError('ASCII source differs from the pinned 25 m grid/bounds')
    samples = []
    for line in lines[5:]:
        tokens = line.split()
        if len(tokens) != 780 or any(len(token) > 64 for token in tokens):
            raise ValueError('Expected exactly 780 bounded heights in every source row')
        try:
            samples.append([float(token) for token in tokens])
        except ValueError as error:
            raise ValueError('Malformed ASCII height') from error
    return samples


def cropped_grid(parent_grid, crop):
    try:
        from pyproj import Transformer, network
    except ImportError as error:
        raise RuntimeError('Compilation requires offline pyproj; use .runtime/malaga-data-venv/bin/python') from error
    left, top = crop['left'], crop['top']
    width, height = crop['width'], crop['height']
    transform = parent_grid['transform'][:]
    transform[2] += left * transform[0] + top * transform[1]
    transform[5] += left * transform[3] + top * transform[4]
    network.set_network_enabled(False)
    transformer = Transformer.from_crs(parent_grid['crs'], 'EPSG:4326', always_xy=True,
                                       allow_ballpark=False)
    longitude, latitude = transformer.transform(transform[2], transform[5], errcheck=True)
    if not (math.isfinite(longitude) and math.isfinite(latitude)
            and -5.5 < longitude < -4.8 and 36.2 < latitude < 36.9):
        raise ValueError('Invalid transformed northwest corner')
    return dict(width=width, height=height, cell_km=parent_grid['cell_km'], crs=parent_grid['crs'],
                transform=transform, northwest_lon_lat=[round(longitude, 10), round(latitude, 10)],
                centre_lon_lat=[row[left:left + width]
                                for row in parent_grid['centre_lon_lat'][top:top + height]])


def compile_inputs(root, output=DEFAULT_OUTPUT):
    from envs.wildfire_response.regional.ops.generator import digest
    from envs.wildfire_response.regional.prepared.data import validate_inputs

    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError('Repository root must be a canonical non-symlink directory')
    files = PinnedFiles(root)
    retained = {}
    for relative, pin in INPUT_PINS.items():
        data, record = files.read(relative, pin, retain=relative in (PREPARED_INPUTS, TERRAIN_ASCII))
        if relative in (PREPARED_INPUTS, TERRAIN_ASCII):
            retained[relative] = data
    parent = validate_inputs(load_json(retained[PREPARED_INPUTS]), PREPARED_INPUTS_SHA256)
    elevations, support = coarsen_heights(read_ascii(retained[TERRAIN_ASCII]), parent['study_mask'])
    crop = largest_supported_rectangle(support)
    left, top = crop['left'], crop['top']
    right, bottom = left + crop['width'], top + crop['height']
    payload = dict(version=VERSION, role='simulation_configuration',
                   prepared_inputs_sha256=PREPARED_INPUTS_SHA256, crop=crop,
                   grid=cropped_grid(parent['grid'], crop),
                   elevations_metres=[row[left:right] for row in elevations[top:bottom]],
                   height_support_mask=[row[left:right] for row in support[top:bottom]],
                   policy=dict(POLICY),
                   provenance=[files.records[relative] for relative in sorted(files.records)])
    payload['sha256'] = digest(payload)
    encoded = (json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError('Topographic JSON exceeds output cap')
    destination = output_path(root, output, files.records.values())
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_path(root, destination.relative_to(root).as_posix())
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(encoded)
    return dict(path=destination.relative_to(root).as_posix(), bytes=len(encoded), crop=crop,
                sha256=payload['sha256'], file_sha256=hashlib.sha256(encoded).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, default=Path(DEFAULT_OUTPUT),
                        help='Fresh .json file or fresh directory inside repository .runtime')
    options = parser.parse_args()
    try:
        result = compile_inputs(options.root, options.output)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, OverflowError, RecursionError) as error:
        parser.exit(2, f'Compilation failed: {error}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
