"""Caller-pinned continuous height domains: reject missing terrain, never flatten it."""
import copy
import json
import math
from pathlib import Path

from ..ops.generator import digest


VERSION = 'wildfire-topographic-inputs-v1'
POLICY = dict(height_units='metre_documented_MDT05_ASCII', missing_height='reject_domain_no_fill',
              boundary='censor_on_fire_contact_not_firebreak')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate topographic field')
        result[key] = value
    return result


def load_domain(source, prepared, expected_sha256):
    if isinstance(source, dict):
        value = copy.deepcopy(source)
        raw = json.dumps(value, allow_nan=False).encode()
    else:
        path = Path(source)
        if path.is_symlink():
            raise ValueError('Topographic bundle cannot be a symlink')
        with path.open('rb') as stream:
            raw = stream.read(2_000_001)
        value = json.loads(raw, object_pairs_hook=_unique)
    if len(raw) > 2_000_000:
        raise ValueError('Topographic bundle exceeds size cap')
    required = {'version', 'role', 'prepared_inputs_sha256', 'crop', 'grid', 'elevations_metres',
                'height_support_mask', 'policy', 'provenance', 'sha256'}
    if (type(value) is not dict or set(value) != required or value['version'] != VERSION
            or value['role'] != 'simulation_configuration' or value['policy'] != POLICY
            or value['prepared_inputs_sha256'] != prepared['sha256']
            or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or value['sha256'] != expected_sha256
            or digest({key: item for key, item in value.items() if key != 'sha256'}) != expected_sha256):
        raise ValueError('Topographic contract or caller pin mismatch')
    crop, parent = value['crop'], prepared['grid']
    if type(crop) is not dict or set(crop) != {'left', 'top', 'width', 'height'} or any(type(item) is not int for item in crop.values()):
        raise ValueError('Expected integer crop frame')
    left, top, width, height = (crop[key] for key in ('left', 'top', 'width', 'height'))
    if not (0 <= left <= parent['width'] - width and 0 <= top <= parent['height'] - height
            and 20 <= width <= parent['width'] and 16 <= height <= parent['height']):
        raise ValueError('Unsupported complete-domain dimensions')
    grid = value['grid']
    expected_transform = parent['transform'][:]
    expected_transform[2] += left * 500
    expected_transform[5] -= top * 500
    centres = [row[left:left + width] for row in parent['centre_lon_lat'][top:top + height]]
    if (type(grid) is not dict or set(grid) != set(parent)
            or grid['width'] != width or grid['height'] != height or grid['crs'] != 'EPSG:25830'
            or grid['cell_km'] != .5 or grid['transform'] != expected_transform
            or grid['centre_lon_lat'] != centres):
        raise ValueError('Topographic grid differs from parent crop')
    corner = grid['northwest_lon_lat']
    if (not isinstance(corner, list) or len(corner) != 2 or any(type(item) not in (int, float) or not math.isfinite(item) for item in corner)
            or not (-5.5 < corner[0] < -4.8 and 36.2 < corner[1] < 36.9)):
        raise ValueError('Invalid cropped geographic corner')
    for field in ('elevations_metres', 'height_support_mask'):
        rows = value[field]
        if (not isinstance(rows, list) or len(rows) != height
                or any(not isinstance(row, list) or len(row) != width for row in rows)):
            raise ValueError('Incomplete height surface')
    for row in range(height):
        for column in range(width):
            elevation = value['elevations_metres'][row][column]
            support = value['height_support_mask'][row][column]
            if (type(elevation) not in (int, float) or not math.isfinite(elevation) or not 0 < elevation <= 4000
                    or type(support) is not int or support != 1
                    or prepared['study_mask'][top + row][left + column] != 1):
                raise ValueError('Missing or unsupported interior terrain; expand data or choose another domain')
    return value


def crop_inputs(prepared, domain):
    value = copy.deepcopy(prepared)
    crop = domain['crop']
    left, top, width, height = (crop[key] for key in ('left', 'top', 'width', 'height'))
    value['grid'] = copy.deepcopy(domain['grid'])
    for field in ('fuel_classes', 'fuel_valid_mask', 'study_mask'):
        value[field] = [row[left:left + width] for row in value[field][top:top + height]]
    value['sha256'] = digest(dict(prepared=prepared['sha256'], domain=domain['sha256']))
    return value
