"""Reproducible exercise-only estimates from retained, incomplete land-cover evidence."""
import copy
import hashlib
import json
import math
import re

from .data import _check_hash, _crop, _mask, _matrix, _shape, digest


VERSION = 'incident-day-fuel-estimates-v1'
PREPARED_PIN = 'ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108'
DOMAIN_PIN = 'e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd'


def _ring(ring, point):
    inside = False
    boundary = False
    for index, first in enumerate(ring):
        previous = ring[index - 1]
        delta_x, delta_y = previous[0] - first[0], previous[1] - first[1]
        length = math.hypot(delta_x, delta_y)
        cross = (point[0] - first[0]) * delta_y - (point[1] - first[1]) * delta_x
        dot = (point[0] - first[0]) * delta_x + (point[1] - first[1]) * delta_y
        if length and abs(cross) / length < 1e-10 and 0 <= dot <= length * length:
            boundary = True
        if ((first[1] > point[1]) != (previous[1] > point[1]) and
                point[0] < delta_x * (point[1] - first[1]) / delta_y + first[0]):
            inside = not inside
    return inside, boundary


def _contains(feature, point):
    geometry = feature['geometry']
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    for polygon in polygons:
        outer, outer_boundary = _ring(polygon[0], point)
        holes = [_ring(ring, point) for ring in polygon[1:]]
        if outer_boundary or any(boundary for inside, boundary in holes):
            return 'boundary'
        if outer and not any(inside for inside, boundary in holes):
            return 'inside'
    return 'outside'


def _category(properties):
    code = str(properties.get('cod_os', ''))
    if code in ('3002', '2010', '2002'):
        return 'urban_mixed', 'built', []
    if code == '317':
        return 'vegetation', 'riparian', list(range(1, 11))
    if code.isdigit() and 500 <= int(code) < 600:
        return 'vegetation', 'woodland', [8, 9, 10]
    if code.isdigit() and 600 <= int(code) < 800:
        return 'vegetation', 'shrub', [4, 5, 6, 7]
    if code.isdigit() and 400 <= int(code) < 500:
        return 'vegetation', 'cultivation', [1, 2, 3]
    return 'unresolved', 'unmapped', list(range(1, 11))


def _fallback_class(category):
    return {'woodland': 8, 'cultivation': 1, 'shrub': 4, 'riparian': 4,
            'built': 1}.get(category, 4)


def _features(probe):
    if type(probe) is not dict or type(probe.get('cells')) is not list:
        raise ValueError('Invalid land-cover probe')
    features = {}
    for cell in probe['cells']:
        response = cell.get('response')
        if type(response) is not str or hashlib.sha256(response.encode()).hexdigest() != cell.get('response_extracted_sha256') or len(response.encode()) != cell.get('response_extracted_utf8_bytes'):
            raise ValueError('Land-cover response hash mismatch')
        annotation = re.search(r'Showing characters (\d+)-(\d+) of (\d+) total', response)
        if annotation and (int(annotation.group(1)) != 0 or annotation.group(2) != annotation.group(3)):
            raise ValueError('Truncated land-cover response')
        collection = json.loads(response.split('\n\n---\n')[0])
        items = collection.get('features')
        if collection.get('type') != 'FeatureCollection' or type(items) is not list or collection.get('numberMatched', len(items)) > len(items) or ('numberMatched' not in collection and len(items) >= 5):
            raise ValueError('Incomplete land-cover response')
        for feature in items:
            if feature.get('geometry', {}).get('type') not in ('Polygon', 'MultiPolygon'):
                raise ValueError('Unsupported land-cover geometry')
            features[digest(feature)] = feature
    return list(features.values())


def _segment_crosses(first, second, third, fourth):
    def orientation(point, other, target):
        return (other[0] - point[0]) * (target[1] - point[1]) - (other[1] - point[1]) * (target[0] - point[0])
    left, right = orientation(first, second, third), orientation(first, second, fourth)
    near, far = orientation(third, fourth, first), orientation(third, fourth, second)
    return left * right <= 0 and near * far <= 0 and max(min(first[0], second[0]), min(third[0], fourth[0])) <= min(max(first[0], second[0]), max(third[0], fourth[0])) and max(min(first[1], second[1]), min(third[1], fourth[1])) <= min(max(first[1], second[1]), max(third[1], fourth[1]))


def _blocked(first, second, barriers):
    for feature in barriers:
        geometry = feature['geometry']
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        if _contains(feature, first) != 'outside' or _contains(feature, second) != 'outside':
            return True
        for polygon in polygons:
            for ring in polygon:
                if any(_segment_crosses(first, second, ring[index - 1], point) for index, point in enumerate(ring)):
                    return True
    return False


