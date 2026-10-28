"""Prepare and validate incident-day grids without inventing missing fuel."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = 'incident-day-inputs-v2'
MAX_BYTES = 4_000_000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _check_hash(document, name):
    if (type(document) is not dict or not re.fullmatch(r'[0-9a-f]{64}', str(document.get('sha256', '')))
            or digest({key: value for key, value in document.items() if key != 'sha256'}) != document['sha256']):
        raise ValueError(f'{name} hash mismatch')
    return document['sha256']


def _shape(grid):
    if (type(grid) is not dict or type(grid.get('height')) is not int
            or type(grid.get('width')) is not int or not 1 <= grid['height'] <= 1000
            or not 1 <= grid['width'] <= 1000 or grid['height'] * grid['width'] > 200000):
        raise ValueError('Invalid grid dimensions')
    return grid['height'], grid['width']


def _matrix(value, height, width, name):
    if (type(value) is not list or len(value) != height
            or any(type(row) is not list or len(row) != width for row in value)):
        raise ValueError(f'Invalid {name} dimensions')
    return value


def _mask(value, height, width, name):
    _matrix(value, height, width, name)
    if any(type(item) not in (int, bool) or item not in (0, 1) for row in value for item in row):
        raise ValueError(f'Invalid {name} values')
    return value


def _crop(value, crop):
    top, left = crop['top'], crop['left']
    return [row[left:left + crop['width']] for row in value[top:top + crop['height']]]


def _resolved_fuels(source, known, grid, resolution, pins):
    if resolution is None:
        return [[fuel if valid else None for fuel, valid in zip(source_row, known_row)]
                for source_row, known_row in zip(source, known)]
    if type(resolution) is not dict:
        raise ValueError('Invalid fuel resolution')
    if resolution.get('kind') == 'estimated' and set(resolution) == {'kind', 'document'}:
        from .fuel_estimation import validate_estimates
        document = validate_estimates(resolution['document'], source, known, grid)
        if (document['source_pins']['prepared_sha256'] != pins['prepared_sha256']
                or document['source_pins']['domain_sha256'] != pins['domain_sha256']):
            raise ValueError('Fuel estimates refer to different source inputs')
        return copy.deepcopy(document['fuel_classes'])
    if (set(resolution) != {'kind', 'class'} or resolution.get('kind') != 'synthetic'
            or type(resolution.get('class')) is not int or resolution['class'] not in range(1, 11)):
        raise ValueError('Only explicit synthetic or source-pinned estimated fuel is supported')
    return [[fuel if valid else resolution['class'] for fuel, valid in zip(source_row, known_row)]
            for source_row, known_row in zip(source, known)]


def prepare_inputs(prepared, domain, network_document, *, fuel_resolution=None):
    """Build a hash-pinned pack; source uncertainty is never counted as known.

    fuel_resolution may be {'kind': 'synthetic', 'class': 1..10}; it supplies
    a prototype value only, and never approves a source pack.
    """
    prepared, domain = copy.deepcopy(prepared), copy.deepcopy(domain)
    prepared_hash = _check_hash(prepared, 'prepared input')
    domain_hash = _check_hash(domain, 'topographic input')
    network_hash = _check_hash(network_document, 'network')
    if domain.get('prepared_inputs_sha256') != prepared_hash:
        raise ValueError('Topographic input does not pin this prepared input')
    parent_height, parent_width = _shape(prepared.get('grid'))
    height, width = _shape(domain.get('grid'))
    crop = domain.get('crop')
    if (type(crop) is not dict or set(crop) != {'left', 'top', 'width', 'height'}
            or any(type(crop[key]) is not int for key in crop)
            or crop['left'] < 0 or crop['top'] < 0 or crop['width'] != width or crop['height'] != height
            or crop['left'] + width > parent_width or crop['top'] + height > parent_height):
        raise ValueError('Invalid topographic crop')
    parent_grid, grid = prepared['grid'], domain['grid']
    transform = parent_grid.get('transform')
    if (type(transform) is not list or len(transform) != 6
            or any(type(item) not in (int, float) or not math.isfinite(item) for item in transform)):
        raise ValueError('Invalid parent grid transform')
    expected_transform = transform[:]
    expected_transform[2] += crop['left'] * transform[0] + crop['top'] * transform[1]
    expected_transform[5] += crop['left'] * transform[3] + crop['top'] * transform[4]
    if (grid.get('transform') != expected_transform or grid.get('crs') != parent_grid.get('crs')
            or grid.get('cell_km') != parent_grid.get('cell_km')):
        raise ValueError('Crop grid is not aligned with prepared input')
    centres = parent_grid.get('centre_lon_lat')
    if centres is not None:
        _matrix(centres, parent_height, parent_width, 'parent grid centres')
        if grid.get('centre_lon_lat') != _crop(centres, crop):
            raise ValueError('Crop grid centres differ from prepared input')
    for name in ('fuel_classes', 'fuel_valid_mask', 'study_mask'):
        _matrix(prepared.get(name), parent_height, parent_width, name)
    _mask(prepared['fuel_valid_mask'], parent_height, parent_width, 'fuel validity')
    _mask(prepared['study_mask'], parent_height, parent_width, 'study mask')
    source_classes = _crop(prepared['fuel_classes'], crop)
    source_known = _crop(prepared['fuel_valid_mask'], crop)
    study = _crop(prepared['study_mask'], crop)
    support = _mask(domain.get('height_support_mask'), height, width, 'height support')
    heights = _matrix(domain.get('elevations_metres'), height, width, 'heights')
    if any(not item for row in study for item in row) or any(not item for row in support for item in row):
        raise ValueError('Crop must have complete study and height coverage; masking is not repair')
    for row in range(height):
        for column in range(width):
            source = source_classes[row][column]
            if type(source) is not int or source not in range(11) or bool(source) != bool(source_known[row][column]):
                raise ValueError('Source fuel and validity mask disagree')
    unknown = sum(not value for row in source_known for value in row)
    classes = _resolved_fuels(source_classes, source_known, grid, fuel_resolution,
                             dict(prepared_sha256=prepared_hash, domain_sha256=domain_hash))
    synthetic_fixture = prepared.get('role') == 'synthetic_fixture' and domain.get('role') == 'synthetic_fixture'
    status = 'approved_synthetic_fixture' if synthetic_fixture and not unknown else 'unapproved_source_pack'
    payload = dict(version=VERSION, grid=grid, heights=heights, fuel_classes=classes,
                   study_mask=copy.deepcopy(study), walk_mask=copy.deepcopy(study),
                   fuel_known_mask=copy.deepcopy(source_known),
                   network_sha256=network_hash,
                   source_pins=dict(prepared_sha256=prepared_hash, domain_sha256=domain_hash,
                                    network_sha256=network_hash),
                   approval_status=status,
                   provenance=dict(prepared_role=prepared.get('role'), domain_role=domain.get('role'),
                                   prepared=prepared.get('provenance'), domain=domain.get('provenance'),
                                   crop=crop, source_fuel_classes=source_classes,
                                   source_fuel_known_mask=copy.deepcopy(source_known),
                                   source_study_mask=copy.deepcopy(study),
                                   fuel_resolution=fuel_resolution, unknown_fuel_cells=unknown))
    payload['sha256'] = digest(payload)
    return load_inputs(payload, payload['sha256'], allow_unapproved=True)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def load_inputs(value, expected_sha256, *, allow_unapproved=False):
    """Validate a caller-pinned pack. Unapproved packs require explicit opt-in."""
    if type(value) is dict:
        value = copy.deepcopy(value)
    else:
        path = Path(value)
        if path.is_symlink():
            raise ValueError('Input pack must not be a symlink')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Input pack exceeds byte limit')
        value = json.loads(raw, object_pairs_hook=_unique)
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Input pack exceeds byte limit')
    actual = _check_hash(value, 'incident-day input')
    if type(expected_sha256) is not str or actual != expected_sha256:
        raise ValueError('Input differs from caller-pinned hash')
    required = {'version', 'grid', 'heights', 'fuel_classes', 'study_mask', 'walk_mask',
                'fuel_known_mask', 'network_sha256', 'source_pins', 'approval_status', 'provenance', 'sha256'}
    if set(value) != required or value['version'] != VERSION:
        raise ValueError('Invalid incident-day input contract')
    height, width = _shape(value['grid'])
    for name in ('study_mask', 'walk_mask', 'fuel_known_mask'):
        _mask(value[name], height, width, name)
    for name in ('heights', 'fuel_classes'):
        _matrix(value[name], height, width, name)
    pins = value['source_pins']
    if (type(pins) is not dict or set(pins) != {'prepared_sha256', 'domain_sha256', 'network_sha256'}
            or any(type(pin) is not str or not re.fullmatch(r'[0-9a-f]{64}', pin) for pin in pins.values())
            or pins['network_sha256'] != value['network_sha256']):
        raise ValueError('Invalid source pins')
    provenance = value['provenance']
    if type(provenance) is not dict:
        raise ValueError('Missing source provenance')
    source = _matrix(provenance.get('source_fuel_classes'), height, width, 'source fuel')
    known = _mask(provenance.get('source_fuel_known_mask'), height, width, 'source fuel validity')
    study = _mask(provenance.get('source_study_mask'), height, width, 'source study')
    if (value['fuel_known_mask'] != known or value['study_mask'] != study
            or value['walk_mask'] != study or any(not cell for row in study for cell in row)):
        raise ValueError('Masks cannot conceal source uncertainty or restrict traversal')
    resolution = provenance.get('fuel_resolution')
    resolved = _resolved_fuels(source, known, value['grid'], resolution, pins)
    unknown = 0
    for row in range(height):
        for column in range(width):
            elevation = value['heights'][row][column]
            if type(elevation) not in (int, float) or not math.isfinite(elevation):
                raise ValueError('Complete finite heights are required')
            original = source[row][column]
            is_known = known[row][column]
            if type(original) is not int or original not in range(11) or bool(original) != bool(is_known):
                raise ValueError('Source fuel and validity mask disagree')
            expected = resolved[row][column]
            if value['fuel_classes'][row][column] != expected or type(value['fuel_classes'][row][column]) is bool:
                raise ValueError('Fuel differs from its source or declared assumption')
            unknown += not is_known
    if type(provenance.get('unknown_fuel_cells')) is not int or provenance['unknown_fuel_cells'] != unknown:
        raise ValueError('Unknown fuel count differs from source mask')
    fixture = provenance.get('prepared_role') == 'synthetic_fixture' and provenance.get('domain_role') == 'synthetic_fixture'
    status = 'approved_synthetic_fixture' if fixture and not unknown else 'unapproved_source_pack'
    if value['approval_status'] != status:
        raise ValueError('Invalid approval status')
    if status != 'approved_synthetic_fixture' and not allow_unapproved:
        raise ValueError('Source pack is unapproved; unknown or unverified source fuel requires review')
    return value
