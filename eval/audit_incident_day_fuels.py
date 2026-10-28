#!/usr/bin/env python3
"""Reproduce the fuel-gap ledger from pinned local input documents and rasters."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.fuel_audit import build_ledger

PREPARED_PIN = 'ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108'
DOMAIN_PIN = 'e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd'
RASTERS = {
    'native': ('malaga-missing-data-v1/malaga-terrain-fuel-search/effis-fuel-aoi-native.tif', '4cc9b17c7f738af14d1f12302b4e8c9f958608023c600d8668864954d95cd308'),
    'classes': ('malaga-data-provenance-v1/malaga-landscape-prepared-v1/fuel_classes.tif', '035f7a3ac07db866dc34b4c55778891055d4b03a790268dda77362c1e1ed400c'),
    'valid': ('malaga-data-provenance-v1/malaga-landscape-prepared-v1/fuel_valid_mask.tif', '44368e111a336e03211f754774fcb1b65396803aab0d85f772da7a8a7a8fef77'),
    'coverage': ('malaga-data-provenance-v1/malaga-landscape-prepared-v1/fuel_source_coverage_mask.tif', 'ff67e5d38a9f3765fd1fa1b77e82a1b27d8fda11da308ecb7ec3d62d5e903dd9'),
}


def ring_contains(ring, point):
    inside = False
    boundary = False
    for index, first in enumerate(ring):
        previous = ring[index - 1]
        delta_x, delta_y = previous[0] - first[0], previous[1] - first[1]
        squared_length = delta_x * delta_x + delta_y * delta_y
        cross = (point[0] - first[0]) * delta_y - (point[1] - first[1]) * delta_x
        dot = (point[0] - first[0]) * delta_x + (point[1] - first[1]) * delta_y
        if squared_length and abs(cross) / squared_length ** .5 < 1e-10 and 0 <= dot <= squared_length:
            boundary = True
        if ((first[1] > point[1]) != (previous[1] > point[1]) and
                point[0] < (previous[0] - first[0]) * (point[1] - first[1]) /
                (previous[1] - first[1]) + first[0]):
            inside = not inside
    return inside, boundary


def polygon_contains(polygon, point):
    outer_inside, outer_boundary = ring_contains(polygon[0], point)
    holes = [ring_contains(ring, point) for ring in polygon[1:]]
    return outer_inside and not any(inside for inside, boundary in holes), (
        outer_boundary or any(boundary for inside, boundary in holes))


def classify_siose_response(response, point):
    try:
        annotation = re.search(r'Showing characters (\d+)-(\d+) of (\d+) total', response)
        if annotation and (int(annotation.group(1)) != 0 or int(annotation.group(2)) != int(annotation.group(3))):
            return 'incomplete', None
        document = json.loads(response.split('\n\n---\n')[0])
        features = document['features']
        if document.get('type') != 'FeatureCollection' or type(features) is not list:
            raise ValueError('Invalid feature collection')
        if document.get('numberMatched', len(features)) > len(features) or (
                'numberMatched' not in document and len(features) >= 5):
            return 'incomplete', None
        matches = []
        for feature in features:
            geometry = feature['geometry']
            if geometry['type'] == 'Polygon':
                polygons = [geometry['coordinates']]
            elif geometry['type'] == 'MultiPolygon':
                polygons = geometry['coordinates']
            else:
                return 'incomplete', None
            checks = [polygon_contains(polygon, point) for polygon in polygons]
            if any(inside or boundary for inside, boundary in checks):
                matches.append((feature['properties'], any(boundary for inside, boundary in checks)))
        if len(matches) == 1 and not matches[0][1]:
            return 'point_inside', matches[0][0]
        if matches:
            return 'ambiguous_boundary_or_overlap', None
        return ('no_feature_returned' if not features else 'no_containing_feature'), None
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        return 'incomplete', None


def verify_siose_probe(path, ledger_path):
    document = json.loads(Path(path).read_text())
    ledger = json.loads(Path(ledger_path).read_text())
    locations = {(cell['row'], cell['column']): (cell['longitude'], cell['latitude'])
                 for cell in ledger['cells']}
    seen = set()
    counts = {name: 0 for name in ('point_inside', 'ambiguous_boundary_or_overlap',
                                     'no_feature_returned', 'no_containing_feature', 'incomplete')}
    for cell in document['cells']:
        identifier = (cell['row'], cell['column'])
        if identifier in seen or locations.get(identifier) != (cell['longitude'], cell['latitude']):
            raise ValueError('SIOSE probe does not match the source ledger')
        seen.add(identifier)
        response = cell.get('response')
        if type(response) is not str:
            raise ValueError('Missing captured connector response')
        encoded_response = response.encode('utf-8')
        if (cell.get('response_extracted_utf8_bytes') != len(encoded_response) or
                cell.get('response_extracted_sha256') != hashlib.sha256(encoded_response).hexdigest()):
            raise ValueError('Captured connector response hash or byte count mismatch')
        status, feature = classify_siose_response(response, locations[identifier])
        if status != cell['probe_status'] or feature != cell.get('point_feature'):
            raise ValueError('SIOSE point classification does not match retained response')
        counts[status] += 1
    if seen != set(locations) or counts != document['counts']:
        raise ValueError('SIOSE probe coverage/count mismatch')
    return counts


def run(runtime):
    import rasterio
    from rasterio.warp import transform

    runtime = Path(runtime)
    prepared = json.loads((runtime / 'malaga-prepared-simulation-v1/malaga-prepared-inputs-v1/inputs.json').read_text())
    domain = json.loads((runtime / 'topographic-experiment-v1/topographic-inputs-v1/inputs.json').read_text())
    paths = {}
    for name, (relative, pin) in RASTERS.items():
        path = runtime / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != pin:
            raise ValueError(f'{name} raster hash mismatch')
        paths[name] = path
    from envs.wildfire_response.incident_day.data import _check_hash
    if _check_hash(prepared, 'prepared input') != PREPARED_PIN or _check_hash(domain, 'topographic input') != DOMAIN_PIN:
        raise ValueError('Document pin mismatch')
    crop, grid = domain['crop'], domain['grid']
    indices = [(row, column) for row in range(grid['height']) for column in range(grid['width'])
               if prepared['study_mask'][row + crop['top']][column + crop['left']]
               and not prepared['fuel_valid_mask'][row + crop['top']][column + crop['left']]]
    observations = {}
    with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO', GDAL_NUM_THREADS='1'):
        with rasterio.open(paths['native']) as native, rasterio.open(paths['classes']) as classes, \
             rasterio.open(paths['valid']) as valid, rasterio.open(paths['coverage']) as coverage:
            if str(native.crs) != 'EPSG:3035' or native.nodata != 0:
                raise ValueError('Unexpected native raster CRS or NoData')
            for dataset in (classes, valid, coverage):
                if (str(dataset.crs) != prepared['grid']['crs'] or
                        list(dataset.transform)[:6] != prepared['grid']['transform'] or
                        dataset.width != prepared['grid']['width'] or dataset.height != prepared['grid']['height']):
                    raise ValueError('Prepared raster alignment mismatch')
            class_data, valid_data, coverage_data = (dataset.read(1) for dataset in (classes, valid, coverage))
            coords = [grid['centre_lon_lat'][row][column] for row, column in indices]
            xs, ys = transform('EPSG:4326', native.crs, [point[0] for point in coords],
                               [point[1] for point in coords])
            source = native.read(1, masked=True)
            for (row, column), native_x, native_y in zip(indices, xs, ys):
                parent_row, parent_column = row + crop['top'], column + crop['left']
                if class_data[parent_row, parent_column] != 0 or valid_data[parent_row, parent_column] != 0:
                    raise ValueError('Unknown pack cell disagrees with prepared raster')
                native_row, native_column = native.index(native_x, native_y)
                inside = 0 <= native_row < native.height and 0 <= native_column < native.width
                if bool(coverage_data[parent_row, parent_column]) != inside:
                    raise ValueError('Native footprint and prepared coverage disagree')
                status = 'outside' if not inside else ('masked' if bool(source.mask[native_row, native_column]) else 'valid')
                value = int(source.data[native_row, native_column]) if status == 'valid' else None
                observations[(row, column)] = {'native_xy': [round(native_x, 6), round(native_y, 6)],
                    'native_pixel': [native_row, native_column] if inside else None,
                    'native_status': status, 'native_value': value}
    ledger = build_ledger(prepared, domain, observations, prepared_pin=PREPARED_PIN, domain_pin=DOMAIN_PIN)
    ledger['raster_sha256'] = {name: details[1] for name, details in RASTERS.items()}
    ledger['sampling'] = 'native nearest pixel at stored WGS84 grid centre; masked and outside remain unknown'
    return ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--runtime', type=Path, help='read-only root containing the pinned source packs')
    modes.add_argument('--verify-siose-probe', type=Path, help='verify retained connector responses')
    args = parser.parse_args()
    if args.verify_siose_probe:
        ledger_path = ROOT / 'data/incident_day_v2/fuel_review/unknown_cells.json'
        result = verify_siose_probe(args.verify_siose_probe, ledger_path)
    else:
        result = run(args.runtime)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
