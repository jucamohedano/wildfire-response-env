"""Prepare private, *unqualified* candidates for an expanded source-map grid.

This produces draft exercises, not an environment catalog or benchmark. It
does not validate the new terrain, routes, warm-up, or mission opportunities.
Future schedules and hidden initial conditions must never be given to an actor.
"""
import copy
import hashlib
import json
import math

VERSION = 'wildfire-extended-scenario-drafts-v1'
SOURCE_VERSION = 'wildfire-incident-eval-catalog-v1'
SCENARIO_VERSION = 'wildfire-extended-incident-candidate-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def wind_from_degrees(degrees, magnitude):
    """Meteorological *from* direction -> model vector (east, south).

    Magnitude is in relative model units, not a measured wind speed.
    """
    if (isinstance(degrees, bool) or isinstance(magnitude, bool)
            or not isinstance(degrees, (int, float)) or not isinstance(magnitude, (int, float))
            or not math.isfinite(degrees) or not math.isfinite(magnitude)
            or magnitude < 0 or magnitude > 3):
        raise ValueError('Invalid wind direction or model magnitude')
    angle = math.radians(degrees % 360)
    return [round(-math.sin(angle) * magnitude, 9), round(math.cos(angle) * magnitude, 9)]


def _validate_grid(grid):
    if grid.get('crs') != 'EPSG:25830':
        raise ValueError('Expected EPSG:25830')
    transform = grid.get('transform')
    if (not isinstance(transform, list) or len(transform) != 6 or
            transform[0] != 500 or transform[1] != 0 or transform[3] != 0 or transform[4] != -500 or
            any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in transform)):
        raise ValueError('Expected a north-up 500 m affine grid')
    if any(type(grid.get(key)) is not int or grid[key] <= 0 for key in ('width', 'height')):
        raise ValueError('Invalid grid dimensions')


def _remap(point, old, new):
    if (not isinstance(point, list) or len(point) != 2 or
            any(type(v) is not int for v in point) or
            not 0 <= point[0] < old['width'] or not 0 <= point[1] < old['height']):
        raise ValueError('Invalid source cell')
    ox, oy = old['transform'][2], old['transform'][5]
    nx, ny = new['transform'][2], new['transform'][5]
    x = (ox + (point[0] + .5) * 500 - nx) / 500 - .5
    y = (ny - (oy - (point[1] + .5) * 500)) / 500 - .5
    if not math.isclose(x, round(x), abs_tol=1e-9) or not math.isclose(y, round(y), abs_tol=1e-9):
        raise ValueError('Grids must be aligned at cell boundaries')
    result = [int(round(x)), int(round(y))]
    if not 0 <= result[0] < new['width'] or not 0 <= result[1] < new['height']:
        raise ValueError('New grid does not cover an original scenario cell')
    return result


def build_drafts(catalog, old_grid, new_grid):
    """Derive 12 private drafts; no generated result is qualified to run."""
    _validate_grid(old_grid)
    _validate_grid(new_grid)
    if (catalog.get('version') != SOURCE_VERSION or
            catalog.get('sha256') != _digest({k: v for k, v in catalog.items() if k != 'sha256'}) or
            not isinstance(catalog.get('cases'), list) or len(catalog['cases']) != 12):
        raise ValueError('Invalid or unpinned source catalog')
    for point in ([0, 0], [old_grid['width'] - 1, old_grid['height'] - 1]):
        _remap(point, old_grid, new_grid)
    # Ten from the sea, two deliberate alternative initial directions. This is
    # exercise selection, not a measured distribution of local winds.
    directions = (180, 150, 210, 165, 195, 180, 150, 210, 165, 195, 270, 0)
    cases = []
    for index, (original, direction) in enumerate(zip(catalog['cases'], directions)):
        if (original.get('id') != f'incident-eval-v1-{index:03}' or
                original.get('sha256') != _digest({k: v for k, v in original.items() if k != 'sha256'})):
            raise ValueError('Unexpected source scenario identity or hash')
        case = copy.deepcopy(original)
        case['id'] = f'extended-eval-draft-v1-{index:03}'
        case['version'] = SCENARIO_VERSION
        case['seed'] += 100000
        case['initial_fire'] = [_remap(point, old_grid, new_grid) for point in case['initial_fire']]
        case['sector_grid'] = _remap(case['sector_grid'], old_grid, new_grid)
        for report in case.get('incoming_reports', []):
            if report.get('location') is not None:
                report['location'] = _remap(report['location'], old_grid, new_grid)
        initial = case['weather_schedule'][0]
        if initial['minute'] != 0:
            raise ValueError('Initial weather must be at minute zero')
        magnitude = min(3, math.hypot(*initial['wind']))
        case['wind'] = wind_from_degrees(direction, magnitude)
        initial['wind'] = case['wind'][:]
        case['parent_scenario_sha256'] = original['sha256']
        case['sha256'] = _digest({k: v for k, v in case.items() if k != 'sha256'})
        cases.append(case)
    output = {'version': VERSION, 'status': 'unqualified-not-runnable',
              'source_catalog_sha256': catalog['sha256'],
              'grid': {k: copy.deepcopy(new_grid[k]) for k in ('crs', 'transform', 'width', 'height')},
              'assumptions': {'initial_onshore_count': 10,
                              'wind_units': 'relative model units, not m/s',
                              'new_map_validation': 'pending', 'road_access_validation': 'pending',
                              'scenario_runtime_validation': 'pending'},
              'cases': cases}
    output['sha256'] = _digest(output)
    return output