def _vote(row, column, classes, known, grid, allowed, barriers, limit=8):
    centre = grid['centre_lon_lat'][row][column]
    candidates = sorted((math.hypot(other_row-row, other_column-column), other_row, other_column)
                        for other_row in range(len(classes)) for other_column in range(len(classes[0]))
                        if known[other_row][other_column] and classes[other_row][other_column] in allowed)
    neighbours = []
    for distance, other_row, other_column in candidates:
        if not distance or _blocked(centre, grid['centre_lon_lat'][other_row][other_column], barriers):
            continue
        neighbours.append({'row': other_row, 'column': other_column, 'class': classes[other_row][other_column],
                           'distance_metres': round(distance * grid['cell_km'] * 1000, 3),
                           'weight': round(1 / distance, 9)})
        if len(neighbours) == limit:
            break
    scores = {}
    for neighbour in neighbours:
        scores[neighbour['class']] = scores.get(neighbour['class'], 0) + neighbour['weight']
    alternatives = [{'class': code, 'weight': round(weight, 9)} for code, weight in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
    return neighbours, alternatives


def build_estimates(prepared, domain, landcover_probe):
    prepared_hash = _check_hash(prepared, 'prepared input')
    domain_hash = _check_hash(domain, 'domain input')
    if domain.get('prepared_inputs_sha256') != prepared_hash:
        raise ValueError('Domain parent pin mismatch')
    parent_height, parent_width = _shape(prepared['grid'])
    height, width = _shape(domain['grid'])
    crop = domain.get('crop')
    if type(crop) is not dict or set(crop) != {'top', 'left', 'height', 'width'} or any(type(value) is not int for value in crop.values()) or crop['top'] < 0 or crop['left'] < 0 or crop['height'] != height or crop['width'] != width or crop['top'] + height > parent_height or crop['left'] + width > parent_width:
        raise ValueError('Invalid crop')
    parent_grid, grid = prepared['grid'], domain['grid']
    transform = parent_grid['transform'][:]
    transform[2] += crop['left'] * transform[0] + crop['top'] * transform[1]
    transform[5] += crop['left'] * transform[3] + crop['top'] * transform[4]
    if grid['transform'] != transform or grid['crs'] != parent_grid['crs'] or grid['cell_km'] != parent_grid['cell_km'] or grid['centre_lon_lat'] != _crop(parent_grid['centre_lon_lat'], crop):
        raise ValueError('Grid alignment mismatch')
    classes = _crop(_matrix(prepared['fuel_classes'], parent_height, parent_width, 'source classes'), crop)
    known = _crop(_mask(prepared['fuel_valid_mask'], parent_height, parent_width, 'source known'), crop)
    study = _crop(_mask(prepared['study_mask'], parent_height, parent_width, 'study'), crop)
    if any(not value for line in study for value in line):
        raise ValueError('Study coverage incomplete')
    if any(type(value) is not int or value not in range(11) or bool(value) != bool(known[row][column]) for row, line in enumerate(classes) for column, value in enumerate(line)):
        raise ValueError('Source classes and known mask disagree')
    expected = {(row, column) for row in range(height) for column in range(width) if not known[row][column]}
    locations = {(cell['row'], cell['column']): (cell['longitude'], cell['latitude']) for cell in landcover_probe['cells']}
    if len(locations) != len(landcover_probe['cells']) or set(locations) != expected or any(list(point) != grid['centre_lon_lat'][row][column] for (row, column), point in locations.items()):
        raise ValueError('Land-cover probe coordinates do not match unknown cells')
    features = _features(landcover_probe)
    barriers = [feature for feature in features if _category(feature['properties'])[0] == 'urban_mixed']
    estimated = copy.deepcopy(classes)
    cells = []
    for row, column in sorted(expected):
        point = grid['centre_lon_lat'][row][column]
        matches = [(feature, _contains(feature, point)) for feature in features]
        matches = [(feature, status) for feature, status in matches if status != 'outside']
        if len(matches) == 1 and matches[0][1] == 'inside':
            properties = matches[0][0]['properties']
            land_kind, category, allowed = _category(properties)
        else:
            properties = None
            land_kind, category, allowed = 'unresolved', 'uncovered_or_ambiguous', list(range(1, 11))
        neighbours, alternatives = _vote(row, column, classes, known, grid, allowed, barriers) if allowed else ([], [])
        chosen = alternatives[0]['class'] if alternatives else _fallback_class(category)
        flags = ['exercise_assumption', 'point_not_cell_coverage']
        if land_kind == 'unresolved':
            flags.append('land_and_water_unresolved')
        if land_kind == 'urban_mixed':
            flags.append('garden_fuel_assumption_no_vegetation_borrow')
        if not alternatives:
            flags.append('fallback_no_compatible_support')
        if alternatives and len(alternatives) > 1 and alternatives[0]['weight'] == alternatives[1]['weight']:
            flags.append('tie_lower_class_id')
        total = sum(item['weight'] for item in alternatives)
        terrain = 'built' if land_kind == 'urban_mixed' else ('field' if chosen <= 3 else 'scrub' if chosen <= 7 else 'woodland')
        cells.append({'row': row, 'column': column, 'fuel_class': chosen, 'land_kind': land_kind,
                      'land_category': category, 'centre_feature': properties, 'confidence': 'low',
                      'vote_share': round(alternatives[0]['weight'] / total, 6) if total else None,
                      'support_neighbours': neighbours, 'alternatives': alternatives, 'inference_flags': flags,
                      'assumed_built_terrain': 'urban' if land_kind == 'urban_mixed' else None,
                      'runtime_terrain': terrain,
                      'fuel_multiplier': 0.35 if land_kind == 'urban_mixed' else 1.0})
        estimated[row][column] = chosen
    document = {'version': VERSION, 'source_pins': {'prepared_sha256': prepared_hash, 'domain_sha256': domain_hash,
                'landcover_probe_canonical_sha256': digest(landcover_probe), 'grid_sha256': digest(grid),
                'source_classes_sha256': digest(classes), 'source_known_sha256': digest(known)},
                'method': 'nearest 8 compatible known classes, inverse cell-distance categorical vote; ties choose lowest class; cached built polygons block support segments; fallback woodland 8, cultivation 1, shrub/riparian/unresolved 4, built 1',
                'status': 'unapproved_exercise_assumptions', 'fuel_classes': estimated,
                'source_known_mask': copy.deepcopy(known), 'cells': cells}
    document['sha256'] = digest(document)
    validate_estimates(document, classes, known, grid)
    return document


def validate_estimates(document, source_classes, source_known, grid):
    _check_hash(document, 'fuel estimation')
    if set(document) != {'version', 'source_pins', 'method', 'status', 'fuel_classes', 'source_known_mask', 'cells', 'sha256'} or document['version'] != VERSION or document['status'] != 'unapproved_exercise_assumptions':
        raise ValueError('Invalid estimation document schema')
    height, width = _shape(grid)
    _matrix(source_classes, height, width, 'source classes')
    _mask(source_known, height, width, 'source known')
    _matrix(document['fuel_classes'], height, width, 'estimated classes')
    if any(type(value) is not int or value not in range(11) or bool(value) != bool(source_known[row][column]) for row, line in enumerate(source_classes) for column, value in enumerate(line)):
        raise ValueError('Invalid source class and mask')
    pins = document['source_pins']
    if type(pins) is not dict or set(pins) != {'prepared_sha256', 'domain_sha256', 'landcover_probe_canonical_sha256', 'grid_sha256', 'source_classes_sha256', 'source_known_sha256'} or any(type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value) for value in pins.values()) or pins['grid_sha256'] != digest(grid) or pins['source_classes_sha256'] != digest(source_classes) or pins['source_known_sha256'] != digest(source_known) or document['source_known_mask'] != source_known:
        raise ValueError('Estimation source pin mismatch')
    required = {'row', 'column', 'fuel_class', 'land_kind', 'land_category', 'centre_feature', 'confidence', 'vote_share', 'support_neighbours', 'alternatives', 'inference_flags', 'assumed_built_terrain', 'runtime_terrain', 'fuel_multiplier'}
    expected = {(row, column) for row in range(height) for column in range(width) if not source_known[row][column]}
    seen = set()
    if type(document['cells']) is not list or type(document['method']) is not str:
        raise ValueError('Invalid document fields')
    for cell in document['cells']:
        if type(cell) is not dict or set(cell) != required or type(cell['row']) is not int or type(cell['column']) is not int:
            raise ValueError('Invalid estimate cell schema')
        position = cell['row'], cell['column']
        if position not in expected or position in seen or type(cell['fuel_class']) is not int or cell['fuel_class'] not in range(1, 11) or cell['land_kind'] not in ('vegetation', 'urban_mixed', 'water', 'unresolved') or cell['confidence'] != 'low' or document['fuel_classes'][position[0]][position[1]] != cell['fuel_class']:
            raise ValueError('Invalid estimated cell')
        if type(cell['support_neighbours']) is not list or type(cell['alternatives']) is not list or type(cell['inference_flags']) is not list or any(type(flag) is not str for flag in cell['inference_flags']):
            raise ValueError('Invalid support schema')
        if len(cell['support_neighbours']) > 8:
            raise ValueError('Too many support neighbours')
        support_positions = set()
        support_order = []
        for neighbour in cell['support_neighbours']:
            if type(neighbour) is not dict or set(neighbour) != {'row', 'column', 'class', 'distance_metres', 'weight'} or type(neighbour['row']) is not int or type(neighbour['column']) is not int or not (0 <= neighbour['row'] < height and 0 <= neighbour['column'] < width) or not source_known[neighbour['row']][neighbour['column']] or source_classes[neighbour['row']][neighbour['column']] != neighbour['class'] or type(neighbour['weight']) not in (int, float) or not math.isfinite(neighbour['weight']) or neighbour['weight'] <= 0 or type(neighbour['distance_metres']) not in (int, float) or not math.isfinite(neighbour['distance_metres']) or neighbour['distance_metres'] <= 0:
                raise ValueError('Invalid support neighbour')
            support_position = neighbour['row'], neighbour['column']
            distance = math.hypot(neighbour['row'] - cell['row'], neighbour['column'] - cell['column'])
            if not distance or support_position in support_positions or neighbour['distance_metres'] != round(distance * grid['cell_km'] * 1000, 3) or neighbour['weight'] != round(1 / distance, 9):
                raise ValueError('Inconsistent support distance or weight')
            support_positions.add(support_position)
            support_order.append((distance, *support_position))
        if support_order != sorted(support_order):
            raise ValueError('Support neighbours out of order')
        if any(type(item) is not dict or set(item) != {'class', 'weight'} or type(item['class']) is not int or item['class'] not in range(1, 11) or type(item['weight']) not in (int, float) or not math.isfinite(item['weight']) or item['weight'] <= 0 for item in cell['alternatives']):
            raise ValueError('Invalid alternatives')
        if type(cell['land_category']) is not str or (cell['centre_feature'] is not None and type(cell['centre_feature']) is not dict) or type(cell['fuel_multiplier']) not in (int, float) or not math.isfinite(cell['fuel_multiplier']) or not 0 <= cell['fuel_multiplier'] <= 1 or cell['runtime_terrain'] not in ('field', 'scrub', 'woodland', 'built', 'bare'):
            raise ValueError('Invalid land assumptions')
        if cell['centre_feature'] is None:
            if (cell['land_kind'], cell['land_category']) != ('unresolved', 'uncovered_or_ambiguous'):
                raise ValueError('Missing land-cover evidence')
            allowed = list(range(1, 11))
        else:
            expected_kind, expected_category, allowed = _category(cell['centre_feature'])
            if (cell['land_kind'], cell['land_category']) != (expected_kind, expected_category):
                raise ValueError('Land-cover category mismatch')
        if any(neighbour['class'] not in allowed for neighbour in cell['support_neighbours']):
            raise ValueError('Incompatible support class')
        if cell['land_kind'] != 'urban_mixed' and cell['fuel_class'] not in allowed:
            raise ValueError('Incompatible estimated class')
        if cell['land_kind'] != 'water' and cell['fuel_multiplier'] <= 0:
            raise ValueError('Non-water fuel multiplier must be positive')
        if cell['land_kind'] == 'water':
            raise ValueError('Water classification is not supported by this evidence schema')
        expected_terrain = 'built' if cell['land_kind'] == 'urban_mixed' else ('field' if cell['fuel_class'] <= 3 else 'scrub' if cell['fuel_class'] <= 7 else 'woodland')
        if cell['runtime_terrain'] != expected_terrain or (cell['land_kind'] != 'urban_mixed' and (cell['assumed_built_terrain'] is not None or cell['fuel_multiplier'] != 1.0)):
            raise ValueError('Inconsistent terrain assumption')
        scores = {}
        for neighbour in cell['support_neighbours']:
            scores[neighbour['class']] = scores.get(neighbour['class'], 0) + neighbour['weight']
        expected_alternatives = [{'class': code, 'weight': round(weight, 9)} for code, weight in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
        total = sum(item['weight'] for item in expected_alternatives)
        expected_share = round(expected_alternatives[0]['weight'] / total, 6) if total else None
        if cell['alternatives'] != expected_alternatives or cell['vote_share'] != expected_share or (expected_alternatives and cell['fuel_class'] != expected_alternatives[0]['class']):
            raise ValueError('Inconsistent categorical vote')
        if not expected_alternatives and cell['fuel_class'] != _fallback_class(cell['land_category']):
            raise ValueError('Inconsistent fallback class')
        if 'exercise_assumption' not in cell['inference_flags'] or 'point_not_cell_coverage' not in cell['inference_flags'] or (cell['land_kind'] == 'unresolved' and 'land_and_water_unresolved' not in cell['inference_flags']):
            raise ValueError('Missing inference flags')
        if cell['land_kind'] == 'urban_mixed' and (cell['assumed_built_terrain'] != 'urban' or cell['fuel_multiplier'] != 0.35 or cell['support_neighbours']):
            raise ValueError('Invalid urban assumption')
        seen.add(position)
    if seen != expected or any(source_known[row][column] and document['fuel_classes'][row][column] != source_classes[row][column] for row in range(height) for column in range(width)):
        raise ValueError('Estimate coverage or known classes changed')
    return document
