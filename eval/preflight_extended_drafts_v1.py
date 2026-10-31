"""Conservative initial-cell check; this does not run or approve a scenario."""
from eval.extended_scenario_drafts_v1 import _digest

VERSION = 'wildfire-extended-draft-preflight-v1'


def screen(cases, resolved, *, expected_map_sha256, allow_estimates=False):
    """Check initial fire and its complete eight-neighbour spread stencil.

    The caller supplies an independently trusted source pin. This check cannot
    approve data, routes, warm-up, later fire behavior or 24-hour outcomes.
    """
    if type(allow_estimates) is not bool:
        raise ValueError('Estimate permission must be explicit')
    if (type(resolved) is not dict or type(expected_map_sha256) is not str
            or resolved.get('sha256') != expected_map_sha256
            or _digest({k: v for k, v in resolved.items() if k != 'sha256'}) != expected_map_sha256
            or resolved.get('version') != 'extended-map-resolution-evidence-v1'):
        raise ValueError('Resolved evidence differs from the trusted pin')
    grid = resolved['grid']
    w, h = grid['width'], grid['height']
    if type(w) is not int or type(h) is not int or w <= 0 or h <= 0:
        raise ValueError('Invalid grid dimensions')
    cells, elevations = resolved['cells'], resolved['terrain_candidate']
    if any(type(matrix) is not list or len(matrix) != h
           or any(type(row) is not list or len(row) != w for row in matrix)
           for matrix in (cells, elevations)):
        raise ValueError('Evidence matrix dimensions differ')
    if (type(cases) is not list or len(cases) != 12
            or any(type(case) is not dict or type(case.get('id')) is not str for case in cases)
            or len({case['id'] for case in cases}) != 12):
        raise ValueError('Exactly twelve unique draft cases are required')
    rows = []
    for case in cases:
        fires = case.get('initial_fire')
        if (type(fires) is not list or not fires or any(type(p) is not list or len(p) != 2
                or any(type(v) is not int for v in p) or not 0 <= p[0] < w or not 0 <= p[1] < h
                for p in fires) or len({tuple(p) for p in fires}) != len(fires)):
            raise ValueError('Invalid initial-fire cell set')
        reasons = set()
        visited = {(x + dx, y + dy) for x, y in fires for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        fire_set = {tuple(p) for p in fires}
        for x, y in visited:
            if not (0 <= x < w and 0 <= y < h):
                reasons.add('domain_boundary'); continue
            cell = cells[y][x]
            if type(cell) is not dict or cell.get('surface') not in ('land', 'sea', 'unknown', 'mixed'):
                raise ValueError('Invalid surface evidence')
            surface = cell['surface']
            if surface in ('unknown', 'mixed'):
                reasons.add('unknown_surface'); continue
            if surface == 'sea':
                if (x, y) in fire_set:
                    reasons.add('ignition_on_sea')
                continue
            status = cell.get('fuel_status')
            if status not in ('source', 'estimated', 'unknown'):
                raise ValueError('Invalid fuel evidence status')
            if status == 'unknown' or status == 'estimated' and not allow_estimates:
                reasons.add('unsupported_fuel')
            if elevations[y][x] is None:
                reasons.add('unsupported_elevation')
        rows.append({'id': case['id'], 'status': 'blocked' if reasons else 'initial_check_passed',
                     'reasons': sorted(reasons)})
    return {'version': VERSION, 'status': 'initial_check_only_not_qualified',
            'map_sha256': expected_map_sha256, 'allow_estimates': allow_estimates, 'cases': rows}
