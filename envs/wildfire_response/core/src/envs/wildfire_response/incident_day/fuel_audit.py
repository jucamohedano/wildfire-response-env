"""Build an evidence ledger without assigning fuel to missing source cells.

The caller supplies native raster observations; this module has no geodata
dependencies and never treats a replacement candidate as an approval.
"""
import math

from .data import _check_hash, _shape, _matrix, _mask


def build_ledger(prepared, domain, observations, *, prepared_pin, domain_pin):
    """Audit each unknown crop cell; observations are keyed by (row, column)."""
    if _check_hash(prepared, 'prepared input') != prepared_pin:
        raise ValueError('Prepared input pin mismatch')
    if _check_hash(domain, 'topographic input') != domain_pin:
        raise ValueError('Topographic input pin mismatch')
    if domain.get('prepared_inputs_sha256') != prepared_pin:
        raise ValueError('Topographic parent pin mismatch')
    parent_height, parent_width = _shape(prepared.get('grid'))
    height, width = _shape(domain.get('grid'))
    crop = domain.get('crop')
    if (type(crop) is not dict or set(crop) != {'top', 'left', 'height', 'width'}
            or any(type(item) is not int for item in crop.values()) or crop['top'] < 0
            or crop['left'] < 0 or crop['height'] != height or crop['width'] != width
            or crop['top'] + height > parent_height or crop['left'] + width > parent_width):
        raise ValueError('Invalid crop')
    parent_grid, grid = prepared['grid'], domain['grid']
    transform = parent_grid.get('transform')
    if (type(transform) is not list or len(transform) != 6 or
            any(type(item) not in (int, float) or not math.isfinite(item) for item in transform)):
        raise ValueError('Invalid transform')
    expected = transform[:]
    expected[2] += crop['left'] * transform[0] + crop['top'] * transform[1]
    expected[5] += crop['left'] * transform[3] + crop['top'] * transform[4]
    if (grid.get('transform') != expected or grid.get('crs') != parent_grid.get('crs')
            or grid.get('cell_km') != parent_grid.get('cell_km')):
        raise ValueError('Grid alignment mismatch')
    source = _matrix(prepared.get('fuel_classes'), parent_height, parent_width, 'fuel classes')
    known = _mask(prepared.get('fuel_valid_mask'), parent_height, parent_width, 'fuel validity')
    study = _mask(prepared.get('study_mask'), parent_height, parent_width, 'study mask')
    centres = _matrix(parent_grid.get('centre_lon_lat'), parent_height, parent_width, 'WGS84 centres')
    crop_centres = _matrix(grid.get('centre_lon_lat'), height, width, 'crop WGS84 centres')
    cells = []
    for row in range(height):
        for column in range(width):
            parent_row, parent_column = row + crop['top'], column + crop['left']
            fuel_class, valid = source[parent_row][parent_column], known[parent_row][parent_column]
            if type(fuel_class) is not int or fuel_class not in range(11) or bool(fuel_class) != bool(valid):
                raise ValueError('Source class and validity disagree')
            if crop_centres[row][column] != centres[parent_row][parent_column]:
                raise ValueError('Crop centres differ from parent')
            lonlat = centres[parent_row][parent_column]
            if (type(lonlat) is not list or len(lonlat) != 2 or
                    any(type(item) not in (int, float) or not math.isfinite(item) for item in lonlat)):
                raise ValueError('Invalid WGS84 centre')
            if not study[parent_row][parent_column] or valid:
                continue
            observation = observations.get((row, column))
            if type(observation) is not dict or observation.get('native_status') not in ('masked', 'outside', 'valid'):
                raise ValueError('Missing or invalid native observation')
            if (type(observation.get('native_xy')) not in (tuple, list) or len(observation['native_xy']) != 2
                    or any(type(item) not in (float, int) or not math.isfinite(item) for item in observation['native_xy'])):
                raise ValueError('Invalid native coordinate')
            pixel, value = observation.get('native_pixel'), observation.get('native_value')
            if observation['native_status'] == 'outside':
                if pixel is not None or value is not None:
                    raise ValueError('Outside observation cannot contain a pixel or value')
            elif (type(pixel) is not list or len(pixel) != 2 or
                  any(type(item) is not int or item < 0 for item in pixel)):
                raise ValueError('Invalid native pixel')
            elif observation['native_status'] == 'masked' and value is not None:
                raise ValueError('Masked observation cannot contain a fuel value')
            elif observation['native_status'] == 'valid' and (type(value) is not int or value <= 0):
                raise ValueError('Valid observation requires a positive source code')
            status = 'source_conflict_requires_review' if observation['native_status'] == 'valid' else 'unresolved'
            cells.append(dict(row=row, column=column, parent_row=parent_row, parent_column=parent_column,
                grid_xy=[transform[0]*(parent_column+.5)+transform[1]*(parent_row+.5)+transform[2],
                         transform[3]*(parent_column+.5)+transform[4]*(parent_row+.5)+transform[5]],
                longitude=lonlat[0], latitude=lonlat[1], native_xy=list(observation['native_xy']),
                native_pixel=observation.get('native_pixel'), native_status=observation['native_status'],
                native_value=observation.get('native_value'), source_known=False, source_class=None,
                evidence_status=status, replacement_evidence=None))
    if set(observations) != {(cell['row'], cell['column']) for cell in cells}:
        raise ValueError('Observations must match unknown study cells exactly')
    return {'version': 'incident-day-fuel-evidence-v1', 'approval_status': 'blocked',
            'prepared_sha256': prepared_pin, 'domain_sha256': domain_pin,
            'grid_crs': grid['crs'], 'native_crs': 'EPSG:3035', 'wgs84_crs': 'EPSG:4326',
            'coordinate_convention': 'cell centres; longitude then latitude; zero-based rows and columns',
            'counts': {'unknown': len(cells), 'masked': sum(cell['native_status'] == 'masked' for cell in cells),
                       'outside': sum(cell['native_status'] == 'outside' for cell in cells),
                       'valid': sum(cell['native_status'] == 'valid' for cell in cells)}, 'cells': cells}
