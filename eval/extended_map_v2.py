#!/usr/bin/env python3
"""Compile corrected protected-area-referenced fuel extent, not a massif boundary."""
import argparse
from pathlib import Path

from extended_map_common import (PARENT_FILE_SHA256, PARENT_DOCUMENT_SHA256, PARENT_NETWORK_SHA256,
    FUEL_ARCHIVE_SHA256, FUEL_MEMBER_SHA256, FUEL_MEMBER, digest, file_sha256,
    load_parent, read_json, sample_fuel, serialize, write_private_json)
from extended_domain_v1 import domain_from_bounds

BOUNDARY_PIN = '0b35e4f6ec7ab198f51cd66705a090a496d5b98f9203c825b499a9f8ebbb2cbd'
BOUNDARY_LAYER = 'n2000_spatial_es_pibal_proy_end25.shp'
SITE_CODES = ('ES6170004', 'ES6170010')
BOUNDS = [290000, 4035000, 332000, 4064500]
WIDTH, HEIGHT, ROW, COL = 84, 59, 20, 14


def inspect_boundaries(archive):
    import pyogrio
    if file_sha256(archive) != BOUNDARY_PIN:
        raise ValueError('boundary archive hash mismatch')
    path = f'/vsizip/{Path(archive).resolve()}/{BOUNDARY_LAYER}'
    frame = pyogrio.read_dataframe(path, where="site_code IN ('ES6170004','ES6170010')")
    if (len(frame) != 2 or sorted(frame.site_code.tolist()) != list(SITE_CODES)
        or frame.crs.to_epsg() != 25830 or not frame.geometry.is_valid.all()
        or frame.geometry.isna().any() or frame.geometry.is_empty.any()):
        raise ValueError('unexpected protected-area features')
    bounds = [float(x) for x in frame.total_bounds]
    # Minimum 5 km beyond reference in west, east and north, and encompass
    # both the polygon and original southern edge; align outward to 500 m.
    target = domain_from_bounds(bounds)['bounds']
    if target != BOUNDS:
        raise ValueError('boundary-derived extent differs from pinned grid')
    return {'archive_sha256': BOUNDARY_PIN, 'layer': BOUNDARY_LAYER,
            'site_codes': list(SITE_CODES), 'reference_bounds': bounds,
            'reference_type': 'Natura 2000 protected-area boundaries; not the complete physical massif',
            'extent_rule': '5 km north/east/west of reference, south contains reference and parent, outward 500 m'}


def compile_map(parent, samples, reference):
    if parent.get('sha256') != PARENT_DOCUMENT_SHA256 or digest({k:v for k,v in parent.items() if k != 'sha256'}) != PARENT_DOCUMENT_SHA256:
        raise ValueError('parent document hash mismatch')
    if len(samples) != HEIGHT or any(len(row) != WIDTH for row in samples):
        raise ValueError('invalid sample dimensions')
    elevations = [[None] * WIDTH for _ in range(HEIGHT)]
    fuels = [[None] * WIDTH for _ in range(HEIGHT)]
    source = parent['provenance']
    unknown_recovered = []
    parent_agree = fringe_known = 0
    for r in range(HEIGHT):
        for c in range(WIDTH):
            sample = samples[r][c]
            if sample is not None and (type(sample) is not int or sample not in range(1,11)):
                raise ValueError('invalid source fuel sample')
            if ROW <= r < ROW + 36 and COL <= c < COL + 37:
                pr, pc = r - ROW, c - COL
                elevations[r][c] = parent['heights'][pr][pc]
                if source['source_fuel_known_mask'][pr][pc]:
                    original = source['source_fuel_classes'][pr][pc]
                    if original != sample:
                        raise ValueError(f'parent known fuel mismatch at {pr},{pc}')
                    fuels[r][c] = original
                    parent_agree += 1
                elif sample is not None:
                    unknown_recovered.append({'parent_row': pr, 'parent_column': pc, 'sample': sample})
            elif sample is not None:
                fuels[r][c] = sample
                fringe_known += 1
    surface = [['unknown'] * WIDTH for _ in range(HEIGHT)]
    doc = {'version': 'extended-map-v2', 'status': 'incomplete_not_simulator_ready',
           'grid': {'crs': 'EPSG:25830', 'width': WIDTH, 'height': HEIGHT, 'cell_metres': 500,
                    'transform': [500, 0, BOUNDS[0], 0, -500, BOUNDS[3]], 'bounds': BOUNDS,
                    'parent_offset': {'row': ROW, 'column': COL}},
           'reference': reference,
           'source': {'parent_file_sha256': PARENT_FILE_SHA256, 'parent_document_sha256': PARENT_DOCUMENT_SHA256,
                      'parent_network_sha256': PARENT_NETWORK_SHA256, 'fuel_archive_sha256': FUEL_ARCHIVE_SHA256,
                      'fuel_member': FUEL_MEMBER, 'fuel_member_sha256': FUEL_MEMBER_SHA256,
                      'fuel_sampling': 'centre projected to EPSG:3035; containing 250 m pixel; zero/NoData unknown',
                      'fuel_attribution': 'European Forest Fire Information System, 2017, European Fuel Map, European Commission'},
           'elevation': elevations, 'source_fuel_class': fuels, 'surface': surface,
           'overlap': {'known_samples_agree': parent_agree, 'known_mismatches': 0,
                       'parent_unknown_with_archive_value': unknown_recovered, 'policy': 'retain parent unknowns'},
           'coverage': {'cells': WIDTH * HEIGHT, 'parent_cells': 1332, 'elevation_values': 1332,
                        'fringe_cells': WIDTH * HEIGHT - 1332, 'fringe_fuel_values': fringe_known,
                        'fringe_fuel_unknown': WIDTH * HEIGHT - 1332 - fringe_known,
                        'source_fuel_values': parent_agree + fringe_known,
                        'surface_unknown': WIDTH * HEIGHT, 'verified_land': 0, 'verified_sea': 0}}
    doc['sha256'] = digest(doc)
    return doc


def validate_map(value, parent, samples, reference):
    if serialize(value) != serialize(compile_map(parent, samples, reference)):
        raise ValueError('map differs from source reconstruction')
    return value['coverage']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['compile', 'validate'])
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--fuel-archive', type=Path, required=True)
    parser.add_argument('--boundary-archive', type=Path, required=True)
    parser.add_argument('--map', type=Path, required=True)
    args = parser.parse_args()
    parent = load_parent(args.parent)
    reference = inspect_boundaries(args.boundary_archive)
    samples = sample_fuel(args.fuel_archive, parent, width=WIDTH, height=HEIGHT,
                          west=BOUNDS[0], north=BOUNDS[3], parent_row=ROW, parent_col=COL)
    result = compile_map(parent, samples, reference)
    if args.command == 'compile':
        write_private_json(args.map, result)
    else:
        validate_map(read_json(args.map), parent, samples, reference)
    print({'sha256': result['sha256'], 'coverage': result['coverage'], 'overlap': result['overlap']})


if __name__ == '__main__':
    main()
